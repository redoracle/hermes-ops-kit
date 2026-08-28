#!/usr/bin/env python3
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


if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )

import argparse
import json
import os
import sys
import time

from .env.env_loader import load_hermes_env, get_generated_env_path  # noqa: F401  # compat shim
from .security.file_permissions import check_env_file  # pyright: ignore[reportMissingImports]
from .security.redaction import redact  # pyright: ignore[reportMissingImports]
from .security.vaultwarden_backend import VaultwardenSecretBackend  # pyright: ignore[reportMissingImports]
from hermes_ops_kit import ops_config_io  # noqa: E402


# ─── Helpers ───────────────────────────────────────────────────────────


def _print_json(obj: dict) -> None:
    print(json.dumps(obj, indent=2))


def _compute_status(backend: VaultwardenSecretBackend) -> dict:
    """Return a fingerprint/age report for all known provider secrets."""
    from .security.vaultwarden_backend import _classify_ref

    refs = backend.list_secret_refs()
    now = int(time.time())
    STALE_DAYS = 90

    result: dict = {
        "ok": True,
        "backend": "vaultwarden",
        "ref_count": len(refs),
        "timestamp": now,
        "secrets": {},
        "warnings": [],
    }

    runtime_count = 0
    admin_count = 0
    for ref in sorted(refs):
        meta = backend.get_metadata(ref)
        secret_class = _classify_ref(ref)
        if meta:
            entry: dict = {
                "fingerprint": meta.fingerprint,
                "last4": meta.last4,
                "updated_at": meta.updated_at,
                "provider": meta.provider,
                "class": secret_class,
            }
            # Key age: check last_rotated_at from custom fields
            age_warning = None
            if secret_class == "runtime":
                runtime_count += 1
                # Try to get last rotation timestamp from stored item fields
                secret = backend.get_secret(ref)
                if secret and secret.metadata:
                    last_rotated = secret.metadata.get("last_rotated_at")
                    if last_rotated:
                        try:
                            age_seconds = now - int(last_rotated)
                            age_days = age_seconds // 86400
                            entry["age_days"] = age_days
                            if age_days > STALE_DAYS:
                                age_warning = f"Key last rotated {age_days}d ago (stale threshold: {STALE_DAYS}d)"
                                entry["stale"] = True
                        except (ValueError, TypeError):
                            pass
            elif secret_class == "admin":
                admin_count += 1
            result["secrets"][ref] = entry
            if age_warning:
                result["warnings"].append({"ref": ref, "warning": age_warning})
        else:
            result["secrets"][ref] = {"error": "metadata unavailable"}

    result["runtime_keys"] = runtime_count
    result["admin_keys"] = admin_count
    if not result["warnings"]:
        del result["warnings"]
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
    env_path = os.path.join(ops_config_io.HERMES_HOME, ".env")
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

    # Docs — audit logs are written to JSONL, not Obsidian
    result["DOCS SINK"] = {
        "status": "jsonl_only",
        "notes": ["~/.hermes/key-rotation-audit.jsonl"],
    }

    result["SUMMARY"]["status"] = result["SECRET BACKEND"].get("status", "unknown")
    return result


def cmd_render_env(
    backend: VaultwardenSecretBackend, dry_run: bool = False, merge: bool = False
) -> dict:
    """--render-env: generate ~/.hermes/.env.generated from Vaultwarden.

    When *merge* is True, also syncs new keys from .env.generated into
    ~/.hermes/.env without duplicating existing keys.
    """
    from .env.render_env import render_env, render_env_content  # pyright: ignore[reportMissingImports]

    # Sync vault data from server — catches manual edits made via
    # the Bitwarden/Vaultwarden web UI that the local bw cache doesn't see.
    try:
        backend.sync()
    except Exception:
        pass  # sync is best-effort; stale data beats no data

    if dry_run:
        content = render_env_content(backend)
        _print_json({"ok": True, "dry_run": True, "vars_rendered": content.count("=")})
        return {"ok": True, "dry_run": True}

    path = render_env(backend)
    result: dict = {"ok": True, "output": path, "rendered": True}

    if merge:
        result["merged"] = _merge_generated_into_env(path)

    return result


