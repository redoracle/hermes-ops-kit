"""Hermes Ops Kit — Plugin Scanner: CLI Handler.

Provides the CLI subcommand dispatch for `hermes-ops-kit plugin ...`.
Called from commands.py:_handle_plugin().
"""

from __future__ import annotations

import os
from typing import Any

# Ensure package imports resolve

from ...ui.json_output import ok_envelope, error_envelope, error_item  # pyright: ignore[reportMissingImports]
from ...ui.console import Console  # pyright: ignore[reportMissingImports]
from ...security.plugin_scanner.scanner import (  # pyright: ignore[reportMissingImports]
    scan_plugin,
    scan_all,
)
from ...security.plugin_scanner.cache import cache_stats, cache_list, cache_clear  # pyright: ignore[reportMissingImports]
from ...security.plugin_scanner.policy import (  # pyright: ignore[reportMissingImports]
    approve_plugin,
    approve_finding,
    approve_category,
    approve_all,
    revoke_plugin,
    revoke_finding,
    revoke_all,
    disable_plugin,
    enable_plugin,
    block_plugin,
    get_policy,
    set_rule_override,
    remove_rule_override,
    get_rule_overrides,
    get_plugin_status,
)
from hermes_ops_kit import ops_config_io  # noqa: E402


def handle_plugin(args: list[str]) -> int:
    """Dispatch `hermes-ops-kit plugin <subcommand> [...]`.

    Returns exit code.
    """
    if not args:
        return _plugin_usage()

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "scan":
        return _cmd_scan(rest)
    elif subcmd == "approve":
        return _cmd_approve(rest)
    elif subcmd == "override":
        return _cmd_override(rest)
    elif subcmd == "revoke":
        return _cmd_revoke(rest)
    elif subcmd == "disable":
        return _cmd_disable(rest)
    elif subcmd == "enable":
        return _cmd_enable(rest)
    elif subcmd == "block":
        return _cmd_block(rest)
    elif subcmd == "policy":
        return _cmd_policy(rest)
    elif subcmd == "cache":
        return _cmd_cache(rest)
    elif subcmd == "rules":
        return _cmd_rules(rest)
    else:
        print(f"Unknown plugin subcommand: {subcmd}")
        return _plugin_usage()


def _plugin_usage() -> int:
    print("Hermes Ops Kit — Plugin Security Scanner")
    print()
    print("  hermes-ops-kit plugin scan [--profile <name>] [--category <cats>]")
    print("  hermes-ops-kit plugin scan --plugin <name-or-path> [--force] [--json]")
    print("  hermes-ops-kit plugin scan --use-bandit --use-gitleaks --use-semgrep")
    print("  hermes-ops-kit plugin scan --no-bandit  # disable external tools")
    print("  hermes-ops-kit plugin approve <plugin>")
    print("  hermes-ops-kit plugin approve <plugin> --category <category>")
    print("  hermes-ops-kit plugin approve --finding <finding-id>")
    print("  hermes-ops-kit plugin approve --all")
    print("  hermes-ops-kit plugin override <plugin> <rule> <action>")
    print("  hermes-ops-kit plugin override <plugin> --remove [--rule <rule>]")
    print("  hermes-ops-kit plugin override --show [<plugin>]")
    print("  hermes-ops-kit plugin revoke <plugin>")
    print("  hermes-ops-kit plugin revoke --finding <finding-id>")
    print("  hermes-ops-kit plugin revoke --all")
    print("  hermes-ops-kit plugin disable <plugin>")
    print("  hermes-ops-kit plugin enable <plugin>")
    print("  hermes-ops-kit plugin block <plugin>")
    print("  hermes-ops-kit plugin policy [--json]")
    print("  hermes-ops-kit plugin cache show")
    print("  hermes-ops-kit plugin cache clear")
    print("  hermes-ops-kit plugin rules update")
    print()
    print("Scan profiles: startup, install, update, manual, ci")
    print("Scan categories: secrets, policy (MVP)")
    print("Optional tools:  semgrep, gitleaks, bandit (graceful degradation)")
    return 1


# ── Scan Command ───────────────────────────────────────────────────


