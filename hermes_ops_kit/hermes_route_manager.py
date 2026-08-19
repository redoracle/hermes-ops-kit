#!/usr/bin/env python3
"""Hermes Ops Kit — Route Manager CLI.

Safe profile applier + route observability + drift detector.

Manages Hermes routing by patching ``~/.hermes/config.yaml`` (the
canonical runtime config).  ``routes.yaml`` is profile metadata only —
labels, cost classes, and profile presets.  It is NEVER a second
routing source of truth.

Architecture boundary:
  Hermes owns:  model runtime, auxiliary routing, fallback chain,
                provider resolution, active sessions
  Ops Kit owns: safe config patching, route profiles, credential
                validation, route observability, drift detection,
                backups + atomic writes, cost/rate/health reporting

Usage:
    hermes-route-manager show
    hermes-route-manager doctor
    hermes-route-manager providers
    hermes-route-manager set-primary copilot gpt-5.4-mini
    hermes-route-manager set-aux vision gemini gemini-2.5-flash
    hermes-route-manager fallback add openai gpt-5.4-mini
    hermes-route-manager fallback list
    hermes-route-manager apply-profile cheap
    hermes-route-manager export --json
"""

from __future__ import annotations


if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )

import argparse
import copy
import json
import os
import sys
from typing import Any

from .ui.console import Console
from .ui.json_output import ok_envelope
from .security.redaction import sanitize_url_for_display
from .config.route_map import AUX_SHORT_KEYS, aux_config_key, aux_hermes_path

HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
HERMES_CONFIG = os.path.join(HERMES_HOME, "config.yaml")
OPS_KIT_DIR = os.path.join(HERMES_HOME, "ops-kit")
ROUTES_CONFIG = os.path.join(OPS_KIT_DIR, "routes.yaml")
IMAGE_ROUTES_CONFIG = os.path.join(OPS_KIT_DIR, "image_routes.yaml")
BUNDLED_ROUTES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "routes.yaml"
)
BUNDLED_IMAGE_ROUTES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config", "image_routes.yaml"
)

# Reasoning effort tiers — mirrors Hermes core ``hermes_constants.VALID_REASONING_EFFORTS``
# (v0.19.0 Quicksilver added ``max`` and ``ultra``). ``"none"`` disables thinking entirely
# (core's parse_reasoning_effort treats it as falsy). Written to ``agent.reasoning_effort``
# in ~/.hermes/config.yaml by cmd_apply_profile().
REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)

BUILTIN_PROFILES = {
    "cheap": {
        "primary": {
            "provider": "copilot",
            "model": "gpt-5.4-mini",
            "label": "coding/default",
        },
        "utility": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "fallbacks": [
            {"provider": "openai", "model": "gpt-5.4-mini"},
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            {"provider": "deepseek", "model": "deepseek-v4-flash"},
        ],
        "image_gen": {"provider": "ops-kit-router", "model": "auto"},
        "reasoning_effort": "low",
    },
    "balanced": {
        "primary": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "label": "deep reasoning",
        },
        "utility": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "fallbacks": [
            {"provider": "openai", "model": "gpt-5.4-mini"},
            {"provider": "deepseek", "model": "deepseek-v4-flash"},
        ],
        "image_gen": {"provider": "ops-kit-router", "model": "auto"},
        "reasoning_effort": "medium",
    },
    "max-quality": {
        "primary": {
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "label": "maximum reasoning",
        },
        "utility": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "fallbacks": [
            {"provider": "openai", "model": "gpt-5.4"},
            {"provider": "deepseek", "model": "deepseek-v4-pro"},
        ],
        "image_gen": {"provider": "ops-kit-router", "model": "auto"},
        "reasoning_effort": "max",
    },
}


# ─── YAML Helpers ─────────────────────────────────────────────────────


from .ops_config_io import load_yaml as _load_yaml, save_yaml as _save_yaml  # noqa: E402


# ─── Commands ──────────────────────────────────────────────────────────


