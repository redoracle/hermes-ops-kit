"""Hermes Ops Kit — Plugin Scanner: Enforcement Engine.

Bridges plugin scanner and MCP auditor decisions with Hermes' configuration.
Hermes loads plugins based on ``plugins.enabled`` / ``plugins.disabled`` in
``~/.hermes/config.yaml``. The scanner produces risk assessments in
``plugin_policy.json``. This module synchronizes the two: scanner decisions
are written to the Hermes config so blocked/disabled plugins are not loaded.

The enforcement runs as a **preflight check** — before Hermes boots — not
as a runtime hook (Hermes ``startup`` hooks fire after plugin loading).

Usage:
    python3 -m security.plugin_scanner.enforce [--dry-run] [--json]
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from typing import Any

# Ensure package imports resolve from ops-kit root
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from security.plugin_scanner.scanner import scan_all  # pyright: ignore[reportMissingImports]
from security.plugin_scanner.policy import is_approved  # pyright: ignore[reportMissingImports]
from security.plugin_scanner.findings import RiskLevel  # pyright: ignore[reportMissingImports]


HERMES_CONFIG_PATH = os.path.expanduser("~/.hermes/config.yaml")


# ── Config read/write ────────────────────────────────────────────────────


def _parse_hermes_config(path: str) -> dict[str, Any]:
    """Parse a Hermes YAML config, failing closed on malformed content."""
    if not os.path.exists(path):
        return {}
    try:
        # Use the YAML library available in the project
        from ruamel.yaml import YAML  # pyright: ignore[reportMissingImports]

        yaml = YAML()
        yaml.preserve_quotes = True
        with open(path) as f:
            config = yaml.load(f) or {}
    except ImportError:
        try:
            import yaml  # type: ignore[import-untyped]

            with open(path) as f:
                config = yaml.safe_load(f) or {}
        except Exception as exc:
            raise RuntimeError(f"Cannot parse Hermes config: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Cannot parse Hermes config: {exc}") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Hermes config root must be a mapping")
    return config


def _load_hermes_config() -> dict[str, Any]:
    """Load ~/.hermes/config.yaml, failing closed on malformed content."""
    return _parse_hermes_config(HERMES_CONFIG_PATH)


def _save_hermes_config(config: dict[str, Any]) -> None:
    """Durably and atomically write ~/.hermes/config.yaml with mode 0600."""
    config_dir = os.path.dirname(HERMES_CONFIG_PATH)
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=config_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            try:
                from ruamel.yaml import YAML  # pyright: ignore[reportMissingImports]

                yaml = YAML()
                yaml.preserve_quotes = True
                yaml.dump(config, f)
            except ImportError:
                import yaml  # type: ignore[import-untyped]

                yaml.safe_dump(config, f, default_flow_style=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, HERMES_CONFIG_PATH)
        os.chmod(HERMES_CONFIG_PATH, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _backup_hermes_config() -> str | None:
    """Create a rollback backup of the current Hermes config if present."""
    if not os.path.exists(HERMES_CONFIG_PATH):
        return None
    backup_path = f"{HERMES_CONFIG_PATH}.bak"
    shutil.copy2(HERMES_CONFIG_PATH, backup_path)
    try:
        os.chmod(backup_path, 0o600)
    except OSError:
        pass
    return backup_path


def _restore_hermes_config(backup_path: str) -> None:
    """Atomically restore the Hermes config from a backup file."""
    _parse_hermes_config(backup_path)
    config_dir = os.path.dirname(HERMES_CONFIG_PATH)
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".config-restore-", suffix=".yaml", dir=config_dir
    )
    try:
        os.fchmod(fd, 0o600)
        with open(backup_path, "rb") as source, os.fdopen(fd, "wb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, HERMES_CONFIG_PATH)
        os.chmod(HERMES_CONFIG_PATH, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


# ── Enforcement logic ────────────────────────────────────────────────────


def _result_approval(result: Any) -> tuple[bool, str]:
    """Resolve plugin, finding, and category approvals for a scan result."""
    approved, reason = is_approved(result.plugin_name)
    if approved or reason in ("plugin_blocked", "plugin_disabled"):
        return approved, reason

    findings = getattr(result, "findings", [])
    errors = getattr(result, "errors", [])
    if not findings or errors:
        return False, reason

    finding_reasons: set[str] = set()
    for finding in findings:
        finding_approved, finding_reason = is_approved(
            result.plugin_name,
            finding_id=getattr(finding, "id", ""),
            category=getattr(finding, "category", ""),
        )
        if not finding_approved:
            return False, "not_approved"
        finding_reasons.add(finding_reason)

    if finding_reasons == {"category_approved"}:
        return True, "all_findings_category_approved"
    return True, "all_findings_approved"


def get_enforcement_decisions(
    results: list[Any] | None = None,
) -> dict[str, Any]:
    """Compute which plugins should be enabled/disabled based on scan results.

    Combines scanner risk levels with policy approvals to produce a set
    of decisions that can be applied to ``~/.hermes/config.yaml``.

    Args:
        results: Scan results from ``scan_all()``. If None, runs a fresh
                 scan with the ``startup`` profile (uses cache, fast).

    Returns:
        {
            "ok": bool,            # False if any CRITICAL plugin is not
                                   # explicitly blocked — Hermes should not boot
            "enforce": [str],     # Plugins to add to plugins.enabled
            "disable": [str],     # Plugins to add to plugins.disabled
            "blocked": [str],     # Plugins that are CRITICAL (should stop boot)
            "allowed": [str],     # Plugins that are NONE/LOW risk
            "approved": [str],    # Plugins explicitly approved by operator
            "deferred": [str],    # MEDIUM/HIGH unapproved — disabled by default
            "details": {str: str},  # plugin_name → human-readable reason
            "scan_duration_ms": int,
        }
    """
    if results is None:
        import time

        start = time.time()
        results = scan_all(profile="startup", force=False)
        duration_ms = int((time.time() - start) * 1000)
    else:
        duration_ms = 0

    enabled_list: list[str] = []
    disable_list: list[str] = []
    blocked_list: list[str] = []
    allowed_list: list[str] = []
    approved_list: list[str] = []
    deferred_list: list[str] = []
    details: dict[str, str] = {}
    has_critical_unblocked = False

    for r in results:
        name = r.plugin_name
        risk = r.risk_level
        approved, reason = _result_approval(r)

        if approved:
            approved_list.append(name)

        if reason == "plugin_blocked":
            blocked_list.append(name)
            details[name] = "Explicitly blocked by operator policy"
            has_critical_unblocked = True
            continue
        if reason == "plugin_disabled":
            disable_list.append(name)
            deferred_list.append(name)
            details[name] = "Explicitly disabled by operator policy"
            continue

        if risk == RiskLevel.CRITICAL:
            blocked_list.append(name)
            details[name] = "CRITICAL risk — blocked from loading"
            has_critical_unblocked = True

        elif risk == RiskLevel.HIGH:
            if approved:
                enabled_list.append(name)
                details[name] = f"HIGH risk but explicitly approved ({reason})"
            else:
                disable_list.append(name)
                deferred_list.append(name)
                details[name] = "HIGH risk — requires approval to load"

        elif risk == RiskLevel.MEDIUM:
            if approved:
                enabled_list.append(name)
                details[name] = f"MEDIUM risk but explicitly approved ({reason})"
            else:
                disable_list.append(name)
                deferred_list.append(name)
                details[name] = "MEDIUM risk — requires approval to load"

        elif risk in (RiskLevel.NONE, RiskLevel.LOW):
            allowed_list.append(name)
            enabled_list.append(name)
            details[name] = f"{risk.value.upper()} risk — allowed"

    return {
        "ok": not has_critical_unblocked,
        "enforce": sorted(enabled_list),
        "disable": sorted(disable_list),
        "blocked": sorted(blocked_list),
        "allowed": sorted(allowed_list),
        "approved": sorted(approved_list),
        "deferred": sorted(deferred_list),
        "details": details,
        "scan_duration_ms": duration_ms,
    }


def get_mcp_enforcement_decisions(
    audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute server-level MCP enforcement from objective tool audit results.

    Hermes currently exposes only a server-level ``enabled`` switch, so one
    unsafe tool disables the whole server. Critical tools always block boot.
    """
    if audit is None:
        from mcp_auditor.auditor import run_audit  # pyright: ignore[reportMissingImports]

        audit = run_audit(dynamic_discovery=False)
    if not audit.get("ok"):
        raise RuntimeError("MCP audit failed")

    allowed: list[str] = []
    approved: list[str] = []
    disable: list[str] = []
    blocked: list[str] = []
    details: dict[str, str] = {}

    for server in audit.get("servers", []):
        server_id = server.get("server_id")
        if not isinstance(server_id, str) or not server_id:
            raise RuntimeError("MCP audit returned a server without a valid id")
        tools = server.get("tools", [])
        if not isinstance(tools, list):
            raise RuntimeError(f"MCP audit returned invalid tools for '{server_id}'")

        if not tools:
            disable.append(server_id)
            details[server_id] = "Tool discovery failed or returned no tools"
            continue

        critical = [t for t in tools if t.get("risk") == "critical"]
        unapproved_risky = [
            t
            for t in tools
            if (
                t.get("risk") in ("medium", "high")
                or t.get("injection_risk") in ("medium", "high")
            )
            and not t.get("approved", False)
        ]
        approved_risky = [
            t
            for t in tools
            if (
                t.get("risk") in ("medium", "high")
                or t.get("injection_risk") in ("medium", "high")
            )
            and t.get("approved", False)
        ]

        if critical:
            blocked.append(server_id)
            names = ", ".join(t.get("tool_name", "?") for t in critical[:3])
            details[server_id] = f"CRITICAL MCP tools block boot: {names}"
        elif unapproved_risky:
            disable.append(server_id)
            names = ", ".join(t.get("tool_name", "?") for t in unapproved_risky[:3])
            details[server_id] = f"MEDIUM/HIGH MCP tools require approval: {names}"
        else:
            allowed.append(server_id)
            if approved_risky:
                approved.append(server_id)
                details[server_id] = "MEDIUM/HIGH MCP tools explicitly approved"
            else:
                details[server_id] = "No unapproved MEDIUM/HIGH or CRITICAL MCP tools"

    return {
        "ok": not blocked,
        "allowed": sorted(allowed),
        "approved": sorted(approved),
        "disable": sorted(disable),
        "blocked": sorted(blocked),
        "details": details,
    }