def _cmd_scan(args: list[str]) -> int:
    """Handle `plugin scan` subcommand."""
    json_mode = "--json" in args
    force = "--force" in args
    profile = "startup"
    plugin_name: str | None = None
    categories_str: str | None = None
    use_semgrep: bool | None = None
    use_bandit: bool | None = None
    use_gitleaks: bool | None = None

    # Parse arguments
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--profile" and i + 1 < len(args):
            profile = args[i + 1]
            i += 2
        elif a == "--plugin" and i + 1 < len(args):
            plugin_name = args[i + 1]
            i += 2
        elif a == "--category" and i + 1 < len(args):
            categories_str = args[i + 1]
            i += 2
        elif a == "--use-semgrep":
            use_semgrep = True
            i += 1
        elif a == "--no-semgrep":
            use_semgrep = False
            i += 1
        elif a == "--use-bandit":
            use_bandit = True
            i += 1
        elif a == "--no-bandit":
            use_bandit = False
            i += 1
        elif a == "--use-gitleaks":
            use_gitleaks = True
            i += 1
        elif a == "--no-gitleaks":
            use_gitleaks = False
            i += 1
        elif a in ("--json", "--force"):
            i += 1
        else:
            i += 1

    # Resolve categories
    categories: list[str] | None = None
    if categories_str:
        categories = [c.strip() for c in categories_str.split(",") if c.strip()]

    # Build scanner kwargs from CLI flags
    scan_kwargs: dict[str, Any] = {}
    if use_semgrep is not None:
        scan_kwargs["use_semgrep"] = use_semgrep
    if use_bandit is not None:
        scan_kwargs["use_bandit"] = use_bandit
    if use_gitleaks is not None:
        scan_kwargs["use_gitleaks"] = use_gitleaks

    console = Console(json_mode=json_mode)

    try:
        if plugin_name:
            # Single plugin scan — resolve path
            plugin_path = _resolve_plugin_path(plugin_name)
            if not plugin_path:
                if json_mode:
                    console.print_json(
                        error_envelope(
                            "plugin_scan",
                            [
                                error_item(
                                    "NOT_FOUND", f"Plugin not found: {plugin_name}"
                                )
                            ],
                        )
                    )
                else:
                    console.print_error(f"Plugin not found: {plugin_name}")
                return 1

            result = scan_plugin(
                plugin_name=os.path.basename(plugin_path),
                plugin_path=plugin_path,
                categories=categories,
                profile=profile,
                force=force,
                **scan_kwargs,
            )
            results = [result]
        else:
            # Scan all plugins
            results = scan_all(
                categories=categories,
                profile=profile,
                force=force,
                **scan_kwargs,
            )

        policy_statuses = [_scan_policy_status(r) for r in results]

        if json_mode:
            console.print_json(
                ok_envelope(
                    "plugin_scan",
                    {
                        "profile": profile,
                        "plugins": [r.to_dict() for r in results],
                    },
                )
            )
        else:
            _print_scan_results(results, policy_statuses, console)
            statuses = [status["status"] for status in policy_statuses]
            enabled_count = statuses.count("enabled")
            disabled_count = statuses.count("disabled")
            blocked_count = statuses.count("blocked")
            console.print(
                f"\n  Enabled: {enabled_count}  |  Disabled: {disabled_count}  |  Blocked: {blocked_count}"
            )

        # Return non-zero if effective scanner policy blocks any plugin.
        return 1 if any(s["status"] == "blocked" for s in policy_statuses) else 0

    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope(
                    "plugin_scan",
                    [error_item("SCAN_ERROR", str(exc))],
                )
            )
        else:
            console.print_error(f"Scan failed: {exc}")
        return 1


