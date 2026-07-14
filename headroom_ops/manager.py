"""Hermes Ops Kit — Headroom CLI.

Usage:
    hermes-ops-kit headroom status                # Route + daemon overview
    hermes-ops-kit headroom doctor                # Full health/invariant checks
    hermes-ops-kit headroom up                    # Start the proxy (daemon only)
    hermes-ops-kit headroom down                  # Stop the proxy (daemon only)
    hermes-ops-kit headroom enable [--dry-run]    # Desired=on + reconcile now
    hermes-ops-kit headroom disable [--dry-run]   # Desired=off + reconcile + stop
    hermes-ops-kit headroom reconcile [--dry-run] # Align config.yaml to desired
    hermes-ops-kit headroom stats                 # Proxy /stats (token savings)
    hermes-ops-kit headroom export --json         # Machine-readable state

Daemon lifecycle is owned by ops-kit (pidfile in ~/.hermes/ops-kit/run);
no shell aliases or external supervisors required.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from headroom_ops import daemon, reconcile as rec  # noqa: E402
from headroom_ops.settings import (  # noqa: E402
    DEPLOYED_CONFIG,
    FORBIDDEN_FLAGS,
    load_settings,
    proxy_root,
    set_desired_enabled,
)
from ops_config_io import HERMES_CONFIG, load_yaml  # noqa: E402
from security.redaction import sanitize_url_for_display  # noqa: E402
from ui.console import Console  # noqa: E402
from ui.json_output import error_envelope, error_item, ok_envelope  # noqa: E402


def _gather_state() -> dict[str, Any]:
    """Shared snapshot used by status/doctor/export."""
    settings = load_settings()
    hermes_cfg = load_yaml(HERMES_CONFIG)
    provider, entry = rec.upstream_provider_entry(hermes_cfg, settings)
    return {
        "settings": settings,
        "hermes_cfg": hermes_cfg,
        "desired": "enabled" if settings.get("enabled") else "disabled",
        "proxied": rec.is_proxied(hermes_cfg, settings),
        "daemon": daemon.status(settings),
        "upstream_provider": provider,
        "upstream_base_url": str(entry.get("base_url", "") or ""),
        "collisions": rec.collision_findings(hermes_cfg, settings),
    }


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe subset of _gather_state (no full configs)."""
    s = state["settings"]
    return {
        "desired": state["desired"],
        "proxied": state["proxied"],
        "port": s.get("port"),
        "base_url": s.get("base_url"),
        "upstream": {
            "mode": s["upstream"].get("mode"),
            "provider": state["upstream_provider"],
            "base_url": sanitize_url_for_display(state["upstream_base_url"]),
        },
        "apply": s.get("apply"),
        "daemon": state["daemon"],
        "collisions": state["collisions"],
        "config_path": DEPLOYED_CONFIG,
    }