# ── Config sync ──────────────────────────────────────────────────────────


def apply_enforcement(
    decisions: dict[str, Any],
    *,
    mcp_decisions: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Apply enforcement decisions to ``~/.hermes/config.yaml``.

    Updates ``plugins.enabled`` and ``plugins.disabled`` to match scanner
    decisions. Existing entries not related to scanned plugins are preserved.

    Args:
        decisions: Output from ``get_enforcement_decisions()``.
        dry_run: If True, compute changes but don't write.

    Returns:
        {
            "applied": bool,           # Whether changes were written
            "dry_run": bool,
            "changes": {               # What would change / did change
                "enabled_added": [str],
                "enabled_removed": [str],
                "disabled_added": [str],
                "disabled_removed": [str],
                "mcp_disabled": [str],
            },
            "config_written": bool,
            "backup_path": str | None,
        }
    """
    config = _load_hermes_config()
    plugins_cfg = config.setdefault("plugins", {})
    if not isinstance(plugins_cfg, dict):
        raise RuntimeError("Hermes config 'plugins' must be a mapping")

    current_enabled = plugins_cfg.get("enabled") or []
    current_disabled = plugins_cfg.get("disabled") or []
    if not isinstance(current_enabled, list) or not all(
        isinstance(item, str) for item in current_enabled
    ):
        raise RuntimeError("Hermes config 'plugins.enabled' must be a list of strings")
    if not isinstance(current_disabled, list) or not all(
        isinstance(item, str) for item in current_disabled
    ):
        raise RuntimeError("Hermes config 'plugins.disabled' must be a list of strings")

    # The scanner only manages plugins it scanned — don't touch
    # hand-curated entries the operator added manually.
    scanned_plugins = set(
        decisions["enforce"] + decisions["disable"] + decisions["blocked"]
    )

    # Hermes plugin loading is opt-in. Preflight may remove unsafe plugins
    # from enabled, but it must never auto-enable newly discovered plugins.
    want_enabled = (
        set(current_enabled) - set(decisions["disable"]) - set(decisions["blocked"])
    )
    want_disabled = set(decisions["disable"]) | set(decisions["blocked"])

    # Compute delta for scanned plugins only
    enabled_added = sorted(want_enabled - set(current_enabled))
    enabled_removed = sorted(
        p for p in current_enabled if p in scanned_plugins and p not in want_enabled
    )
    disabled_added = sorted(want_disabled - set(current_disabled))
    disabled_removed = sorted(
        p for p in current_disabled if p in scanned_plugins and p not in want_disabled
    )

    changes = {
        "enabled_added": enabled_added,
        "enabled_removed": enabled_removed,
        "disabled_added": disabled_added,
        "disabled_removed": disabled_removed,
        "mcp_disabled": [],
    }

    mcp_servers = config.get("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        raise RuntimeError("Hermes config 'mcp_servers' must be a mapping")
    if mcp_decisions is not None:
        unsafe_servers = set(mcp_decisions["disable"]) | set(mcp_decisions["blocked"])
        for server_id in sorted(unsafe_servers):
            server_cfg = mcp_servers.get(server_id)
            if not isinstance(server_cfg, dict):
                raise RuntimeError(
                    f"Hermes config MCP server '{server_id}' must be a mapping"
                )
            if server_cfg.get("enabled", True):
                changes["mcp_disabled"].append(server_id)
    has_changes = any(v for v in changes.values())

    if dry_run:
        return {
            "applied": False,
            "dry_run": True,
            "changes": changes,
            "config_written": False,
            "backup_path": None,
        }

    if has_changes:
        # Build new lists
        new_enabled = [
            p for p in current_enabled if p not in enabled_removed
        ] + enabled_added
        new_disabled = [
            p for p in current_disabled if p not in disabled_removed
        ] + disabled_added

        backup_path = _backup_hermes_config()
        plugins_cfg["enabled"] = new_enabled
        plugins_cfg["disabled"] = new_disabled
        for server_id in changes["mcp_disabled"]:
            mcp_servers[server_id]["enabled"] = False

        _save_hermes_config(config)
        return {
            "applied": True,
            "dry_run": False,
            "changes": changes,
            "config_written": True,
            "backup_path": backup_path,
        }

    return {
        "applied": False,
        "dry_run": False,
        "changes": changes,
        "config_written": False,
        "backup_path": None,
    }


# ── CLI entry point ──────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Run enforcement as a standalone command.

    Exit codes:
        0 — no blocked plugins, boot is safe
        2 — blocked plugins found, boot should be stopped
        3 — scan or enforcement error
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Hermes Ops Kit — Preflight Plugin Security Enforcement"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview enforcement without modifying config",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force fresh scan (skip cache)",
    )
    parser.add_argument(
        "--restore-config",
        metavar="BACKUP",
        help="Validate and atomically restore Hermes config from BACKUP",
    )
    args = parser.parse_args(argv)

    try:
        if args.restore_config:
            _restore_hermes_config(args.restore_config)
            if args.json:
                print(json.dumps({"ok": True, "restored": args.restore_config}))
            else:
                print(f"Restored Hermes config from {args.restore_config}")
            return 0

        # 1. Scan
        import time

        start = time.time()
        results = scan_all(profile="startup", force=args.force)
        from mcp_auditor.auditor import run_audit  # pyright: ignore[reportMissingImports]

        mcp_audit = run_audit(dynamic_discovery=False)
        scan_ms = int((time.time() - start) * 1000)

        # 2. Decide
        decisions = get_enforcement_decisions(results)
        mcp_decisions = get_mcp_enforcement_decisions(mcp_audit)
        decisions["scan_duration_ms"] = scan_ms

        # 3. Apply
        enforcement = apply_enforcement(
            decisions, mcp_decisions=mcp_decisions, dry_run=args.dry_run
        )

        if args.json:
            output = {
                "ok": decisions["ok"] and mcp_decisions["ok"],
                "scan_duration_ms": scan_ms,
                "plugins_scanned": len(results),
                "decisions": {
                    "allowed": decisions["allowed"],
                    "approved": decisions["approved"],
                    "deferred": decisions["deferred"],
                    "blocked": decisions["blocked"],
                },
                "mcp_decisions": mcp_decisions,
                "enforcement": enforcement,
                "details": decisions["details"],
            }
            print(json.dumps(output, indent=2, sort_keys=True))
        else:
            _print_summary(decisions, enforcement, mcp_decisions)

        return 2 if not decisions["ok"] or not mcp_decisions["ok"] else 0

    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "error": str(exc)},
                    indent=2,
                )
            )
        else:
            print(f"Preflight error: {exc}", file=sys.stderr)
        return 3


def _print_summary(
    decisions: dict[str, Any],
    enforcement: dict[str, Any],
    mcp_decisions: dict[str, Any] | None = None,
) -> None:
    """Human-readable preflight output."""
    allowed = set(decisions["allowed"])
    approved_exceptions = (
        set(decisions["approved"]) & set(decisions["enforce"])
    ) - allowed

    print("Hermes Ops Kit — Preflight Plugin Security")
    print(f"  Scanned:   {len(decisions['details'])} plugins")
    print(f"  Allowed:   {len(allowed)} (NONE/LOW risk)")
    print(
        f"  Approved:  {len(approved_exceptions)} "
        "(MEDIUM/HIGH risk, explicitly approved by operator)"
    )
    print(
        f"  Deferred:  {len(decisions['deferred'])} (MEDIUM/HIGH risk, requires approval)"
    )
    print(f"  Blocked:   {len(decisions['blocked'])} (CRITICAL risk)")
    if mcp_decisions is not None:
        print(f"  MCP safe:  {len(mcp_decisions['allowed'])}")
        print(f"  MCP off:   {len(mcp_decisions['disable'])}")
        print(f"  MCP block: {len(mcp_decisions['blocked'])}")

    if decisions["blocked"]:
        print("\n  ⛔ BLOCKED PLUGINS — Hermes boot should be aborted:")
        for name in decisions["blocked"]:
            print(f"     {name}: {decisions['details'][name]}")

    if decisions["deferred"]:
        print("\n  ⚠️  DEFERRED PLUGINS — added to plugins.disabled:")
        for name in decisions["deferred"]:
            print(f"     {name}: {decisions['details'][name]}")

    changes = enforcement["changes"]
    if any(v for v in changes.values()):
        if enforcement["dry_run"]:
            print("\n  [DRY RUN] Would apply changes to ~/.hermes/config.yaml:")
        else:
            print("\n  Applied changes to ~/.hermes/config.yaml:")
        if changes["enabled_added"]:
            print(f"    + plugins.enabled: {changes['enabled_added']}")
        if changes["enabled_removed"]:
            print(f"    - plugins.enabled: {changes['enabled_removed']}")
        if changes["disabled_added"]:
            print(f"    + plugins.disabled: {changes['disabled_added']}")
        if changes["disabled_removed"]:
            print(f"    - plugins.disabled: {changes['disabled_removed']}")
        if changes["mcp_disabled"]:
            print(f"    - mcp_servers enabled: {changes['mcp_disabled']}")
    else:
        print("\n  Config is already synchronized — no changes needed")

    if decisions["ok"] and (mcp_decisions is None or mcp_decisions["ok"]):
        print("\n  Preflight passed — safe to boot Hermes")
    else:
        print(
            "\n  Preflight FAILED — do not boot Hermes until blocked plugins are resolved"
        )


if __name__ == "__main__":
    raise SystemExit(main())