def _print_scan_results(
    results: list[Any], policy_statuses: list[dict[str, Any]], console: Console
) -> None:
    """Pretty-print scan results."""
    for r, policy_status in zip(results, policy_statuses, strict=True):
        risk_icon = {
            "none": "✅",
            "low": "ℹ️ ",
            "medium": "⚠️ ",
            "high": "🔴",
            "critical": "⛔",
        }.get(r.risk_level.value, "❓")

        if policy_status["status"] == "enabled":
            status = (
                "ENABLED ✓"
                if policy_status["reason"] == "plugin_approved"
                else "ENABLED"
            )
        elif policy_status["status"] == "blocked":
            status = "BLOCKED ✗"
        elif policy_status["status"] == "disabled":
            status = "DISABLED"
        else:
            status = policy_status["status"].upper()

        cache_tag = " [cache]" if r.cache_hit else ""
        console.print(
            f"{risk_icon} {r.plugin_name:<30} {r.risk_level.value.upper():<10} "
            f"{status:<12} ({len(r.findings)} findings){cache_tag}"
        )

        if r.cache_hit:
            continue

        for f in r.findings[:5]:  # Show first 5 findings
            sev_icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(
                f.severity.value if hasattr(f.severity, "value") else f.severity, "⚪"
            )
            console.print(f"   {sev_icon} [{f.rule}] {f.message[:100]}")
            if hasattr(f, "file_path") and f.file_path:
                location = f"{f.file_path}"
                if hasattr(f, "line") and f.line:
                    location += f":{f.line}"
                console.print(f"      at {location}")

        if len(r.findings) > 5:
            console.print(f"   ... and {len(r.findings) - 5} more findings")


def _scan_policy_status(result: Any) -> dict[str, Any]:
    """Return the scanner policy status implied by a scan result."""
    return get_plugin_status(result.plugin_name, result.risk_level.value)


# ── Approve Command ─────────────────────────────────────────────────


def _cmd_approve(args: list[str]) -> int:
    """Handle `plugin approve` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    # Parse flags
    finding_id: str | None = None
    category: str | None = None
    all_plugins = False
    plugin_name: str | None = None
    notes = ""

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--finding" and i + 1 < len(args):
            finding_id = args[i + 1]
            i += 2
        elif a == "--category" and i + 1 < len(args):
            category = args[i + 1]
            i += 2
        elif a == "--all":
            all_plugins = True
            i += 1
        elif a == "--notes" and i + 1 < len(args):
            notes = args[i + 1]
            i += 2
        elif a == "--json":
            i += 1
        elif not a.startswith("--"):
            plugin_name = a
            i += 1
        else:
            i += 1

    try:
        if finding_id:
            policy = approve_finding(finding_id, notes=notes)
        elif all_plugins:
            policy = approve_all(notes=notes)
        elif plugin_name and category:
            policy = approve_category(plugin_name, category, notes=notes)
        elif plugin_name:
            policy = approve_plugin(plugin_name, notes=notes)
        else:
            console.print_error(
                "Specify a plugin name, --finding, --all, or --category"
            )
            return 1

        if json_mode:
            console.print_json(ok_envelope("plugin_approve", policy))
        else:
            console.print("Plugin policy updated.")
        return 0
    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope("plugin_approve", [error_item("ERROR", str(exc))])
            )
        else:
            console.print_error(f"Approve failed: {exc}")
        return 1


# ── Override Command ─────────────────────────────────────────────────


def _cmd_override(args: list[str]) -> int:
    """Handle `plugin override` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    remove_mode = "--remove" in args
    show_mode = "--show" in args
    plugin_name: str | None = None
    rule: str | None = None
    action: str | None = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--rule" and i + 1 < len(args):
            rule = args[i + 1]
            i += 2
        elif a in ("--remove", "--show", "--json"):
            i += 1
        elif not a.startswith("--") and plugin_name is None:
            plugin_name = a
            i += 1
        elif not a.startswith("--") and rule is None:
            rule = a
            i += 1
        elif not a.startswith("--") and action is None:
            action = a
            i += 1
        else:
            i += 1

    try:
        if show_mode:
            if plugin_name:
                overrides = get_rule_overrides(plugin_name)
                if json_mode:
                    console.print_json(
                        ok_envelope(
                            "plugin_override_show",
                            {"plugin": plugin_name, "overrides": overrides},
                        )
                    )
                else:
                    console.print(f"Rule overrides for '{plugin_name}':")
                    if overrides:
                        for r, a in sorted(overrides.items()):
                            console.print(f"  {r}: {a}")
                    else:
                        console.print("  (none)")
            else:
                policy = get_policy()
                all_overrides = policy.get("rule_overrides", {})
                if json_mode:
                    console.print_json(
                        ok_envelope(
                            "plugin_override_show",
                            {"overrides": all_overrides},
                        )
                    )
                else:
                    console.print("All rule overrides:")
                    if all_overrides:
                        for pname, rules in sorted(all_overrides.items()):
                            console.print(f"  {pname}:")
                            for r, a in sorted(rules.items()):
                                console.print(f"    {r}: {a}")
                    else:
                        console.print("  (none)")
            return 0

        if remove_mode:
            if not plugin_name:
                console.print_error("Specify a plugin name to remove overrides for")
                return 1
            policy = remove_rule_override(plugin_name, rule)
            if json_mode:
                console.print_json(ok_envelope("plugin_override_remove", policy))
            else:
                target = f"rule '{rule}' for" if rule else "all rules for"
                console.print(f"Override removed: {target} plugin '{plugin_name}'.")
            return 0

        # Set mode
        if not plugin_name or not rule or not action:
            console.print_error(
                "Usage: plugin override <plugin> <rule> <action>\n"
                "       plugin override <plugin> --remove [--rule <rule>]\n"
                "       plugin override --show [<plugin>]"
            )
            console.print("Actions: allow, skip, downgrade:warning, downgrade:info")
            return 1

        policy = set_rule_override(plugin_name, rule, action)
        if json_mode:
            console.print_json(ok_envelope("plugin_override_set", policy))
        else:
            console.print(f"Rule override: '{plugin_name}' / '{rule}' → {action}")
        return 0

    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope("plugin_override", [error_item("ERROR", str(exc))])
            )
        else:
            console.print_error(f"Override failed: {exc}")
        return 1