def _headroom_overlay(hc: dict) -> dict[str, Any] | None:
    """Headroom route-overlay info when the primary is proxied.

    The overlay applied by `hermes-ops-kit headroom enable` is expected
    state, not drift: show/doctor render it as `via headroom(port) →
    upstream` instead of treating the provider swap as an anomaly.
    """
    model = hc.get("model", {}) if isinstance(hc, dict) else {}
    if str(model.get("provider", "")).strip().lower() != "headroom":
        return None
    try:
        from .headroom_ops.reconcile import load_snapshot  # pyright: ignore[reportMissingImports]
        from .headroom_ops.settings import load_settings  # pyright: ignore[reportMissingImports]

        settings = load_settings()
        snap_model = (load_snapshot(settings) or {}).get("model") or {}
        return {
            "port": settings.get("port"),
            "upstream_provider": snap_model.get("provider", "?"),
            "managed": bool(snap_model.get("provider")),
        }
    except Exception:
        return {"port": "?", "upstream_provider": "?", "managed": False}


def cmd_show(args: argparse.Namespace) -> None:
    """Display current route configuration."""
    con = Console(json_mode=args.json)
    hc = _load_yaml(HERMES_CONFIG)
    _rc = _load_yaml(ROUTES_CONFIG) or _load_yaml(BUNDLED_ROUTES)
    img_cfg = _load_yaml(IMAGE_ROUTES_CONFIG) or _load_yaml(BUNDLED_IMAGE_ROUTES)

    model = hc.get("model", {})
    fb = hc.get("fallback_providers", [])
    aux = hc.get("auxiliary", {})

    # ── Assemble auxiliary route data ──────────────────────────────
    aux_data: dict[str, dict[str, Any]] = {}
    for short_key in AUX_SHORT_KEYS:
        config_key = aux_config_key(short_key)
        slot = aux.get(config_key, {}) if isinstance(aux, dict) else {}
        aux_data[short_key] = {
            "provider": slot.get("provider", "auto"),
            "model": slot.get("model", ""),
            "configured": bool(slot.get("model")),
        }

    # ── Assemble image route data ──────────────────────────────────
    img_routes = img_cfg.get("routes", {})
    img_default = img_cfg.get("default_route", "fast")
    img_data: dict[str, dict[str, Any]] = {}
    for name, cfg in img_routes.items():
        img_data[name] = {
            "provider": cfg.get("provider", "?"),
            "model": cfg.get("model", "?"),
            "label": cfg.get("label", ""),
            "cost_class": cfg.get("cost_class", ""),
            "is_default": name == img_default,
        }
    img_policies = {
        "prefer_local": img_cfg.get("policies", {}).get("prefer_local", True),
        "default_route": img_default,
    }

    # ── JSON output ────────────────────────────────────────────────
    overlay = _headroom_overlay(hc)

    if args.json:
        envelope = ok_envelope(
            command="show",
            result={
                "primary": {
                    "provider": model.get("provider", "copilot"),
                    "model": model.get("default", "gpt-5.4-mini"),
                    "headroom_overlay": overlay,
                },
                "fallbacks": fb,
                "auxiliary": aux_data,
                "image_routes": {
                    "routes": img_data,
                    "policies": img_policies,
                },
            },
        )
        con.print_json(envelope)
        return

    # ── Terminal output ────────────────────────────────────────────
    con.print(con.header("=== Primary Model ==="))
    if overlay:
        via = con.dim(f"via headroom({overlay['port']})")
        con.print(f"  provider: {con.green(overlay['upstream_provider'])} {via}")
    else:
        con.print(f"  provider: {con.green(model.get('provider', 'copilot'))}")
    con.print(f"  model:    {con.bold(model.get('default', 'gpt-5.4-mini'))}")
    if overlay and not overlay["managed"]:
        con.print_warning(
            "primary points at headroom without an ops-kit snapshot — "
            "run: hermes-ops-kit headroom reconcile"
        )
    con.print()

    con.print(con.header("=== Fallback Chain ==="))
    if fb:
        for i, f in enumerate(fb, 1):
            con.print(f"  {i}. {f.get('provider', '?')}:{con.dim(f.get('model', '?'))}")
    else:
        con.print(f"  {con.dim('(defaults — see config/routes.yaml profiles)')}")
    con.print()

    con.print(con.header("=== Auxiliary Routes ==="))
    for short_key, a in aux_data.items():
        p = a["provider"]
        m = a["model"] if a["model"] else con.dim("(default)")
        con.print(f"  {short_key:<12s} {p}:{m}")
    con.print()

    # ── Image Routes (separate from auxiliary — image generation, not LLM text) ──
    if img_data:
        con.print(con.header("=== IMAGE ROUTES ==="))
        for name, i in img_data.items():
            provider = i["provider"]
            model = i["model"]
            label = i["label"]
            cost = i["cost_class"]
            marker = " ★" if i["is_default"] else ""
            con.print(
                f"  {name:<10s} {provider}:{model:<28s} "
                f"{cost:<10s} {label}{con.bold(marker)}"
            )
        con.print(f"  prefer_local: {img_policies['prefer_local']}")
        con.print()


