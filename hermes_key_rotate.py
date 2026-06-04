#!/Users/tesla/miniconda3/bin/python3
"""
Hermes Key Rotation — Main CLI Entry Point

Rotates, validates, stores, renders, and audits AI provider credentials
backed by a self-hosted Vaultwarden/Bitwarden vault.

Usage:
    hermes-key-rotate --doctor-secrets
    hermes-key-rotate --secret-backend vaultwarden --healthcheck
    hermes-key-rotate --secret-backend vaultwarden --unlock
    hermes-key-rotate --secret-backend vaultwarden --lock
    hermes-key-rotate --secret-backend vaultwarden --sync
    hermes-key-rotate --secret-backend vaultwarden --list-refs
    hermes-key-rotate --secret-backend vaultwarden --render-env
    hermes-key-rotate --status
    hermes-key-rotate --dry-run
    hermes-key-rotate --provider deepseek --manual-new-key-stdin
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from env.env_loader import load_hermes_env, get_generated_env_path  # pyright: ignore[reportMissingImports]
from security.file_permissions import check_env_file  # pyright: ignore[reportMissingImports]
from security.redaction import redact  # pyright: ignore[reportMissingImports]
from security.vaultwarden_backend import VaultwardenSecretBackend  # pyright: ignore[reportMissingImports]


# ─── Helpers ───────────────────────────────────────────────────────────


def _print_json(obj: dict) -> None:
    print(json.dumps(obj, indent=2))


def _compute_status(backend: VaultwardenSecretBackend) -> dict:
    """Return a fingerprint/age report for all known provider secrets."""
    refs = backend.list_secret_refs()
    result: dict = {
        "ok": True,
        "backend": "vaultwarden",
        "ref_count": len(refs),
        "timestamp": int(time.time()),
        "secrets": {},
    }
    for ref in sorted(refs):
        meta = backend.get_metadata(ref)
        if meta:
            result["secrets"][ref] = {
                "fingerprint": meta.fingerprint,
                "last4": meta.last4,
                "updated_at": meta.updated_at,
                "provider": meta.provider,
            }
        else:
            result["secrets"][ref] = {"error": "metadata unavailable"}
    return result


# ─── Commands ──────────────────────────────────────────────────────────


def cmd_doctor_secrets(backend: VaultwardenSecretBackend) -> dict:
    """--doctor-secrets: comprehensive diagnostic output (spec section 21)."""
    result: dict = {
        "SECRET BACKEND": {},
        "ENV FILES": {},
        "DOCS SINK": {},
        "SUMMARY": {},
    }

    # Backend health
    hc = backend.healthcheck()
    result["SECRET BACKEND"] = {
        "type": "vaultwarden",
        "mode": backend.mode,
        "server": "configured",
        "tls": "verified" if hc.get("tls_configured") else "failed",
        "auth": "configured" if hc.get("authenticated") else "missing",
        "vault": "unlocked" if hc.get("unlocked") else "locked",
        "refs": hc.get("ref_count", 0),
    }

    if hc.get("ok"):
        result["SECRET BACKEND"]["status"] = "ready"
        risk_parts = []
        if backend.mode == "bitwarden_cli_password":
            risk_parts.append("medium: password bootstrap configured in ~/.hermes/.env")
        if risk_parts:
            result["SECRET BACKEND"]["risk"] = "; ".join(risk_parts)
            result["SECRET BACKEND"]["fix"] = (
                "prefer API-key login or short-lived BW_SESSION where possible"
            )
    elif not hc.get("tls_configured"):
        result["SECRET BACKEND"]["status"] = "blocked"
        result["SECRET BACKEND"]["risk"] = (
            "critical: secret backend transport not trusted"
        )
    else:
        result["SECRET BACKEND"]["status"] = "blocked"

    # Env file permissions
    env_path = os.path.expanduser("~/.hermes/.env")
    env_check = check_env_file(env_path)
    result["ENV FILES"][".env"] = env_check
    if not env_check.get("safe"):
        result["SECRET BACKEND"]["status"] = "blocked"
        result["SECRET BACKEND"]["risk"] = (
            f"~/.hermes/.env is not chmod 0600 (got {env_check.get('mode')})"
        )
        result["SECRET BACKEND"]["fix"] = "chmod 600 ~/.hermes/.env"

    # Generated env
    gen_path = get_generated_env_path()
    gen_check = check_env_file(gen_path)
    result["ENV FILES"][".env.generated"] = gen_check

    # Docs sink
    result["DOCS SINK"] = {
        "status": "ready",
        "notes": [
            "<obsidian-vault>/HERMES_KEY_ROTATION.md",
            "<obsidian-vault>/AI_PROVIDER_KEYS_STATUS.md",
        ],
    }

    result["SUMMARY"]["status"] = result["SECRET BACKEND"].get("status", "unknown")
    return result


def cmd_render_env(backend: VaultwardenSecretBackend, dry_run: bool = False) -> dict:
    """--render-env: generate ~/.hermes/.env.generated from Vaultwarden."""
    from env.render_env import render_env, render_env_content  # pyright: ignore[reportMissingImports]

    if dry_run:
        content = render_env_content(backend)
        _print_json({"ok": True, "dry_run": True, "vars_rendered": content.count("=")})
        return {"ok": True, "dry_run": True}

    path = render_env(backend)
    return {"ok": True, "output": path, "rendered": True}


# ─── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hermes Key Rotation — AI Provider Credential Manager",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without making changes"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show fingerprint and age report for all provider secrets",
    )
    parser.add_argument(
        "--doctor-secrets",
        action="store_true",
        help="Full diagnostic: backend health, env permissions, doc sink",
    )
    parser.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )

    # Secret backend
    parser.add_argument(
        "--secret-backend",
        choices=["vaultwarden"],
        default="vaultwarden",
        help="Secret backend type (default: vaultwarden)",
    )
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Check Vaultwarden backend connectivity and auth",
    )
    parser.add_argument(
        "--unlock", action="store_true", help="Unlock the Vaultwarden vault"
    )
    parser.add_argument(
        "--lock", action="store_true", help="Lock the Vaultwarden vault"
    )
    parser.add_argument(
        "--sync", action="store_true", help="Sync vault data from the server"
    )
    parser.add_argument(
        "--list-refs",
        action="store_true",
        help="List all secret refs stored in Vaultwarden",
    )
    parser.add_argument(
        "--render-env",
        action="store_true",
        help="Generate ~/.hermes/.env.generated from Vaultwarden secrets",
    )

    # Provider
    parser.add_argument(
        "--provider",
        choices=[
            "openai",
            "anthropic",
            "google",
            "github",
            "deepseek",
            "all",
        ],
        help="Provider to rotate keys for (or 'all')",
    )

    args = parser.parse_args()

    # Load bootstrap config
    env = load_hermes_env()

    # Init backend
    try:
        backend = VaultwardenSecretBackend(
            server_url=env.get("VAULTWARDEN_SERVER_URL", "<vaultwarden-url>"),
            mode=env.get("HERMES_AUTH_MODE", "bitwarden_cli_password"),
            appdata_dir=env.get("BITWARDENCLI_APPDATA_DIR"),
            user=env.get("VAULTWARDEN_USER"),
            password=env.get("VAULTWARDEN_PASSWORD"),
            bw_client_id=env.get("BW_CLIENTID"),
            bw_client_secret=env.get("BW_CLIENTSECRET"),
            bw_password=env.get("BW_PASSWORD"),
            bw_session=env.get("BW_SESSION"),
        )
    except Exception as e:
        _print_json(
            {"ok": False, "error": redact(str(e)), "error_type": type(e).__name__}
        )
        sys.exit(1)

    try:
        # ── Backend commands ──
        if args.healthcheck:
            _print_json(backend.healthcheck())
        elif args.unlock:
            backend.unlock()
            _print_json({"ok": True, "session_obtained": True})
        elif args.lock:
            backend.lock()
            _print_json({"ok": True, "locked": True})
        elif args.sync:
            backend.sync()
            _print_json({"ok": True, "synced": True})
        elif args.list_refs:
            refs = backend.list_secret_refs()
            _print_json({"ok": True, "refs": refs, "count": len(refs)})
        elif args.render_env:
            result = cmd_render_env(backend, dry_run=args.dry_run)
            _print_json(result)
        elif args.doctor_secrets:
            result = cmd_doctor_secrets(backend)
            _print_json(result)
        elif args.status:
            backend.authenticate()
            backend.unlock()
            result = _compute_status(backend)
            _print_json(result)
        elif args.dry_run:
            # Dry run on its own: show what would happen
            hc = backend.healthcheck()
            refs = backend.list_secret_refs() if hc.get("unlocked") else []
            _print_json(
                {
                    "ok": hc.get("ok", False),
                    "dry_run": True,
                    "backend_health": hc,
                    "would_rotate": list(refs),
                    "ref_count": len(refs),
                }
            )
        else:
            # No command given — show help-like output
            hc = backend.healthcheck()
            env_check = check_env_file(os.path.expanduser("~/.hermes/.env"))
            _print_json(
                {
                    "ok": hc.get("ok", False),
                    "message": "No operation specified. Use --doctor-secrets, --healthcheck, --render-env, etc.",
                    "backend": hc,
                    "env_permissions": env_check,
                }
            )
    except Exception as e:
        _print_json(
            {
                "ok": False,
                "error": redact(str(e)),
                "error_type": type(e).__name__,
            }
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