# ── Revoke Command ──────────────────────────────────────────────────


def _cmd_revoke(args: list[str]) -> int:
    """Handle `plugin revoke` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    finding_id: str | None = None
    all_plugins = False
    plugin_name: str | None = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--finding" and i + 1 < len(args):
            finding_id = args[i + 1]
            i += 2
        elif a == "--all":
            all_plugins = True
            i += 1
        elif a == "--json":
            i += 1
        elif not a.startswith("--"):
            plugin_name = a
            i += 1
        else:
            i += 1

    try:
        if finding_id:
            policy = revoke_finding(finding_id)
        elif all_plugins:
            policy = revoke_all()
        elif plugin_name:
            policy = revoke_plugin(plugin_name)
        else:
            console.print_error("Specify a plugin name, --finding, or --all")
            return 1

        if json_mode:
            console.print_json(ok_envelope("plugin_revoke", policy))
        else:
            console.print("Approval revoked.")
        return 0
    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope("plugin_revoke", [error_item("ERROR", str(exc))])
            )
        else:
            console.print_error(f"Revoke failed: {exc}")
        return 1


# ── Disable / Enable Commands ────────────────────────────────────────


def _cmd_disable(args: list[str]) -> int:
    """Handle `plugin disable` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    plugin_name = None
    for a in args:
        if not a.startswith("--"):
            plugin_name = a
            break

    if not plugin_name:
        console.print_error("Specify a plugin name to disable")
        return 1

    try:
        policy = disable_plugin(plugin_name)
        if json_mode:
            console.print_json(ok_envelope("plugin_disable", policy))
        else:
            console.print(f"Plugin '{plugin_name}' disabled.")
        return 0
    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope("plugin_disable", [error_item("ERROR", str(exc))])
            )
        else:
            console.print_error(f"Disable failed: {exc}")
        return 1


def _cmd_enable(args: list[str]) -> int:
    """Handle `plugin enable` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    plugin_name = None
    for a in args:
        if not a.startswith("--"):
            plugin_name = a
            break

    if not plugin_name:
        console.print_error("Specify a plugin name to enable")
        return 1

    try:
        policy = enable_plugin(plugin_name)
        if json_mode:
            console.print_json(ok_envelope("plugin_enable", policy))
        else:
            console.print(f"Plugin '{plugin_name}' enabled.")
        return 0
    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope("plugin_enable", [error_item("ERROR", str(exc))])
            )
        else:
            console.print_error(f"Enable failed: {exc}")
        return 1


# ── Block Command ────────────────────────────────────────────────────


def _cmd_block(args: list[str]) -> int:
    """Handle `plugin block` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    plugin_name = None
    reason = ""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--reason" and i + 1 < len(args):
            reason = args[i + 1]
            i += 2
        elif a == "--json":
            i += 1
        elif not a.startswith("--"):
            plugin_name = a
            i += 1
        else:
            i += 1

    if not plugin_name:
        console.print_error("Specify a plugin name to block")
        return 1

    try:
        policy = block_plugin(plugin_name, reason=reason)
        if json_mode:
            console.print_json(ok_envelope("plugin_block", policy))
        else:
            console.print(f"Plugin '{plugin_name}' permanently blocked.")
        return 0
    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope("plugin_block", [error_item("ERROR", str(exc))])
            )
        else:
            console.print_error(f"Block failed: {exc}")
        return 1


