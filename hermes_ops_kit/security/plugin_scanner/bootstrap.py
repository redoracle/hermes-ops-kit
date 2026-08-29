"""Hermes Ops Kit — First-install bootstrap for plugin security.

Creates the default scanner config on first use, runs the install-profile
scan, applies preflight enforcement, and writes a human-readable + JSON
report for operators and automation.
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
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast


from ...policy.decisions import preflight_decision  # pyright: ignore[reportMissingImports]
from ...security.plugin_scanner.cache import SCANNER_VERSION as scanner_version  # pyright: ignore[reportMissingImports]
from ...security.plugin_scanner.enforce import _restore_hermes_config  # pyright: ignore[reportMissingImports]
from ...security.plugin_scanner.policy import approve_plugin  # pyright: ignore[reportMissingImports]
from ...security.plugin_scanner.scanner import scan_all  # pyright: ignore[reportMissingImports]
from hermes_ops_kit import ops_config_io  # noqa: E402

# The scanner's own plugin ID — auto-approved during bootstrap since
# the operator has already verified the installation source.
_SCANNER_PLUGIN_ID = "hermes-ops-kit"


HERMES_HOME = Path(ops_config_io.HERMES_HOME)
OPS_KIT_DIR = HERMES_HOME / "ops-kit"
REPORT_DIR = OPS_KIT_DIR / "reports"
SCANNER_CONFIG_PATH = OPS_KIT_DIR / "plugin_scanner.yaml"
ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SCANNER_CONFIG = Path(__file__).resolve().parent / "plugin_scanner.yaml"
PLUGIN_MANIFEST = ROOT_DIR / "plugin.yaml"
DISCLAIMER = (
    "Security scanning reduces risk but does not guarantee that a plugin is safe. "
    "It performs static analysis and optional external-tool checks before execution, "
    "but it is not a runtime antivirus, EDR, or guarantee against zero-days, time "
    "bombs, logic bombs, or sophisticated obfuscation. Review findings before "
    "approving plugins."
)


def _ops_kit_version() -> str:
    from ...ops_config_io import load_yaml

    version = load_yaml(PLUGIN_MANIFEST).get("version")
    if isinstance(version, str) and version:
        return version
    return "unknown"


def _ensure_default_scanner_config(*, dry_run: bool = False) -> dict[str, Any]:
    """Create ~/.hermes/ops-kit/plugin_scanner.yaml if missing."""
    exists = SCANNER_CONFIG_PATH.exists()
    created = not exists and not dry_run
    if created:
        OPS_KIT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(DEFAULT_SCANNER_CONFIG, SCANNER_CONFIG_PATH)
        os.chmod(SCANNER_CONFIG_PATH, 0o600)
    return {
        "created": created,
        "path": str(SCANNER_CONFIG_PATH),
        "exists": exists or created,
        "would_create": not exists and dry_run,
    }


def _tool_availability() -> dict[str, bool]:
    return {
        "built_in": True,
        "semgrep": shutil.which("semgrep") is not None,
        "bandit": shutil.which("bandit") is not None,
        "gitleaks": shutil.which("gitleaks") is not None,
        "headroom": shutil.which("headroom") is not None,
    }


def _seed_headroom_config() -> None:
    """Seed ~/.hermes/ops-kit/headroom.yaml (disabled) on first install.

    Enabling the proxied route stays an explicit operator action:
    `hermes-ops-kit headroom enable`.
    """
    try:
        from ...headroom_ops.settings import seed_deployed  # pyright: ignore[reportMissingImports]

        seed_deployed()
    except Exception:
        pass


def _disclaimer_block() -> list[str]:
    return [
        "DISCLAIMER",
        DISCLAIMER,
    ]


def _summarize_scan_results(results: list[Any]) -> dict[str, Any]:
    by_risk: dict[str, int] = {}
    findings = 0
    for result in results:
        risk = getattr(result, "risk_level", None)
        risk_value = getattr(risk, "value", str(risk))
        by_risk[risk_value] = by_risk.get(risk_value, 0) + 1
        findings += len(getattr(result, "findings", []))
    return {
        "plugins_scanned": len(results),
        "findings": findings,
        "by_risk": dict(sorted(by_risk.items())),
    }


def _report_paths(report_stem: str) -> dict[str, str]:
    return {
        "directory": str(REPORT_DIR),
        "json": str(REPORT_DIR / f"{report_stem}.json"),
        "text": str(REPORT_DIR / f"{report_stem}.txt"),
    }


def _write_reports(report_stem: str, payload: dict[str, Any]) -> dict[str, str]:
    paths = _report_paths(report_stem)
    REPORT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload["report_paths"] = paths

    lines = [
        "Hermes Ops Kit — First Install Security Report",
        f"Timestamp: {payload['timestamp']}",
        f"hermes-ops-kit: {payload['hermes_ops_kit_version']}",
        f"scanner: {payload['scanner_version']}",
        f"scan profile: {payload['scan_profile']}",
        f"report directory: {paths['directory']}",
        "",
        "Installed / modified:",
    ]
    for item in payload["setup"].get("changes", []):
        lines.append(f"  - {item}")
    lines.extend(
        [
            "",
            "Wrapper commands:",
        ]
    )
    for wrapper in payload["wrappers"]:
        lines.append(f"  - {wrapper}")
    lines.extend(
        [
            "",
            "Findings summary:",
            f"  plugins scanned: {payload['install_scan']['summary']['plugins_scanned']}",
            f"  findings: {payload['install_scan']['summary']['findings']}",
            f"  allowed: {len(payload['preflight']['decisions']['allowed'])}",
            f"  deferred: {len(payload['preflight']['decisions']['deferred'])}",
            f"  blocked: {len(payload['preflight']['decisions']['blocked'])}",
            f"  mcp disabled: {len(payload['preflight']['mcp_decisions']['disable'])}",
            "",
            "Docs:",
            "  - docs/plugin-security-scanner.md",
            "  - docs/plugin-security-scanner-design.md",
            "",
            *(_disclaimer_block()),
            "",
            "Next commands:",
        ]
    )
    for cmd in payload["next_commands"]:
        lines.append(f"  - {cmd}")

    _atomic_write_text(
        Path(paths["json"]), json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    _atomic_write_text(Path(paths["text"]), "\n".join(lines) + "\n")
    return paths


def _atomic_write_text(path: Path, content: str) -> None:
    """Write an owner-only text file atomically."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def bootstrap(
    *,
    dry_run: bool = False,
    headless: bool = True,
    force_scan: bool = True,
    restart_command: list[str] | None = None,
) -> dict[str, Any]:
    """Run the first-install setup flow and return a structured report."""
    setup = _ensure_default_scanner_config(dry_run=dry_run)
    if not dry_run:
        _seed_headroom_config()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report_stem = f"bootstrap-{timestamp.replace(':', '').replace('-', '')}"
    wrappers = [
        "hermes-export",
        "hermes-route-manager",
        "hermes-usage",
        "hermes-assistant-manager",
        "hermes-key-rotate",
        "hermes-ops-kit",
        "hermes-skill-factory",
    ]

    install_results = scan_all(profile="install", force=force_scan)
    install_summary = _summarize_scan_results(install_results)

    # Self-approve: the scanner auditing its own plugin is circular —
    # the operator already trusts this installation. Auto-approve so
    # the toolkit isn't blocked by its own legitimate ops patterns.
    if not dry_run:
        try:
            approve_plugin(_SCANNER_PLUGIN_ID, notes="auto-approved during bootstrap")
        except Exception:
            pass

    preflight_result = cast(
        dict[str, Any],
        preflight_decision(
            dry_run=dry_run,
            force_scan=force_scan,
            exclude_plugins={_SCANNER_PLUGIN_ID},
        ),
    )

    # If preflight mutated config and the caller asked for a controlled restart,
    # run it now. On failure, restore the previous config snapshot.
    restart = {
        "needed": bool(preflight_result["enforcement"]["config_written"]),
        "attempted": False,
        "succeeded": None,
        "command": restart_command or [],
        "rollback_performed": False,
        "diagnostics": "",
    }
    if restart["needed"] and restart_command:
        restart["attempted"] = True
        try:
            proc = subprocess.run(
                restart_command,
                capture_output=True,
                text=True,
                timeout=60,
            )
            restart["succeeded"] = proc.returncode == 0
            restart["diagnostics"] = (
                f"stdout:\n{getattr(proc, 'stdout', '')[-4000:]}\n"
                f"stderr:\n{getattr(proc, 'stderr', '')[-4000:]}"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            restart["succeeded"] = False
            restart["diagnostics"] = str(exc)
        if not restart["succeeded"]:
            backup_path = preflight_result["enforcement"].get("backup_path")
            if backup_path and os.path.exists(backup_path):
                _restore_hermes_config(backup_path)
                restart["rollback_performed"] = True

    payload = {
        "timestamp": timestamp,
        "hermes_ops_kit_version": _ops_kit_version(),
        "scanner_version": scanner_version,
        "scan_profile": "install",
        "paths": {
            "plugins": str(HERMES_HOME / "plugins"),
            "skills": str(HERMES_HOME / "skills"),
            "mcp_policy": str(HERMES_HOME / "mcp_policy.json"),
            "hermes_config": str(HERMES_HOME / "config.yaml"),
            "scanner_config": str(SCANNER_CONFIG_PATH),
        },
        "tools": _tool_availability(),
        "setup": {
            "created_scanner_config": setup["created"],
            "config_path": setup["path"],
            "changes": [
                "Created ~/.hermes/ops-kit/plugin_scanner.yaml"
                if setup["created"]
                else (
                    "Would create ~/.hermes/ops-kit/plugin_scanner.yaml"
                    if setup["would_create"]
                    else "Kept existing ~/.hermes/ops-kit/plugin_scanner.yaml"
                ),
            ],
        },
        "install_scan": {
            "profile": "install",
            "summary": install_summary,
            "results": [result.to_dict() for result in install_results],
        },
        "preflight": preflight_result,
        "restart": restart,
        "wrappers": wrappers,
        "docs": [
            "docs/plugin-security-scanner.md",
            "docs/plugin-security-scanner-design.md",
        ],
        "disclaimer": DISCLAIMER,
        "next_commands": [
            "hermes-ops-kit preflight --dry-run --json",
            "hermes-ops-kit plugin scan --profile manual --json --force",
            "hermes-ops-kit plugin policy",
            "hermes-ops-kit plugin approve <plugin>",
            "hermes-ops-kit plugin override <plugin> <rule> downgrade:info",
        ],
        "headless": headless,
        "dry_run": dry_run,
    }

    _write_reports(report_stem, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes Ops Kit bootstrap")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify config")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    parser.add_argument("--headless", action="store_true", help="No prompts")
    parser.add_argument("--force", action="store_true", help="Force fresh scans")
    parser.add_argument(
        "--restart-command",
        nargs="+",
        help="Optional command to restart Hermes if config changes",
    )
    args = parser.parse_args(argv)

    try:
        report = bootstrap(
            dry_run=args.dry_run,
            headless=args.headless,
            force_scan=args.force,
            restart_command=args.restart_command,
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"Bootstrap error: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(
            json.dumps(
                {
                    "ok": report["preflight"]["ok"]
                    and not report["restart"]["rollback_performed"],
                    "report": report,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("Hermes Ops Kit — First Install")
        print(f"  report: {report['report_paths']['text']}")
        print(f"  report json: {report['report_paths']['json']}")
        print(f"  scanner config: {report['setup']['config_path']}")
        print(
            f"  scanner config created: {'yes' if report['setup']['created_scanner_config'] else 'no'}"
        )
        print(
            f"  plugins scanned: {report['install_scan']['summary']['plugins_scanned']}"
        )
        print(f"  preflight enabled: {'yes' if not report['dry_run'] else 'no'}")
        print("  wrappers: " + ", ".join(report["wrappers"]))
        print(f"  blocked: {len(report['preflight']['decisions']['blocked'])}")
        print(f"  deferred: {len(report['preflight']['decisions']['deferred'])}")
        print(
            f"  disabled MCP servers: {len(report['preflight']['mcp_decisions']['disable'])}"
        )
        print("  docs:")
        for doc in report["docs"]:
            print(f"    - {doc}")
        if report["restart"]["needed"]:
            if report["restart"]["attempted"] and report["restart"]["succeeded"]:
                print("  restart: succeeded")
            elif report["restart"]["rollback_performed"]:
                print("  restart: failed, config rolled back")
            else:
                print("  restart: required")
        print("")
        print(DISCLAIMER)

    if report["preflight"]["ok"] is False:
        return 2
    if report["restart"]["succeeded"] is False and report["restart"]["attempted"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