def cmd_status(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    state = _gather_state()
    if getattr(args, "json", False):
        con.print_json(ok_envelope(command="headroom.status", result=_public_state(state)))
        return 0

    d = state["daemon"]
    con.print(con.header("=== Headroom ==="))
    con.print(f"  desired:  {con.bold(state['desired'])}")
    route = (
        f"via headroom({state['settings'].get('port')}) → "
        f"{state['upstream_provider'] or '?'}"
        if state["proxied"]
        else f"direct ({state['upstream_provider'] or '?'})"
    )
    con.print(f"  route:    {con.green(route) if state['proxied'] else route}")
    pid_note = con.dim(f" (pid {d['pid']})") if d["alive"] else ""
    proxy_state = con.green("healthy") if d["healthy"] else con.red("down")
    con.print(f"  proxy:    {proxy_state}{pid_note}")
    con.print(f"  upstream: {sanitize_url_for_display(state['upstream_base_url']) or con.dim('(unresolved)')}")
    if state["collisions"]:
        for c in state["collisions"]:
            con.print_error(f"collision: {c}")
    return 1 if state["collisions"] else 0


def _doctor_checks(state: dict[str, Any]) -> list[tuple[str, bool, str]]:
    """(label, ok, detail) tuples; warnings encode ok=True with detail."""
    import shutil

    settings = state["settings"]
    hermes_cfg = state["hermes_cfg"]
    checks: list[tuple[str, bool, str]] = []

    binary = shutil.which("headroom")
    checks.append(("headroom binary", binary is not None,
                   binary or "not found — pipx install headroom-ai"))

    healthy = state["daemon"]["healthy"]
    desired_on = settings.get("enabled", False)
    checks.append(("proxy /readyz", healthy or not desired_on,
                   "healthy" if healthy else
                   ("down (desired=disabled — ok)" if not desired_on else
                    f"down — run: hermes-ops-kit headroom up "
                    f"({proxy_root(settings)}/readyz)")))

    upstream = state["upstream_base_url"]
    checks.append(("upstream resolvable", bool(upstream),
                   upstream or f"providers.{state['upstream_provider'] or '?'} "
                               f"has no base_url"))

    _, entry = rec.upstream_provider_entry(hermes_cfg, settings)
    key_env = str(entry.get("api_key_env") or entry.get("key_env") or "")
    key_present = bool(key_env and os.environ.get(key_env))
    if key_env and not key_present:
        env_file = os.path.join(os.path.dirname(HERMES_CONFIG), ".env")
        try:
            with open(env_file) as f:
                key_present = any(
                    line.strip().startswith(f"{key_env}=") for line in f
                )
        except OSError:
            pass
    checks.append(("upstream key env", key_present,
                   key_env or "no api_key_env on upstream provider"))

    collisions = state["collisions"]
    checks.append(("fallbacks stay direct", not collisions,
                   "; ".join(collisions) or "ok"))

    fallbacks = hermes_cfg.get("fallback_providers") or []
    checks.append(("fallback chain present", bool(fallbacks),
                   f"{len(fallbacks)} entries" if fallbacks else
                   "empty — no degradation path if the proxy dies"))

    proxied = state["proxied"]
    coherent = proxied == desired_on
    checks.append(("config matches desired", coherent,
                   "ok" if coherent else
                   "drift — run: hermes-ops-kit headroom reconcile"))

    meta_upstream = str(state["daemon"]["meta"].get("upstream_url") or "")
    if healthy and desired_on and meta_upstream and upstream:
        upstream_match = meta_upstream.rstrip("/") == str(upstream).rstrip("/")
        checks.append(("proxy upstream matches", upstream_match,
                       "ok" if upstream_match else
                       f"proxy forwards to {meta_upstream} but primary is "
                       f"{upstream} — run: hermes-ops-kit headroom reconcile"))

    meta_flags = state["daemon"]["meta"].get("flags") or []
    bad = [f for f in meta_flags if f in FORBIDDEN_FLAGS]
    checks.append(("no-coding profile", not bad,
                   "ok" if not bad else f"forbidden flags active: {bad}"))

    compression_on = bool((hermes_cfg.get("compression") or {}).get("enabled"))
    if proxied and compression_on:
        checks.append(("compression layering", True,
                       "Hermes history compression + Headroom transport "
                       "compression both active (by design; watch quality)"))
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    state = _gather_state()
    checks = _doctor_checks(state)
    all_ok = all(ok for _, ok, _ in checks)
    if getattr(args, "json", False):
        result = {
            "checks": [
                {"name": n, "ok": ok, "detail": d} for n, ok, d in checks
            ],
            "state": _public_state(state),
        }
        envelope = (
            ok_envelope(command="headroom.doctor", result=result)
            if all_ok
            else error_envelope(
                command="headroom.doctor",
                errors=[error_item("check_failed", f"{n}: {d}")
                        for n, ok, d in checks if not ok],
            )
        )
        envelope["result"] = result
        con.print_json(envelope)
        return 0 if all_ok else 1

    con.print(con.header("=== Headroom Doctor ==="))
    for name, ok, detail in checks:
        icon = "✅" if ok else "❌"
        con.print(f"  {icon} {name}: {detail}")
    return 0 if all_ok else 1


def _resolve_upstream_or_fail(con: Console, state: dict[str, Any]) -> str | None:
    upstream = state["upstream_base_url"]
    if not upstream:
        con.print_error(
            f"cannot resolve an OpenAI-compatible upstream for provider "
            f"'{state['upstream_provider'] or '?'}' — add base_url to its "
            f"providers entry in {HERMES_CONFIG}"
        )
        return None
    return upstream


def cmd_up(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    state = _gather_state()
    upstream = _resolve_upstream_or_fail(con, state)
    if not upstream:
        return 1
    res = daemon.up(state["settings"], upstream,
                    dry_run=getattr(args, "dry_run", False))
    if getattr(args, "json", False):
        con.print_json(ok_envelope(command="headroom.up", result=res))
    else:
        (con.print if res["ok"] else con.print_error)(res["message"])
    return 0 if res["ok"] else 1


def cmd_down(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    res = daemon.down(load_settings())
    if getattr(args, "json", False):
        con.print_json(ok_envelope(command="headroom.down", result=res))
    else:
        (con.print if res["ok"] else con.print_error)(res["message"])
    return 0 if res["ok"] else 1


def _print_reconcile(con: Console, res: dict, json_mode: bool, command: str) -> int:
    if json_mode:
        envelope = (
            ok_envelope(command=command, result=res, warnings=res["warnings"])
            if res["ok"]
            else error_envelope(
                command=command,
                errors=[error_item("reconcile", e) for e in res["errors"]],
                warnings=res["warnings"],
            )
        )
        envelope["result"] = res
        con.print_json(envelope)
        return 0 if res["ok"] else 1
    con.print(f"  desired: {res['desired']}  action: {con.bold(res['action'])}")
    for i in res.get("info", []):
        con.print(f"  {i}")
    if res.get("upstream"):
        con.print(f"  upstream: {res['upstream']['provider']} "
                  f"({sanitize_url_for_display(res['upstream']['base_url'])})")
    if res.get("backup"):
        con.print(f"  backup:  {res['backup']}")
    for w in res["warnings"]:
        con.print_warning(w)
    for e in res["errors"]:
        con.print_error(e)
    return 0 if res["ok"] else 1


def cmd_reconcile(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    res = rec.reconcile(dry_run=getattr(args, "dry_run", False))
    return _print_reconcile(con, res, getattr(args, "json", False),
                            "headroom.reconcile")


def cmd_enable(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    dry = getattr(args, "dry_run", False)
    if not dry:
        set_desired_enabled(True)
    res = rec.reconcile(dry_run=dry, desired_override=True if dry else None)
    if dry:
        res["warnings"].insert(0, "dry-run: desired state not persisted")
    return _print_reconcile(con, res, getattr(args, "json", False),
                            "headroom.enable")


def cmd_disable(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    dry = getattr(args, "dry_run", False)
    if not dry:
        set_desired_enabled(False)
    res = rec.reconcile(dry_run=dry, desired_override=False if dry else None)
    if not dry and res["ok"]:
        stop = daemon.down(load_settings())
        # A clean stop is information, not a warning.
        bucket = res.setdefault("info", []) if stop["ok"] else res["warnings"]
        bucket.append(f"daemon: {stop['message']}")
    if dry:
        res["warnings"].insert(0, "dry-run: desired state not persisted")
    return _print_reconcile(con, res, getattr(args, "json", False),
                            "headroom.disable")


def cmd_stats(args: argparse.Namespace) -> int:
    con = Console(json_mode=getattr(args, "json", False))
    settings = load_settings()
    stats = daemon.get_stats(settings)
    if stats is None:
        msg = f"proxy unreachable at {proxy_root(settings)}/stats"
        if getattr(args, "json", False):
            con.print_json(error_envelope(
                command="headroom.stats",
                errors=[error_item("unreachable", msg)],
            ))
        else:
            con.print_error(msg)
        return 1
    if getattr(args, "json", False):
        con.print_json(ok_envelope(command="headroom.stats", result=stats))
    else:
        import json as _json

        con.print(_json.dumps(stats, indent=2))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    con = Console(json_mode=True)
    con.print_json(ok_envelope(command="headroom.export",
                               result=_public_state(_gather_state())))
    return 0


def handle_headroom_command(args: list[str]) -> int:
    """Entry point for `hermes-ops-kit headroom ...` subcommand."""
    parser = argparse.ArgumentParser(
        prog="hermes-ops-kit headroom",
        description="Hermes Ops Kit — Headroom proxy route overlay",
    )
    sub = parser.add_subparsers(dest="subcommand")

    for name, help_text, flags in (
        ("status", "Route + daemon overview", ("--json",)),
        ("doctor", "Health and invariant checks", ("--json",)),
        ("up", "Start the proxy daemon", ("--json", "--dry-run")),
        ("down", "Stop the proxy daemon", ("--json",)),
        ("enable", "Set desired=enabled and reconcile", ("--json", "--dry-run")),
        ("disable", "Set desired=disabled, reconcile, stop daemon",
         ("--json", "--dry-run")),
        ("reconcile", "Align config.yaml with the desired state",
         ("--json", "--dry-run")),
        ("stats", "Proxy /stats (compression and token savings)", ("--json",)),
        ("export", "Machine-readable state (JSON)", ()),
    ):
        p = sub.add_parser(name, help=help_text)
        for flag in flags:
            p.add_argument(flag, action="store_true")

    parsed = parser.parse_args(args)
    if not parsed.subcommand:
        parser.print_help()
        return 1

    handlers = {
        "status": cmd_status,
        "doctor": cmd_doctor,
        "up": cmd_up,
        "down": cmd_down,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "reconcile": cmd_reconcile,
        "stats": cmd_stats,
        "export": cmd_export,
    }
    return handlers[parsed.subcommand](parsed)


if __name__ == "__main__":
    sys.exit(handle_headroom_command(sys.argv[1:]))