def cmd_doctor(args: argparse.Namespace) -> None:
    """Validate route configuration."""
    issues = []

    if not os.path.exists(HERMES_CONFIG):
        issues.append(
            ("config_missing", "~/.hermes/config.yaml not found — using defaults")
        )
    else:
        hc = _load_yaml(HERMES_CONFIG)
        if not hc.get("model", {}).get("provider"):
            issues.append(
                ("no_primary", "No primary model configured. Run: hermes model")
            )
        if not hc.get("fallback_providers"):
            issues.append(
                (
                    "no_fallbacks",
                    "No fallback providers configured. Run: hermes fallback",
                )
            )
        overlay = _headroom_overlay(hc)
        if overlay:
            if overlay["managed"]:
                print(
                    f"ℹ headroom overlay active: primary via "
                    f"headroom({overlay['port']}) → {overlay['upstream_provider']}"
                )
            else:
                issues.append(
                    (
                        "headroom_unmanaged",
                        "primary points at headroom without an ops-kit "
                        "snapshot — run: hermes-ops-kit headroom reconcile",
                    )
                )
        # Fallbacks must never resolve through the local proxy.
        for i, f in enumerate(hc.get("fallback_providers") or []):
            base = str(f.get("base_url", "") or "") if isinstance(f, dict) else ""
            if "127.0.0.1" in base or "localhost" in base:
                issues.append(
                    (
                        "fallback_via_proxy",
                        f"fallback_providers[{i}] targets a local proxy "
                        f"({sanitize_url_for_display(base)}) — graceful degradation is lost",
                    )
                )

    if issues:
        for code, msg in issues:
            print(f"⚠ {code}: {msg}")
    else:
        print("✓ Route configuration valid")
    print(f"  config: {HERMES_CONFIG}")
    print(f"  routes: {ROUTES_CONFIG}")


def cmd_set_primary(args: argparse.Namespace) -> None:
    """Set primary model in Hermes config."""
    hc = _load_yaml(HERMES_CONFIG)
    if "model" not in hc:
        hc["model"] = {}
    hc["model"]["provider"] = args.provider
    hc["model"]["default"] = args.model
    _save_yaml(HERMES_CONFIG, hc)
    print(f"Primary: {args.provider}:{args.model}")


def cmd_set_aux(args: argparse.Namespace) -> None:
    """Set auxiliary route in Hermes config."""
    hc = _load_yaml(HERMES_CONFIG)
    try:
        config_key = aux_config_key(args.aux_kind)
        _hermes_path = aux_hermes_path(args.aux_kind)
    except KeyError:
        print(f"Unknown aux kind: {args.aux_kind}. Valid: {AUX_SHORT_KEYS}")
        sys.exit(1)
    hc.setdefault("auxiliary", {}).setdefault(config_key, {})
    existing_timeout = hc.get("auxiliary", {}).get(config_key, {}).get("timeout", 120)
    hc["auxiliary"][config_key] = {
        "provider": args.provider,
        "model": args.model,
        "timeout": existing_timeout,
    }
    _save_yaml(HERMES_CONFIG, hc)
    print(f"Aux {args.aux_kind}: {args.provider}:{args.model}")