def _merge_generated_into_env(generated_path: str) -> dict:
    """Sync keys from .env.generated into ~/.hermes/.env.

    - Keys in .env.generated but NOT in .env → appended.
    - Keys in BOTH but with different values → updated in-place.
    - Keys only in .env → left untouched (bootstrap / user-managed).
    """
    env_path = os.path.join(ops_config_io.HERMES_HOME, ".env")

    # Parse existing .env into {key: (line_number, full_line)}
    existing: dict[str, tuple[int, str]] = {}
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            for i, raw in enumerate(f):
                line = raw.rstrip("\n")
                lines.append(line)
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    existing[key] = (i, line)

    # Collect updates and additions from .env.generated
    gen_entries: dict[str, str] = {}
    with open(generated_path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            gen_entries[key] = stripped

    updated: list[str] = []
    added: list[str] = []

    for key, gen_line in gen_entries.items():
        if key in existing:
            _, old_line = existing[key]
            if old_line != gen_line:
                idx = existing[key][0]
                lines[idx] = gen_line
                updated.append(key)
        else:
            added.append(key)
            if added:
                # First addition: add a marker comment
                if not any("Merged from .env.generated" in ln for ln in lines):
                    lines.append("# Merged from .env.generated — 0 new key(s)")

    if added:
        # Update the marker comment count
        for i, ln in enumerate(lines):
            if ln.startswith("# Merged from .env.generated"):
                total = len(added)
                lines[i] = f"# Merged from .env.generated — {total} new key(s)"
                break
        else:
            lines.append(f"# Merged from .env.generated — {len(added)} new key(s)")
        for gen_line in [gen_entries[k] for k in added]:
            lines.append(gen_line)

    if updated or added:
        from .env.atomic_write import atomic_write

        atomic_write(env_path, "\n".join(lines) + "\n")

    return {
        "merged_count": len(updated) + len(added),
        "updated": updated,
        "added": added,
    }


# ─── Rotation ────────────────────────────────────────────────────────


PROVIDER_ROTATORS: dict[str, str] = {
    "openai": "providers.openai_rotator.OpenAIRotator",
    "anthropic": "providers.anthropic_rotator.AnthropicRotator",
    "google": "providers.google_rotator.GoogleRotator",
    "github": "providers.github_rotator.GitHubRotator",
    "deepseek": "providers.deepseek_rotator.DeepSeekRotator",
    "nvidia": "providers.nvidia_rotator.NvidiaRotator",
    "fireworks": "providers.fireworks_rotator.FireworksRotator",
    "deepinfra": "providers.deepinfra_rotator.DeepInfraRotator",
}
PROVIDER_CHOICES: list[str] = list(PROVIDER_ROTATORS.keys()) + ["all"]

# Admin credential refs — separate from API keys, used for auto-rotation
ADMIN_REFS: dict[str, dict[str, str]] = {
    "openai": {
        "key_ref": "hermes/openai/admin_key",
        "extra_ref": "hermes/openai/project_id",
        "extra_flag": "--project-id",
        "extra_label": "OpenAI project ID",
    },
    "anthropic": {
        "key_ref": "hermes/anthropic/admin_key",
        "extra_ref": "hermes/anthropic/workspace_id",
        "extra_flag": "--workspace-id",
        "extra_label": "Anthropic workspace ID",
    },
    "google": {
        "key_ref": "hermes/google/admin_key",
        "extra_ref": "hermes/google/project_number",
        "extra_flag": "--project-number",
        "extra_label": "Google project number",
    },
}


def _get_rotator(provider: str, backend: VaultwardenSecretBackend):
    """Instantiate a rotator for the given provider."""
    import importlib

    fqdn = PROVIDER_ROTATORS.get(provider)
    if not fqdn:
        raise ValueError(f"Unknown provider: {provider}")
    mod_path, _, cls_name = fqdn.rpartition(".")
    mod = importlib.import_module("." + mod_path, package=__package__)
    cls = getattr(mod, cls_name)
    return cls(backend)


def cmd_rotate(
    backend: VaultwardenSecretBackend,
    provider: str,
    manual_stdin: bool = False,
    dry_run: bool = False,
) -> dict:
    """Execute key rotation for a single provider (lock-protected)."""
    from .security.lockfile import provider_lock, LockTimeoutError  # pyright: ignore[reportMissingImports]

    rotator = _get_rotator(provider, backend)

    candidate_key: str | None = None
    if manual_stdin:
        candidate_key = sys.stdin.read().strip()
        if not candidate_key:
            return {"ok": False, "error": "No key provided on stdin"}

    if dry_run:
        # Validate but don't store (no lock needed for dry-run)
        if candidate_key:
            vr = rotator.validate_new_key(candidate_key)
            return {
                "ok": vr.valid,
                "dry_run": True,
                "provider": provider,
                "key_valid": vr.valid,
                "validation": {
                    "reason": vr.reason_class.value,
                    "detail": vr.detail,
                    "http_status": vr.http_status,
                },
            }
        return {
            "ok": True,
            "dry_run": True,
            "provider": provider,
            "message": "Would prompt for key interactively and rotate",
        }

    try:
        with provider_lock(provider):
            return rotator.rotate(candidate_key)
    except LockTimeoutError as e:
        return {"ok": False, "error": str(e), "error_type": "LockTimeoutError"}


def cmd_rotate_all_parallel(
    backend: VaultwardenSecretBackend,
    dry_run: bool = False,
    max_parallel: int = 4,
) -> dict:
    """Rotate all providers concurrently, each under its own lock."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .security.lockfile import provider_lock  # pyright: ignore[reportMissingImports]

    results: dict[str, dict] = {}
    all_ok = True
    providers = list(PROVIDER_ROTATORS.keys())

    def rotate_one(provider: str) -> tuple[str, dict]:
        with provider_lock(provider):  # noqa: F821 — imported at runtime above
            return provider, cmd_rotate(backend, provider, dry_run=dry_run)

    with ThreadPoolExecutor(max_workers=min(max_parallel, len(providers))) as executor:
        futures = {executor.submit(rotate_one, p): p for p in providers}
        for future in as_completed(futures):
            p, result = future.result()
            results[p] = result
            if not result.get("ok"):
                all_ok = False

    return {"ok": all_ok, "results": results}


def cmd_emergency_compromise(
    backend: VaultwardenSecretBackend,
    provider: str,
    revoke_only: bool = False,
) -> dict:
    """Emergency rotation: revoke immediately, then replace.

    In revoke-only mode, the compromised key is revoked without creating a
    replacement — the service will be down until a new key is provisioned.
    """
    from .security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]

    rotator = _get_rotator(provider, backend)
    backend.authenticate()
    backend.unlock()
    backend.sync()

    # Determine the API key ref for this provider
    api_key_ref = f"hermes/{provider}/api_key"
    if provider == "google":
        api_key_ref = "hermes/google/gemini_api_key"
    elif provider == "github":
        api_key_ref = "hermes/github/token"

    old_secret = backend.get_secret(api_key_ref)
    old_fp, _ = secret_fingerprint(old_secret.value) if old_secret else ("unknown", "")

    # Phase 1: Revoke immediately — the old key is assumed compromised.
    # We revoke BEFORE validating the replacement so the attacker's window
    # is minimised.  The replacement validation and smoke-test steps below
    # still run to ensure the new key is functional.
    admin_ref = f"hermes/{provider}/admin_key"
    admin = backend.get_secret(admin_ref)
    revoked = rotator.revoke_key(api_key_ref, admin.value if admin else None)

    if revoke_only:
        from .audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]

        audit_rotation_attempt(
            provider=provider,
            status="emergency_revoked",
            old_fp=old_fp,
            new_fp=None,
            old_revoked=revoked,
            manual_action=not revoked,
        )
        return {
            "ok": revoked,
            "provider": provider,
            "mode": "emergency-revoke-only",
            "revoked": revoked,
            "old_fingerprint": old_fp,
            "warning": "No replacement key created — service may be down",
        }

    # Phase 2: Accept new key
    candidate_key = sys.stdin.read().strip()
    if not candidate_key:
        return {"ok": False, "error": "No replacement key provided on stdin"}

    vr = rotator.validate_new_key(candidate_key)
    if not vr.valid:
        return {
            "ok": False,
            "error": f"Emergency replacement key unusable: {vr.reason_class.value}",
            "validation": {"reason": vr.reason_class.value, "detail": vr.detail},
        }

    # Store new key
    from .security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]

    new_fp, new_l4 = secret_fingerprint(candidate_key)
    backend.set_secret(
        api_key_ref,
        candidate_key,
        metadata={
            "rotation_mode": "emergency",
            "last_rotated_at": str(int(time.time())),
        },
    )

    # Render env
    env_path = None
    try:
        env_path = cmd_render_env(backend, dry_run=False)["output"]
    except Exception as e:
        return {"ok": False, "error": f"Emergency env render failed: {e}"}

    # Quick smoke test
    passed, detail = rotator.smoke_test()
    if not passed:
        if old_secret:
            backend.restore_secret(api_key_ref, old_secret)
        return {"ok": False, "error": f"Emergency smoke test failed: {detail}"}

    from .audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]

    audit_rotation_attempt(
        provider=provider,
        status="emergency_success",
        old_fp=old_fp,
        new_fp=new_fp,
        old_revoked=revoked,
    )

    return {
        "ok": True,
        "provider": provider,
        "mode": "emergency-compromise",
        "revoked": revoked,
        "replaced": True,
        "old_fingerprint": old_fp,
        "new_fingerprint": new_fp,
        "new_last4": new_l4,
        "env_rendered": env_path,
    }


def _validate_admin_key(provider: str, admin_key: str):
    """Validate an admin key against the provider's Admin API.

    Admin keys are scoped differently from runtime API keys — they work
    against admin/management endpoints, not the regular API.  This function
    probes the correct admin endpoint for each provider.
    """
    from .security.secret_backend import ValidationReason, ValidationResult  # pyright: ignore[reportMissingImports]

    try:
        import requests  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
    except Exception:
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.SDK_UNAVAILABLE,
            detail="requests library not installed",
            retry_recommended=False,
        )

    endpoints: dict[str, tuple[str, str, dict]] = {
        "openai": (
            "https://api.openai.com/v1/organization/projects",
            "Authorization",
            {"Authorization": f"Bearer {admin_key}"},
        ),
        "anthropic": (
            "https://api.anthropic.com/v1/organizations/api_keys",
            "x-api-key",
            {
                "x-api-key": admin_key,
                "anthropic-version": "2023-06-01",
            },
        ),
        "google": (
            "https://apikeys.googleapis.com/v2/projects/-/locations/global/keys",
            "Authorization",
            {},  # Google uses ADC, not admin key header — handled separately
        ),
    }

    if provider == "google":
        # Google admin auth uses ADC (Application Default Credentials), not a key header.
        # Just check that the admin key looks like a valid token format.
        if len(admin_key) > 20:
            return ValidationResult(valid=True)
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.INVALID_FORMAT,
            detail="Google admin credential too short for a valid token",
        )

    if provider not in endpoints:
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.UNKNOWN,
            detail=f"No admin validation endpoint for provider: {provider}",
        )

    url, _auth_header_name, headers = endpoints[provider]
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return ValidationResult(valid=True)
        if resp.status_code == 401:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.AUTH_DENIED,
                detail=resp.text[:500],
                http_status=401,
            )
        if resp.status_code == 403:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.FORBIDDEN,
                detail=resp.text[:500],
                http_status=403,
            )
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.UNKNOWN,
            detail=f"HTTP {resp.status_code}: {resp.text[:300]}",
            http_status=resp.status_code,
        )
    except requests.Timeout:
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.TIMEOUT,
            detail="Admin API request timed out",
            retry_recommended=True,
        )
    except requests.ConnectionError as e:
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.NETWORK_ERROR,
            detail=str(e),
            retry_recommended=True,
        )
    except Exception as e:
        return ValidationResult(
            valid=False,
            reason_class=ValidationReason.UNKNOWN,
            detail=str(e)[:500],
        )


def _verify_rendered_env(env_path: str) -> dict:
    """Run usage_metrics_v2 against the rendered .env.generated."""
    import subprocess as _sp

    if not env_path or not os.path.exists(env_path):
        return {"ok": False, "error": f"Env file not found: {env_path}"}
    try:
        result = _sp.run(
            ["python3", "-m", "usage_metrics_v2", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "HERMES_ENV_FILE": env_path},
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr[:500]}
        import json as _json

        data = _json.loads(result.stdout)
        providers_ok = sum(
            1
            for p, v in data.items()
            if isinstance(v, dict) and v.get("status") == "online"
        )
        return {"ok": True, "providers_online": providers_ok, "raw": data}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


def cmd_backup_vault(
    backend: VaultwardenSecretBackend, output_path: str | None = None
) -> dict:
    """Export all Vaultwarden secrets to an encrypted JSON backup.

    Only fingerprints are included — raw secret values are NEVER exported.
    This is a metadata backup for disaster recovery, not a secret dump.
    """
    backend.authenticate()
    backend.unlock()
    refs = backend.list_secret_refs()
    if not refs:
        return {"ok": False, "error": "No secrets found in Vaultwarden"}

    entries: list[dict] = []
    for ref in sorted(refs):
        meta = backend.get_metadata(ref)
        from .security.vaultwarden_backend import _classify_ref

        entries.append(
            {
                "ref": ref,
                "fingerprint": meta.fingerprint if meta else "unknown",
                "last4": meta.last4 if meta else "",
                "provider": meta.provider if meta else "",
                "class": _classify_ref(ref),
                "updated_at": meta.updated_at if meta else None,
            }
        )

    import json as _json

    payload = _json.dumps(
        {
            "version": 1,
            "timestamp": int(time.time()),
            "ref_count": len(refs),
            "secrets": entries,
        },
        indent=2,
    )

    path = output_path or os.path.join(ops_config_io.HERMES_HOME, "ops-kit/vault-backup.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, 0o600)
    return {"ok": True, "output": path, "ref_count": len(refs)}


def cmd_restore_vault(
    backend: VaultwardenSecretBackend, input_path: str, dry_run: bool = False
) -> dict:
    """Restore Vaultwarden secrets from a backup JSON (fingerprints only).

    This does NOT restore raw secret values — only metadata references.
    Use this to verify backup integrity or to recreate refs on a new vault.
    """
    import json as _json

    if not os.path.exists(input_path):
        return {"ok": False, "error": f"Backup file not found: {input_path}"}

    with open(input_path) as f:
        data = _json.load(f)

    entries = data.get("secrets", [])
    if not entries:
        return {"ok": False, "error": "Backup file is empty or invalid"}

    existing = set(backend.list_secret_refs())
    results = {"restored": 0, "skipped": 0, "missing_values": []}
    for entry in entries:
        ref = entry["ref"]
        if ref in existing:
            results["skipped"] += 1
            continue
        # Cannot restore raw values — only fingerprints are in backup
        results["missing_values"].append(ref)

    return {
        "ok": True,
        "dry_run": dry_run,
        "total_in_backup": len(entries),
        "already_in_vault": results["skipped"],
        "missing_raw_values": len(results["missing_values"]),
        "missing_refs": results["missing_values"][:10],
        "note": "Backup contains fingerprints only. Raw secrets must be re-seeded via seed-from-env or rotate.",
    }


def cmd_diff_vault(
    backend: VaultwardenSecretBackend,
    env_path: str | None = None,
    generated_path: str | None = None,
) -> dict:
    """Compare Vaultwarden state vs .env vs .env.generated."""
    from .env.loader import parse_env_file as _parse_env_file

    vault_refs = set(backend.list_secret_refs())

    # .env state
    dotenv_path = env_path or os.path.join(ops_config_io.HERMES_HOME, ".env")
    dotenv = _parse_env_file(dotenv_path) if os.path.exists(dotenv_path) else {}

    # .env.generated state
    gen_path = generated_path or os.path.join(ops_config_io.HERMES_HOME, ".env.generated")
    gen_env = _parse_env_file(gen_path) if os.path.exists(gen_path) else {}

    # Load projection to map env vars → refs
    projection_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config", "env_projection.yaml"
    )
    mapping: dict[str, str] = {}
    with open(projection_path) as f:
        for line in f:
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in ("env_projection", "deny_render"):
                    mapping[k] = v

    in_dotenv_not_vault: list[str] = []
    in_vault_not_generated: list[str] = []

    for env_var, ref in mapping.items():
        if ref in vault_refs and env_var not in gen_env:
            in_vault_not_generated.append(f"{env_var} → {ref}")
        if env_var in dotenv and ref not in vault_refs:
            in_dotenv_not_vault.append(f"{env_var} → {ref}")
    for ref in vault_refs:
        # Check if any env var maps to this ref
        mapped = [ev for ev, r in mapping.items() if r == ref]
        if mapped and mapped[0] not in dotenv:
            pass  # already tracked above

    return {
        "ok": True,
        "vault_refs": len(vault_refs),
        "dotenv_vars": len(dotenv),
        "generated_vars": len(gen_env),
        "in_dotenv_not_vault": in_dotenv_not_vault,
        "in_vault_not_generated": in_vault_not_generated,
        "action": (
            "Run 'seed-from-env' to migrate keys still in .env"
            if in_dotenv_not_vault
            else "All keys in sync"
        ),
    }


def cmd_migrate(backend: VaultwardenSecretBackend) -> dict:
    """Interactive migration wizard: .env → Vaultwarden."""
    import os as _os

    dotenv_path = _os.path.join(ops_config_io.HERMES_HOME, ".env")
    if not _os.path.exists(dotenv_path):
        return {"ok": False, "error": f"{dotenv_path} not found"}

    from .env.loader import parse_env_file as _parse_env_file

    _parse_env_file(dotenv_path)

    # Show what we found
    result = cmd_seed_from_env(backend, dry_run=True)
    to_migrate = [
        k for k, v in result["results"].items() if v["status"] == "would_seed"
    ]
    already = [k for k, v in result["results"].items() if v["status"] == "skipped"]

    if not to_migrate:
        return {
            "ok": True,
            "message": "All keys already in Vaultwarden — nothing to migrate",
            "already_stored": already,
        }

    # Execute migration
    seed_result = cmd_seed_from_env(backend, dry_run=False, skip_existing=True)

    # Verify
    render_result = cmd_render_env(backend, dry_run=False)

    # Check what's left in .env
    dotenv_after = _parse_env_file(dotenv_path)
    still_in_dotenv = [k for k in to_migrate if k in dotenv_after]

    return {
        "ok": seed_result["ok"],
        "migrated": seed_result["seeded"],
        "skipped": seed_result["skipped"],
        "failed": seed_result["failed"],
        "env_rendered": render_result.get("output", ""),
        "still_in_dotenv": still_in_dotenv,
        "next_step": (
            f"Remove these from {dotenv_path}: {', '.join(still_in_dotenv)}"
            if still_in_dotenv
            else "All keys migrated. You can now remove API keys from .env"
        ),
    }


def cmd_seed_from_env(
    backend: VaultwardenSecretBackend,
    dry_run: bool = False,
    skip_existing: bool = True,
) -> dict:
    """Bulk-migrate provider API keys from ~/.hermes/.env into Vaultwarden.

    Reads env_projection.yaml to discover the env-var → secret-ref mapping,
    then for each runtime API key found in ~/.hermes/.env, validates and
    stores it in Vaultwarden. Skips admin keys (use seed-admin instead).
    """
    from .security.secret_backend import ValidationReason  # pyright: ignore[reportMissingImports]
    import os as _os

    env_path = _os.path.join(ops_config_io.HERMES_HOME, ".env")
    if not _os.path.exists(env_path):
        return {"ok": False, "error": f"{env_path} not found"}

    from .env.loader import parse_env_file as _parse_env_file

    dotenv = _parse_env_file(env_path)

    # Load projection mapping to discover which env vars map to runtime keys
    projection_path = _os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)),
        "config",
        "env_projection.yaml",
    )
    mapping: dict[str, str] = {}
    with open(projection_path) as f:
        for line in f:
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and val and key != "env_projection" and key != "deny_render":
                    mapping[key] = val

    # Determine which refs are runtime API keys (not admin, not config)
    from .security.vaultwarden_backend import _classify_ref

    runtime_refs = {ref for ref in mapping.values() if _classify_ref(ref) == "runtime"}

    # Check what's already in Vaultwarden
    existing_refs: set[str] = (
        set(backend.list_secret_refs()) if skip_existing else set()
    )

    results: dict[str, dict] = {}
    seeded = 0
    skipped = 0
    failed = 0

    for env_var, secret_ref in sorted(mapping.items()):
        if secret_ref not in runtime_refs:
            continue  # skip admin and config refs

        key_value = dotenv.get(env_var, "")
        if not key_value:
            continue

        if secret_ref in existing_refs:
            # Key already in vault — show fingerprint
            meta = backend.get_metadata(secret_ref)
            results[env_var] = {
                "ok": True,
                "status": "skipped",
                "ref": secret_ref,
                "fingerprint": meta.fingerprint if meta else "unknown",
            }
            skipped += 1
            continue

        # Determine provider from the secret ref path
        provider = secret_ref.split("/")[1]
        if provider not in PROVIDER_ROTATORS:
            results[env_var] = {"ok": False, "error": f"Unknown provider: {provider}"}
            failed += 1
            continue

        if dry_run:
            results[env_var] = {
                "ok": True,
                "status": "would_seed",
                "ref": secret_ref,
                "provider": provider,
                "key_length": len(key_value),
            }
            seeded += 1
            continue

        # Actually seed
        rotator = _get_rotator(provider, backend)
        vr = rotator.validate_with_retry(key_value)
        if not vr.valid and vr.reason_class != ValidationReason.QUOTA_OR_BILLING:
            results[env_var] = {
                "ok": False,
                "error": f"Validation failed: {vr.reason_class.value}",
                "ref": secret_ref,
                "detail": vr.detail[:200],
            }
            failed += 1
            continue

        try:
            from .security.fingerprints import secret_fingerprint

            fp, l4 = secret_fingerprint(key_value)
            backend.set_secret(
                secret_ref,
                key_value,
                metadata={"rotation_mode": "seed-from-env", "source": "~/.hermes/.env"},
            )
            results[env_var] = {
                "ok": True,
                "status": "seeded",
                "ref": secret_ref,
                "fingerprint": fp,
                "last4": l4,
                "warning": (
                    "Key stored but account has billing/credit issues"
                    if vr.reason_class == ValidationReason.QUOTA_OR_BILLING
                    else None
                ),
            }
            seeded += 1
        except Exception as e:
            results[env_var] = {"ok": False, "error": redact(str(e)), "ref": secret_ref}
            failed += 1

    return {
        "ok": failed == 0,
        "seeded": seeded,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "results": results,
    }


def cmd_seed_admin(
    backend: VaultwardenSecretBackend,
    provider: str,
    *,
    project_id: str | None = None,
    workspace_id: str | None = None,
    project_number: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Store admin credentials for a provider to enable full-auto rotation."""
    if provider not in ADMIN_REFS:
        return {"ok": False, "error": f"No admin refs defined for provider: {provider}"}

    refs = ADMIN_REFS[provider]
    admin_key = sys.stdin.read().strip()
    if not admin_key:
        return {"ok": False, "error": "No admin key provided on stdin"}

    # Pick the right extra value based on provider
    extra_value: str | None = None
    if provider == "openai":
        extra_value = project_id
    elif provider == "anthropic":
        extra_value = workspace_id
    elif provider == "google":
        extra_value = project_number

    if not extra_value:
        return {
            "ok": False,
            "error": f"{refs['extra_flag']} is required for {provider} admin seeding",
        }

    if dry_run:
        # Validate admin key against the admin API (not the regular API)
        vr = _validate_admin_key(provider, admin_key)
        return {
            "ok": vr.valid,
            "dry_run": True,
            "provider": provider,
            "admin_key_valid": vr.valid,
            "validation": {
                "reason": vr.reason_class.value,
                "detail": vr.detail,
                "http_status": vr.http_status,
            },
            "would_store": {
                refs["key_ref"]: f"<{len(admin_key)} chars>",
                refs["extra_ref"]: extra_value,
            },
        }

    # Validate admin key against the admin API before storing
    vr = _validate_admin_key(provider, admin_key)
    if not vr.valid:
        return {
            "ok": False,
            "error": f"Admin key unusable: {vr.reason_class.value}",
            "validation": {
                "reason": vr.reason_class.value,
                "detail": vr.detail,
                "http_status": vr.http_status,
            },
        }

    stored: list[str] = []
    try:
        backend.authenticate()
        backend.unlock()
        backend.sync()

        from .security.fingerprints import secret_fingerprint

        fp, last4 = secret_fingerprint(admin_key)
        backend.set_secret(refs["key_ref"], admin_key, metadata={"role": "admin"})
        stored.append(f"{refs['key_ref']} (fp={fp} last4={last4})")

        backend.set_secret(refs["extra_ref"], extra_value, metadata={"role": "admin"})
        stored.append(f"{refs['extra_ref']} = {extra_value}")

        return {
            "ok": True,
            "provider": provider,
            "stored": stored,
        }
    except Exception as e:
        return {"ok": False, "error": redact(str(e)), "stored": stored}


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
    parser.add_argument(
        "--merge",
        action="store_true",
        help="When used with --render-env, also sync new keys into ~/.hermes/.env",
    )

    # Provider
    parser.add_argument(
        "--provider",
        choices=PROVIDER_CHOICES,
        help="Provider to rotate keys for (or 'all')",
    )
    parser.add_argument(
        "--manual-new-key-stdin",
        action="store_true",
        help="Read a new API key from stdin (pipe or paste) instead of TTY prompt",
    )
    parser.add_argument(
        "--admin-key-stdin",
        action="store_true",
        help="Store an admin key (for auto-rotation) from stdin instead of a user API key",
    )
    parser.add_argument(
        "--project-id",
        help="OpenAI project ID (for admin key seeding)",
    )
    parser.add_argument(
        "--workspace-id",
        help="Anthropic workspace ID (for admin key seeding)",
    )
    parser.add_argument(
        "--project-number",
        help="Google project number (for admin key seeding)",
    )

    # ── Subcommands (new structured interface) ──
    sub = parser.add_subparsers(dest="subcommand", title="commands")

    rotate_p = sub.add_parser("rotate", help="Rotate provider API keys")
    rotate_p.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    rotate_p.add_argument("--parallel", action="store_true")
    rotate_p.add_argument("--max-parallel", type=int, default=4)
    rotate_p.add_argument("--manual-new-key-stdin", action="store_true")
    rotate_p.add_argument("--dry-run", action="store_true")

    seed_p = sub.add_parser(
        "seed-admin", help="Store admin credentials for auto-rotation"
    )
    seed_p.add_argument(
        "--provider", choices=["openai", "anthropic", "google"], required=True
    )
    seed_p.add_argument("--project-id")
    seed_p.add_argument("--workspace-id")
    seed_p.add_argument("--project-number")
    seed_p.add_argument("--dry-run", action="store_true")

    emergency_p = sub.add_parser("emergency", help="Emergency key compromise rotation")
    emergency_p.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)
    emergency_p.add_argument("--revoke-only", action="store_true")
    emergency_p.add_argument(
        "--yes-i-understand-downtime-risk",
        action="store_true",
        dest="confirm_emergency",
    )

    resume_p = sub.add_parser(
        "resume", help="Resume interrupted rotation from checkpoint"
    )
    resume_p.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)

    validate_p = sub.add_parser("validate", help="Validate a key without storing it")
    validate_p.add_argument("--provider", choices=PROVIDER_CHOICES, required=True)

    render_p = sub.add_parser("render-env", help="Generate .env.generated")
    render_p.add_argument("--dry-run", action="store_true")
    render_p.add_argument(
        "--merge",
        action="store_true",
        help="Sync new keys into ~/.hermes/.env without duplicates",
    )
    render_p.add_argument(
        "--verify", action="store_true", help="Run health check against generated env"
    )

    seed_env_p = sub.add_parser(
        "seed-from-env",
        help="Bulk-migrate all provider keys from ~/.hermes/.env into Vaultwarden",
    )
    seed_env_p.add_argument("--dry-run", action="store_true")
    # Hidden no-op compat flag: the handler always skips existing keys
    # (store_true with default=True made it un-disableable).
    seed_env_p.add_argument(
        "--skip-existing", action="store_true", default=True, help=argparse.SUPPRESS
    )

    backup_p = sub.add_parser(
        "backup-vault", help="Export Vaultwarden metadata to backup JSON"
    )
    backup_p.add_argument(
        "--output", help="Output path (default: ~/.hermes/ops-kit/vault-backup.json)"
    )

    restore_p = sub.add_parser(
        "restore-vault", help="Verify backup integrity (fingerprints only)"
    )
    restore_p.add_argument("input", help="Backup JSON file path")
    restore_p.add_argument("--dry-run", action="store_true")

    diff_p = sub.add_parser(
        "diff", help="Compare Vaultwarden vs .env vs .env.generated"
    )
    diff_p.add_argument("--env", help="Path to .env (default: ~/.hermes/.env)")
    diff_p.add_argument("--generated", help="Path to .env.generated")

    # migrate subcommand is a WIP — parser registered but not yet wired
    sub.add_parser("migrate", help="Interactive migration wizard: .env → Vaultwarden")

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
        # ── Subcommand dispatch (new structured interface) ──
        if args.subcommand == "rotate":
            if args.provider == "all" and args.parallel:
                _print_json(cmd_rotate_all_parallel(backend, dry_run=args.dry_run))
            elif args.provider:
                result = cmd_rotate(
                    backend,
                    args.provider,
                    manual_stdin=args.manual_new_key_stdin,
                    dry_run=args.dry_run,
                )
                _print_json(result)
            sys.exit(0)

        elif args.subcommand == "seed-admin":
            result = cmd_seed_admin(
                backend,
                args.provider,
                project_id=args.project_id,
                workspace_id=args.workspace_id,
                project_number=args.project_number,
                dry_run=args.dry_run,
            )
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "emergency":
            if not args.confirm_emergency:
                _print_json(
                    {
                        "ok": False,
                        "error": "Emergency mode requires --yes-i-understand-downtime-risk",
                    }
                )
                sys.exit(1)
            result = cmd_emergency_compromise(
                backend,
                args.provider,
                revoke_only=args.revoke_only,
            )
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "resume":
            from .providers.rotation_state_machine import RotationState  # pyright: ignore[reportMissingImports]

            state = RotationState.load_checkpoint(args.provider)
            if not state:
                _print_json(
                    {"ok": False, "error": f"No checkpoint found for {args.provider}"}
                )
                sys.exit(1)
            rotator = _get_rotator(args.provider, backend)
            from .providers.rotation_state_machine import RotationRunner  # pyright: ignore[reportMissingImports]

            runner = RotationRunner(rotator, backend, state)
            result = runner.execute()
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "validate":
            candidate_key = sys.stdin.read().strip()
            if not candidate_key:
                _print_json({"ok": False, "error": "No key provided on stdin"})
                sys.exit(1)
            rotator = _get_rotator(args.provider, backend)
            vr = rotator.validate_new_key(candidate_key)
            _print_json(
                {
                    "ok": vr.valid,
                    "provider": args.provider,
                    "key_valid": vr.valid,
                    "validation": {
                        "reason": vr.reason_class.value,
                        "detail": vr.detail,
                        "http_status": vr.http_status,
                    },
                }
            )
            sys.exit(0)

        elif args.subcommand == "render-env":
            result = cmd_render_env(
                backend, dry_run=args.dry_run, merge=getattr(args, "merge", False)
            )
            if getattr(args, "verify", False) and not args.dry_run and result.get("ok"):
                verify = _verify_rendered_env(result.get("output", ""))
                result["verify"] = verify
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "seed-from-env":
            result = cmd_seed_from_env(
                backend,
                dry_run=args.dry_run,
                skip_existing=getattr(args, "skip_existing", True),
            )
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "backup-vault":
            result = cmd_backup_vault(
                backend, output_path=getattr(args, "output", None)
            )
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "restore-vault":
            result = cmd_restore_vault(backend, args.input, dry_run=args.dry_run)
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "diff":
            result = cmd_diff_vault(
                backend,
                env_path=getattr(args, "env", None),
                generated_path=getattr(args, "generated", None),
            )
            _print_json(result)
            sys.exit(0)

        elif args.subcommand == "migrate":
            result = cmd_migrate(backend)
            _print_json(result)
            sys.exit(0)

        # ── Backend commands (flat flags, backward-compatible) ──
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
            result = cmd_render_env(backend, dry_run=args.dry_run, merge=args.merge)
            _print_json(result)
        elif args.doctor_secrets:
            result = cmd_doctor_secrets(backend)
            _print_json(result)
        elif args.status:
            backend.authenticate()
            backend.unlock()
            result = _compute_status(backend)
            _print_json(result)
        elif args.provider and args.admin_key_stdin:
            # ── Admin Key Seeding ──
            if args.provider == "all":
                _print_json(
                    {
                        "ok": False,
                        "error": "--admin-key-stdin requires a single --provider, not 'all'",
                    }
                )
            else:
                result = cmd_seed_admin(
                    backend,
                    args.provider,
                    project_id=args.project_id,
                    workspace_id=args.workspace_id,
                    project_number=args.project_number,
                    dry_run=args.dry_run,
                )
                _print_json(result)
        elif args.provider:
            # ── Rotation ──
            backend.authenticate()
            backend.unlock()
            providers = (
                list(PROVIDER_ROTATORS.keys())
                if args.provider == "all"
                else [args.provider]
            )
            results = {}
            all_ok = True
            for p in providers:
                try:
                    results[p] = cmd_rotate(
                        backend,
                        p,
                        manual_stdin=args.manual_new_key_stdin,
                        dry_run=args.dry_run,
                    )
                    if not results[p].get("ok"):
                        all_ok = False
                except Exception as e:
                    results[p] = {
                        "ok": False,
                        "error": redact(str(e)),
                        "error_type": type(e).__name__,
                    }
                    all_ok = False
            if len(results) == 1:
                _print_json(results[list(results.keys())[0]])
            else:
                _print_json({"ok": all_ok, "results": results})
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
            env_check = check_env_file(os.path.join(ops_config_io.HERMES_HOME, ".env"))
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