# ── Rules Command ────────────────────────────────────────────────────


def _cmd_rules(args: list[str]) -> int:
    """Handle `plugin rules` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    subcmd = args[0] if args else "update"

    if subcmd == "update":
        try:
            # Check available tools
            semgrep_ok = _check_semgrep()
            gitleaks_ok = _check_gitleaks()
            bandit_ok = _check_bandit()

            result = {
                "semgrep_available": semgrep_ok,
                "gitleaks_available": gitleaks_ok,
                "bandit_available": bandit_ok,
                "custom_rules": _list_custom_rules(),
                "message": "Custom rules are bundled with the scanner. "
                "Run `semgrep --config security/plugin_scanner/rules/` to use them.",
            }

            if json_mode:
                console.print_json(ok_envelope("plugin_rules_update", result))
            else:
                console.print("Plugin Scanner Rules")
                console.print(
                    f"  Semgrep:  {'available' if semgrep_ok else 'not installed'}"
                )
                console.print(
                    f"  gitleaks: {'available' if gitleaks_ok else 'not installed'}"
                )
                console.print(
                    f"  Bandit:   {'available' if bandit_ok else 'not installed'}"
                )
                console.print(f"  Custom rules: {len(result['custom_rules'])} files")
                for r in result["custom_rules"]:
                    console.print(f"    - {r}")
                if semgrep_ok:
                    console.print(
                        "\n  To update community rules: semgrep --config p/security --config p/secrets"
                    )
            return 0
        except Exception as exc:
            if json_mode:
                console.print_json(
                    error_envelope(
                        "plugin_rules_update", [error_item("ERROR", str(exc))]
                    )
                )
            else:
                console.print_error(f"Rules update failed: {exc}")
            return 1
    else:
        console.print_error(f"Unknown rules subcommand: {subcmd}")
        return 1


def _check_semgrep() -> bool:
    """Check if semgrep is available."""
    import subprocess as _sp

    try:
        r = _sp.run(
            ["semgrep", "--version"], capture_output=True, text=True, timeout=10
        )
        return r.returncode == 0
    except Exception:
        return False


def _check_gitleaks() -> bool:
    """Check if gitleaks is available."""
    import subprocess as _sp

    try:
        r = _sp.run(["gitleaks", "version"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _check_bandit() -> bool:
    """Check if bandit is available."""
    import subprocess as _sp

    try:
        r = _sp.run(["bandit", "--version"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _list_custom_rules() -> list[str]:
    """List bundled custom rule files."""
    import os as _os

    rules_dir = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "rules",
    )
    if _os.path.isdir(rules_dir):
        return sorted(f for f in _os.listdir(rules_dir) if f.endswith(".yaml"))
    return []


# ── Policy Command ───────────────────────────────────────────────────


def _cmd_policy(args: list[str]) -> int:
    """Handle `plugin policy` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    try:
        policy = get_policy()
        if json_mode:
            console.print_json(ok_envelope("plugin_policy", policy))
        else:
            console.print("Plugin Security Policy")
            console.print(
                f"  Approved plugins:    {len(policy.get('approved_plugins', []))}"
            )
            console.print(
                f"  Approved findings:   {len(policy.get('approved_findings', []))}"
            )
            console.print(
                f"  Approved categories: {len(policy.get('approved_categories', []))}"
            )
            console.print(
                f"  Disabled plugins:    {len(policy.get('disabled_plugins', []))}"
            )
            console.print(
                f"  Blocked plugins:     {len(policy.get('blocked_plugins', []))}"
            )

            overrides = policy.get("rule_overrides", {})
            total_overrides = sum(len(rules) for rules in overrides.values())
            console.print(
                f"  Rule overrides:      {total_overrides} ({len(overrides)} plugins)"
            )

            if policy.get("approved_plugins"):
                console.print("\n  Approved:")
                for p in policy["approved_plugins"]:
                    console.print(f"    ✅ {p}")
            if overrides:
                console.print("\n  Rule Overrides:")
                for pname, rules in sorted(overrides.items()):
                    for r, a in sorted(rules.items()):
                        icon = "✅" if a in ("allow", "skip") else "🔽"
                        console.print(f"    {icon} {pname}/{r}: {a}")
            if policy.get("disabled_plugins"):
                console.print("\n  Disabled:")
                for p in policy["disabled_plugins"]:
                    console.print(f"    ⚠️  {p}")
            if policy.get("blocked_plugins"):
                console.print("\n  Blocked:")
                for p in policy["blocked_plugins"]:
                    console.print(f"    ⛔ {p}")

        return 0
    except Exception as exc:
        if json_mode:
            console.print_json(
                error_envelope("plugin_policy", [error_item("ERROR", str(exc))])
            )
        else:
            console.print_error(f"Policy read failed: {exc}")
        return 1