def cmd_fallback(args: argparse.Namespace) -> None:
    """Manage fallback providers."""
    hc = _load_yaml(HERMES_CONFIG)
    fb = hc.get("fallback_providers", [])

    if args.fb_action == "list":
        if not fb:
            print("No fallbacks configured")
        for i, f in enumerate(fb, 1):
            print(f"  {i}. {f.get('provider', '?')}:{f.get('model', '?')}")
    elif args.fb_action == "add":
        fb.append({"provider": args.provider, "model": args.model})
        hc["fallback_providers"] = fb
        _save_yaml(HERMES_CONFIG, hc)
        print(f"Added fallback: {args.provider}:{args.model}")
    elif args.fb_action == "remove":
        provider = args.provider
        hc["fallback_providers"] = [f for f in fb if f.get("provider") != provider]
        _save_yaml(HERMES_CONFIG, hc)
        print(f"Removed fallback: {provider}")
    elif args.fb_action == "clear":
        hc["fallback_providers"] = []
        _save_yaml(HERMES_CONFIG, hc)
        print("Fallbacks cleared")


def cmd_apply_profile(args: argparse.Namespace) -> None:
    """Apply a route profile by writing native Hermes config keys.

    Translates profile presets from routes.yaml into ~/.hermes/config.yaml
    writes.  The utility preset (if any) is expanded into explicit
    ``auxiliary.<task>.provider`` / ``auxiliary.<task>.model`` entries so
    every AUX route is explicit — no ``auto`` left for Hermes to resolve
    at runtime.

    Only ``~/.hermes/config.yaml`` is written.  ``routes.yaml`` is NEVER
    mutated by this command.
    """
    profile = BUILTIN_PROFILES.get(args.profile_name)
    if not profile:
        print(
            f"Unknown profile: {args.profile_name}. Valid: {list(BUILTIN_PROFILES.keys())}"
        )
        sys.exit(1)

    hc = _load_yaml(HERMES_CONFIG)

    # ── Primary ─────────────────────────────────────────────────────
    primary = profile["primary"]
    hc.setdefault("model", {})["provider"] = primary["provider"]
    hc["model"]["default"] = primary["model"]

    # ── Fallbacks ───────────────────────────────────────────────────
    hc["fallback_providers"] = copy.deepcopy(profile.get("fallbacks", []))

    # ── AUX: expand utility preset into explicit auxiliary entries ──
    utility = profile.get("utility", {})
    aux_entries = profile.get("auxiliary", {})
    if utility and not aux_entries:
        # Expand utility preset → explicit auxiliary.<task> entries
        hc.setdefault("auxiliary", {})
        for sk in AUX_SHORT_KEYS:
            config_key = aux_config_key(sk)
            hc["auxiliary"][config_key] = {
                "provider": utility.get("provider", "gemini"),
                "model": utility.get("model", "gemini-2.5-flash"),
            }
    elif aux_entries:
        hc.setdefault("auxiliary", {})
        for config_key, slot in aux_entries.items():
            hc["auxiliary"][config_key] = dict(slot)

    # ── Image gen ───────────────────────────────────────────────────
    image_gen = profile.get("image_gen", {})
    if image_gen:
        hc["image_gen"] = dict(image_gen)

    # ── provider_routing (OpenRouter only) ──────────────────────────
    provider_routing = profile.get("provider_routing", {})
    if provider_routing and hc.get("model", {}).get("provider") == "openrouter":
        hc["provider_routing"] = copy.deepcopy(provider_routing)

    # ── Reasoning effort (agent.reasoning_effort) ───────────────────
    # Mirrors Hermes core hermes_constants.parse_reasoning_effort.
    # --effort overrides the profile default; "none" disables thinking.
    effort = args.effort or profile.get("reasoning_effort")
    if effort:
        hc.setdefault("agent", {})["reasoning_effort"] = effort

    _save_yaml(HERMES_CONFIG, hc)

    aux_count = (
        len(aux_entries) if aux_entries else (len(AUX_SHORT_KEYS) if utility else 0)
    )
    print(f"Profile '{args.profile_name}' applied → {HERMES_CONFIG}")
    print(f"  primary:     {primary['provider']}:{primary['model']}")
    if utility:
        print(
            f"  utility:     {utility.get('provider', '?')}:{utility.get('model', '?')} (expanded to {aux_count} AUX entries)"
        )
    print(f"  fallbacks:   {len(profile.get('fallbacks', []))}")
    if image_gen:
        print(
            f"  image_gen:   {image_gen.get('provider', '?')}:{image_gen.get('model', '?')}"
        )
    if effort:
        print(f"  effort:      {effort}  (agent.reasoning_effort)")
    print()
    print("Takes effect:")
    print("  - New hermes chat sessions: immediately")
    print("  - Current chat: /model ... --global or restart session")
    print("  - Gateway sessions: restart gateway to force all sessions")