# ── Cache Command ────────────────────────────────────────────────────


def _cmd_cache(args: list[str]) -> int:
    """Handle `plugin cache` subcommand."""
    json_mode = "--json" in args
    console = Console(json_mode=json_mode)

    subcmd = args[0] if args else "show"

    if subcmd == "clear":
        try:
            count = cache_clear()
            if json_mode:
                console.print_json(
                    ok_envelope("plugin_cache_clear", {"cleared": count})
                )
            else:
                console.print(f"Cache cleared: {count} entries removed.")
            return 0
        except Exception as exc:
            if json_mode:
                console.print_json(
                    error_envelope(
                        "plugin_cache_clear", [error_item("ERROR", str(exc))]
                    )
                )
            else:
                console.print_error(f"Cache clear failed: {exc}")
            return 1

    elif subcmd == "show":
        try:
            stats = cache_stats()
            entries = cache_list()
            if json_mode:
                console.print_json(
                    ok_envelope(
                        "plugin_cache_show",
                        {"stats": stats, "entries": entries},
                    )
                )
            else:
                console.print("Plugin Scan Cache")
                console.print(f"  DB path:   {stats['db_path']}")
                console.print(f"  Entries:   {stats['total_entries']}")
                console.print(f"  Expired:   {stats['expired_entries']}")
                if entries:
                    console.print("\n  Cached plugins:")
                    for e in entries:
                        risk_icon = {
                            "blocked": "⛔",
                            "warning": "⚠️",
                            "clean": "✅",
                        }.get(e.get("scan_result", ""), "❓")
                        console.print(
                            f"    {risk_icon} {e['plugin_name']:<30} "
                            f"{e.get('risk_level', '?'):<10} "
                            f"scanned: {e.get('scanned_at', '?')}"
                        )
            return 0
        except Exception as exc:
            if json_mode:
                console.print_json(
                    error_envelope("plugin_cache_show", [error_item("ERROR", str(exc))])
                )
            else:
                console.print_error(f"Cache show failed: {exc}")
            return 1

    else:
        console.print_error(f"Unknown cache subcommand: {subcmd}")
        return 1


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_plugin_path(name_or_path: str) -> str | None:
    """Resolve a plugin name or path to an absolute directory path."""
    # If it looks like an absolute path
    if os.path.isabs(name_or_path) and os.path.isdir(name_or_path):
        return name_or_path

    # If it's a relative path that exists
    expanded = os.path.expanduser(name_or_path)
    if os.path.isdir(expanded):
        return os.path.abspath(expanded)

    # Search standard locations
    locations = [
        os.path.join(ops_config_io.HERMES_HOME, "plugins"),
        os.path.join(ops_config_io.HERMES_HOME, "skills"),
    ]
    for loc in locations:
        candidate = os.path.join(loc, name_or_path)
        if os.path.isdir(candidate):
            return candidate

    return None