def cmd_providers(_args: argparse.Namespace) -> None:
    """List configured providers from Hermes config."""
    hc = _load_yaml(HERMES_CONFIG)
    providers = hc.get("providers", {})
    model = hc.get("model", {})

    print("=== Active Providers ===")
    print(
        f"  primary:  {model.get('provider', 'copilot')} ({model.get('default', 'gpt-5.4-mini')})"
    )
    print(f"  fallback: {len(hc.get('fallback_providers', []))} configured")
    print(
        f"  aux:      {sum(1 for v in hc.get('auxiliary', {}).values() if isinstance(v, dict) and v.get('provider') not in (None, 'auto', ''))} custom"
    )
    if providers:
        for pname, pdata in providers.items():
            print(f"  {pname}: {pdata.get('type', '?')}")


def cmd_export(args: argparse.Namespace) -> None:
    """Export full route configuration as JSON."""
    hc = _load_yaml(HERMES_CONFIG)
    rc = _load_yaml(ROUTES_CONFIG) or _load_yaml(BUNDLED_ROUTES)

    output = {
        "hermes_config": {
            "model": hc.get("model", {}),
            "fallback_providers": hc.get("fallback_providers", []),
            "auxiliary": {
                sk: (
                    hc.get("auxiliary", {}).get(aux_config_key(sk), {})
                    if isinstance(hc.get("auxiliary", {}), dict)
                    else {}
                )
                for sk in AUX_SHORT_KEYS
            },
        },
        "routes_config": rc.get("routes", {}),
        "profiles": list(BUILTIN_PROFILES.keys()),
    }
    print(json.dumps(output, indent=2))


# ─── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Ops Kit — Route Manager")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    sub.add_parser("show")
    sub.add_parser("doctor")
    sub.add_parser("providers")

    p = sub.add_parser("set-primary")
    p.add_argument("provider")
    p.add_argument("model")

    a = sub.add_parser("set-aux")
    a.add_argument("aux_kind", choices=AUX_SHORT_KEYS)
    a.add_argument("provider")
    a.add_argument("model")

    fb = sub.add_parser("fallback")
    fb.add_argument("fb_action", choices=["add", "remove", "list", "clear"])
    fb.add_argument("provider", nargs="?")
    fb.add_argument("model", nargs="?")

    prof = sub.add_parser("apply-profile")
    prof.add_argument("profile_name", choices=list(BUILTIN_PROFILES.keys()))
    prof.add_argument(
        "--effort",
        choices=REASONING_EFFORTS,
        default=None,
        help="reasoning_effort override (writes agent.reasoning_effort); "
        "one of: " + ", ".join(REASONING_EFFORTS),
    )

    sub.add_parser("export")

    args = parser.parse_args()

    handlers = {
        "show": cmd_show,
        "doctor": cmd_doctor,
        "providers": cmd_providers,
        "set-primary": cmd_set_primary,
        "set-aux": cmd_set_aux,
        "fallback": cmd_fallback,
        "apply-profile": cmd_apply_profile,
        "export": cmd_export,
    }
    handler = handlers.get(args.command)
    if handler:
        handler(args)


if __name__ == "__main__":
    main()
