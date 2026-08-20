"""Hermes Ops Kit — Plugin CLI Commands.

Exposes `hermes-ops-kit <subcommand>` for operator use.
Also callable as standalone `hermes-ops-kit <subcommand>`.
"""

from __future__ import annotations

import json
import os
import sys

from ._subprocess import run_module  # pyright: ignore[reportMissingImports]


def handle_ops_kit_command(args: list[str]) -> int:
    """Handle `hermes-ops-kit <subcommand>`.

    Args:
        args: List of subcommand arguments (e.g. ["status"], ["usage", "--json"]).

    Returns:
        Exit code (0 = success).
    """
    if not args:
        return _usage()

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "status":
        return _run_script("usage_metrics_v2.py", ["--json"])
    elif subcmd == "usage":
        return _run_script("usage_metrics_v2.py", rest)
    elif subcmd == "rotate":
        return _run_script("hermes_key_rotate.py", rest)
    elif subcmd == "doctor":
        return _handle_doctor()
    elif subcmd in ("assistants", "assistant"):
        return _handle_assistants(rest)
    elif subcmd == "mcp":
        return _handle_mcp(rest)
    elif subcmd == "budget":
        return _handle_budget(rest)
    elif subcmd == "maintenance":
        return _handle_maintenance(rest)
    elif subcmd == "audit":
        return _handle_audit(rest)
    elif subcmd == "image":
        return _handle_image(rest)
    elif subcmd == "headroom":
        return _handle_headroom(rest)
    elif subcmd == "install":
        return _handle_install(rest)
    elif subcmd == "route-test":
        return _handle_route_test(rest)
    elif subcmd == "plugin":
        return _handle_plugin(rest)
    elif subcmd == "preflight":
        return _handle_preflight(rest)
    else:
        print(f"Unknown subcommand: {subcmd}")
        return _usage()


def _usage() -> int:
    print("Hermes Ops Kit")
    print()
    print("  hermes-ops-kit status                   Health overview")
    print("  hermes-ops-kit usage [--compact|--json]  Usage metrics")
    print("  hermes-ops-kit preflight [--dry-run]     Scan + enforce plugin security")
    print("  hermes-ops-kit rotate --provider X --dry-run")
    print("  hermes-ops-kit doctor                    Full diagnostic")
    print("  hermes-ops-kit assistant list            Manage assistants")
    print("  hermes-ops-kit audit tail                Recent audit events")
    print("  hermes-ops-kit mcp audit                 MCP tool security audit")
    print("  hermes-ops-kit budget status             Cost governor status")
    print("  hermes-ops-kit maintenance profiles            Assistant tasks profiles")
    print("  hermes-ops-kit image routes               Image generation routes")
    print('  hermes-ops-kit image test "prompt"        Test image generation')
    print("  hermes-ops-kit image doctor               Validate image backends")
    print("  hermes-ops-kit route-test [--fallback]  Verify route selection")
    print("  hermes-ops-kit headroom status            Headroom proxy overlay status")
    print("  hermes-ops-kit headroom enable|disable    Toggle proxied primary route")
    print("  hermes-ops-kit headroom doctor            Headroom health + invariants")
    print("  hermes-ops-kit install setup             First-install security bootstrap")
    print("  hermes-ops-kit install doctor            Install checks")
    print("  hermes-ops-kit plugin scan               Plugin security scanner")
    print("  hermes-ops-kit plugin policy              Plugin approval policy")
    return 1


def _run_script(script_name: str, args: list[str]) -> int:
    """Run an ops-kit module as a ``-P -m`` subprocess and pass through output."""
    module = script_name.removesuffix(".py").replace("/", ".")
    result = run_module(module, args)
    return result.returncode


def _handle_doctor() -> int:
    """Unified end-to-end diagnostic. Answers: Is Hermes ready to work?"""

    warnings = []
    ok_count = 0
    total = 0

    def _check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal ok_count, total
        total += 1
        if ok:
            ok_count += 1
            print(f"  ✅ {label:<20s} {detail}")
        else:
            warnings.append(f"{label}: {detail}")
            print(f"  ⚠ {label:<20s} {detail}")

    def _hdr(title: str) -> None:
        print(f"\n{title}")

    # ── CORE ──
    _hdr("CORE")
    try:
        from .security.file_permissions import check_env_file  # pyright: ignore[reportMissingImports]

        generated = os.path.expanduser("~/.hermes/.env.generated")
        env_check_path = (
            generated
            if os.path.exists(generated)
            else os.path.expanduser("~/.hermes/.env")
        )
        env_check = check_env_file(env_check_path)
        _check(
            "hermes env",
            bool(env_check.get("safe", False)),
            str(env_check.get("mode", "?")),
        )
    except Exception as e:
        _check("hermes env", False, str(e)[:60])

    _check(
        "plugin ops-kit",
        os.path.exists(
            os.path.join(
                os.path.expanduser("~/.hermes"),
                "plugins",
                "hermes-ops-kit",
                "plugin.yaml",
            )
        )
        or os.path.exists(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.yaml")
        ),
        "installed"
        if os.path.exists(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.yaml")
        )
        else "not in plugins dir",
    )

    # ── ROUTES ──
    _hdr("ROUTES")
    try:
        route_data = _try_build_routes()
        main = route_data.get("routes", [])
        aux = route_data.get("aux_routes", [])
        fb = route_data.get("fallbacks", [])
        _check("primary", len(main) > 0, main[0]["route"] if main else "none")
        _check(
            "aux routes",
            len([r for r in aux if r.get("online")]) > 0,
            f"{len([r for r in aux if r.get('online')])}/{len(aux)} configured",
        )
        _check("fallbacks", len(fb) > 0, f"{len(fb)} configured")
    except Exception as e:
        _check("routes", False, str(e)[:60])

    # ── ASSISTANTS ──
    _hdr("ASSISTANTS")
    try:
        from .assistants.registry import load_registry  # pyright: ignore[reportMissingImports]

        registry = load_registry()
        for aid, cfg in registry.items():
            # Skip aliases — same AssistantConfig registered under short names
            if cfg.id != aid:
                continue
            caps = [c["id"] for c in cfg.capabilities]
            aliases = [k for k, v in registry.items() if v.id == cfg.id and k != cfg.id]
            alias_str = f" (aliases: {', '.join(aliases)})" if aliases else ""
            _check(aid, cfg.enabled, f"{cfg.role} · {', '.join(caps[:3])}{alias_str}")
    except Exception as e:
        _check("assistants", False, str(e)[:60])

    # ── SECRETS ──
    _hdr("SECRETS")
    try:
        r = run_module(
            "hermes_key_rotate",
            ["--healthcheck"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        import json as _json

        hc = _json.loads(r.stdout)
        _check("vaultwarden", hc.get("server_ok", False), hc.get("server_status", "?"))
        _check(
            "vault unlocked",
            hc.get("unlocked", False),
            "locked" if not hc.get("unlocked") else "ok",
        )
    except Exception as e:
        _check("secrets", False, str(e)[:60])

    gen_env = os.path.expanduser("~/.hermes/.env.generated")
    if os.path.exists(gen_env):
        import stat

        mode = stat.S_IMODE(os.stat(gen_env).st_mode)
        _check("env.generated", mode == 0o600, oct(mode)[2:])
    else:
        _check("env.generated", False, "not found — run hermes-key-rotate --render-env")

    # ── CREDENTIALS ──
    _hdr("CREDENTIALS")
    env_vars = _load_hermes_env()
    _check_credentials_section(env_vars, _check)

    # ── ROUTE BYPASS ──
    _hdr("ROUTE BYPASS")
    cfg_path = os.path.expanduser("~/.hermes/config.yaml")
    if os.path.exists(cfg_path):
        _check_bypass_section(cfg_path, env_vars, _check)
    else:
        _check("config.yaml", False, "not found")

    # ── SUMMARY ──
    status = (
        "READY"
        if len(warnings) == 0
        else "DEGRADED"
        if ok_count > total // 2
        else "BLOCKED"
    )
    print(f"\n{'─' * 50}")
    print(f"  {ok_count}/{total} checks passed · STATUS: {status}")

    if warnings:
        print("\nWARNINGS")
        for w in warnings:
            print(f"  ⚠ {w}")

    print("\nNEXT")
    print("  hermes-ops-kit doctor")
    print("  hermes-usage --compact")
    print("  hermes-key-rotate --render-env")
    return 0 if status != "BLOCKED" else 1


def _try_build_routes() -> dict:
    """Build route display from Hermes config (no live API probes for doctor speed)."""
    try:
        from .usage_metrics_v2 import build_routes  # pyright: ignore[reportMissingImports]

        # Populate results from Hermes config — mark all configured providers as "online"
        # so the doctor shows configured routes, not just live-probed ones.
        cfg_path = os.path.expanduser("~/.hermes/config.yaml")
        results: dict[str, dict] = {}
        if os.path.exists(cfg_path):
            try:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                with open(cfg_path) as f:
                    hc = _yaml.safe_load(f) or {}
                model = hc.get("model", {})
                providers_in_use = {model.get("provider", "copilot")}
                for fb in hc.get("fallback_providers", []):
                    providers_in_use.add(fb.get("provider", ""))
                for aux in hc.get("auxiliary", {}).values():
                    if isinstance(aux, dict) and aux.get("provider") not in (
                        None,
                        "auto",
                        "",
                    ):
                        providers_in_use.add(aux["provider"])
                for p in providers_in_use:
                    if p:
                        results[p] = {
                            "provider": p,
                            "status": "online",
                            "api_latency_ms": 0,
                        }
            except Exception:
                pass

        # Fallback: mark all known providers as online for route display.
        # Derive from usage_metrics_v2.PROVIDERS (single source of truth).
        try:
            from .usage_metrics_v2 import PROVIDERS as _known

        except Exception:
            _known = (
                "github",
                "gemini",
                "openai",
                "anthropic",
                "deepseek",
                "nvidia",
                "fireworks",
                "deepinfra",
            )
        for p in _known:
            if p not in results:
                results[p] = {"provider": p, "status": "online", "api_latency_ms": 0}
        return build_routes(results)
    except Exception:
        return {}


# ── Credential & Bypass helpers for doctor ──────────────────────────────


def _load_hermes_env() -> dict[str, str]:
    """Parse .env and .env.generated into a dict (generated wins on overlap)."""
    result: dict[str, str] = {}

    def _parse(path: str) -> None:
        if not os.path.exists(path):
            return
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    result[k] = v

    _parse(os.path.expanduser("~/.hermes/.env"))
    _parse(os.path.expanduser("~/.hermes/.env.generated"))
    return result


# Provider → (env_var, key_format_check)
#
# NOTE: this list is broader than usage_metrics_v2.PROVIDERS on purpose. It
# recognizes OpenAI-compatible providers (openrouter, zai) for credential
# validation in `install doctor`, even though they have no dedicated
# adapter/health/cost endpoint and therefore are NOT first-class entries in
# the usage registry — they route via the openai adapter / provider_routing.
# Keep this list, route_verifier._credential_for_provider, and the redaction
# patterns in security/redaction.py aligned when adding a provider.
_CREDENTIAL_CHECKS: list[tuple[str, str, str | None]] = [
    ("gemini", "GOOGLE_API_KEY", None),  # AI Studio keys have varied formats
    ("gemini", "GEMINI_API_KEY", None),
    ("openai", "OPENAI_API_KEY", "sk-"),
    ("anthropic", "ANTHROPIC_API_KEY", "sk-ant-"),
    ("deepseek", "DEEPSEEK_API_KEY", "sk-"),
    ("nvidia", "NVIDIA_API_KEY", "nvapi-"),
    (
        "fireworks",
        "FIREWORKS_API_KEY",
        None,
    ),  # fw-/fw_/fpk_ prefixes (see security/redaction.py)
    ("deepinfra", "DEEPINFRA_API_KEY", None),  # opaque tokens, no vendor prefix
    ("openrouter", "OPENROUTER_API_KEY", "sk-or-"),  # OpenAI-compat, no adapter
    (
        "zai",
        "GLM_API_KEY",
        None,
    ),  # Z.AI / ZhipuAI GLM — mirrors hermes_cli.auth zai ProviderConfig
    ("zai", "ZAI_API_KEY", None),
    ("zai", "Z_AI_API_KEY", None),
    ("github", "GITHUB_TOKEN", None),
    ("github", "GH_TOKEN", None),
    ("copilot", "GITHUB_TOKEN", None),  # copilot → github normalization
    ("copilot", "GH_TOKEN", None),
]


def _credential_for_provider(
    provider: str, env_vars: dict[str, str]
) -> tuple[bool, str]:
    """Return (has_credential, detail) for a provider."""
    provider_lower = provider.lower()
    for p, env_var, prefix in _CREDENTIAL_CHECKS:
        if p == provider_lower:
            val = env_vars.get(env_var, "")
            if val:
                if prefix and not val.startswith(prefix):
                    return True, f"{env_var} set but format looks unusual"
                return True, f"{env_var} set"
            # For GitHub, also check if `gh auth token` works
            if provider_lower in ("github", "copilot") and env_var == "GITHUB_TOKEN":
                continue  # check next env var
    # Fallback: check if any matching env var exists
    for p, env_var, _prefix in _CREDENTIAL_CHECKS:
        if p == provider_lower:
            val = env_vars.get(env_var, "")
            if val:
                return True, f"{env_var} set"
    return False, "no credential found (expected env var)"


def _check_credentials_section(
    env_vars: dict[str, str],
    _check,  # type: ignore[reportCallIssue] — inner _check closure
) -> None:
    """Validate credentials for providers referenced in config.yaml.

    Provider names from config.yaml are normalized (e.g. ``copilot`` →
    ``github``, ``openai-api`` → ``openai``) before looking up
    credentials, so fallback-chain entries like ``anthropic-api`` match
    the ``ANTHROPIC_API_KEY`` check.
    """
    cfg_path = os.path.expanduser("~/.hermes/config.yaml")
    providers_seen: set[str] = set()
    if os.path.exists(cfg_path):
        try:
            import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

            with open(cfg_path) as f:
                hc = _yaml.safe_load(f) or {}
            model = hc.get("model", {})
            providers_seen.add(model.get("provider", ""))
            for fb in hc.get("fallback_providers", []):
                providers_seen.add(fb.get("provider", ""))
            for aux in hc.get("auxiliary", {}).values():
                if isinstance(aux, dict):
                    p = aux.get("provider", "")
                    if p and p != "auto":
                        providers_seen.add(p)
        except Exception:
            pass

    # Normalize provider names: copilot→github, openai-api→openai, etc.
    try:
        from .usage_metrics_v2 import _PROVIDER_NORMALIZE  # pyright: ignore[reportMissingImports]
    except Exception:
        _PROVIDER_NORMALIZE: dict[str, str] = {}  # type: ignore[no-redef]

    for provider_raw in sorted(providers_seen):
        if not provider_raw:
            continue
        normalized = _PROVIDER_NORMALIZE.get(provider_raw, provider_raw)
        ok, detail = _credential_for_provider(normalized, env_vars)
        _check(provider_raw, ok, detail)  # type: ignore[call-arg]


def _check_bypass_section(
    cfg_path: str,
    env_vars: dict[str, str],
    _check,  # type: ignore[reportCallIssue]
) -> None:
    """Detect AUX routes that will be silently bypassed at runtime."""
    try:
        from .config.route_map import AUX_SHORT_KEYS, aux_config_key
    except ImportError:
        return
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        with open(cfg_path) as f:
            hc = _yaml.safe_load(f) or {}
    except Exception:
        return

    aux_cfg = hc.get("auxiliary", {}) or {}
    model_cfg = hc.get("model", {})
    agent_cfg = hc.get("agent", {}) or {}
    image_input_mode = agent_cfg.get("image_input_mode", "auto")

    for sk in AUX_SHORT_KEYS:
        config_key = aux_config_key(sk)
        slot = aux_cfg.get(config_key, {}) or {}
        provider = str(slot.get("provider", "auto") or "auto").strip()
        model = str(slot.get("model", "") or "").strip()

        if provider in ("auto", ""):
            _check(
                f"aux {sk}",
                False,
                "provider='auto' → resolves to primary at runtime",
            )
        elif not _credential_for_provider(provider, env_vars)[0]:
            _check(
                f"aux {sk}",
                False,
                f"provider={provider} but no credential found",
            )
        else:
            _check(
                f"aux {sk}",
                True,
                f"{provider}:{model or 'default'}",
            )

    # Native fast path check for vision.
    # In ``auto`` mode Hermes' ``decide_image_input_mode()`` calls
    # ``_explicit_aux_vision_override()`` first — when aux vision is
    # explicitly configured, images route through the aux LLM ("text"
    # mode).  Native fast path is only used when aux vision is NOT
    # configured AND the main model supports vision.  Warn accordingly.
    main_provider = model_cfg.get("provider", "")
    if image_input_mode == "auto" and main_provider:
        vision_cfg = aux_cfg.get("vision", {}) or {}
        vision_provider = str(vision_cfg.get("provider", "auto") or "auto").strip()
        if vision_provider in ("auto", ""):
            _check(
                "vision routing",
                False,
                f"aux vision not configured (provider=auto) + "
                f"image_input_mode=auto → images route through "
                f"main model ({main_provider}) natively if it supports vision",
            )
        else:
            _check(
                "vision routing",
                True,
                f"explicit aux vision ({vision_provider}) → "
                f"images route through aux LLM (text mode)",
            )


def _handle_route_test(args: list[str]) -> int:
    """Deterministic route verification — no live provider calls.

    hermes-ops-kit route-test              # Full report
    hermes-ops-kit route-test --fallback   # Fallback chain cascade
    hermes-ops-kit route-test --json       # Machine-readable
    """

    json_mode = "--json" in args
    fallback_only = "--fallback" in args

    cfg_path = os.path.expanduser("~/.hermes/config.yaml")
    hc: dict = {}
    if os.path.exists(cfg_path):
        try:
            import yaml as _y  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

            with open(cfg_path) as f:
                hc = _y.safe_load(f) or {}
        except ImportError:
            pass

    if fallback_only:
        # ── Fallback chain cascade ──────────────────────────────────
        from .route_runtime_harness import verify_fallback_chain

        report = verify_fallback_chain(hc)
        if json_mode:
            print(json.dumps(report, indent=2, default=str))
            return 0 if report["ok"] else 1

        print("Fallback Chain Verification")
        print(f"  primary: {report['primary']}")
        print(
            f"  steps:   {report['summary']['total']} configured, "
            f"{report['summary']['reachable']} reachable, "
            f"{report['summary']['unreachable']} unreachable"
        )
        print()
        for step in report["steps"]:
            status = "✅" if step["selected"] else "❌"
            print(
                f"  {status} step {step['step']}: "
                f"{step['configured_provider']}:{step['configured_model']}"
            )
            if not step["selected"]:
                print(
                    f"       expected: {step['expected_provider']}:{step['expected_model']}"
                )
                print(
                    f"       actual:   {step['actual_provider']}:{step['actual_model']}"
                )
                print(f"       offline:  {', '.join(step['offline_providers'])}")
        print()
        if report["summary"]["exhausted"]:
            print(
                "  ⚠️  Fallback chain exhausted — no provider available after last fallback."
            )
        elif report["ok"]:
            print("  ✅ Fallback chain intact — all configured providers reachable.")
        return 0 if report["ok"] else 1

    # ── Full route report ───────────────────────────────────────────
    from .route_runtime_harness import build_report

    try:
        routes_cfg_path = os.path.expanduser("~/.hermes/ops-kit/routes.yaml")
        if not os.path.exists(routes_cfg_path):
            routes_cfg_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "config", "routes.yaml"
            )
        import yaml as _y  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        with open(routes_cfg_path) as f:
            rc = _y.safe_load(f) or {}

        assistants_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "assistants.yaml"
        )
        with open(assistants_path) as f:
            ac = _y.safe_load(f) or {}

        img_path = os.path.expanduser("~/.hermes/ops-kit/image_routes.yaml")
        if not os.path.exists(img_path):
            img_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "config",
                "image_routes.yaml",
            )
        with open(img_path) as f:
            ic = _y.safe_load(f) or {}
    except Exception as e:
        print(f"Failed to load configs: {e}")
        return 1

    try:
        report = build_report(hc, rc, ac, ic)
    except Exception as e:
        print(f"Route verification failed: {e}")
        return 1
    if json_mode:
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["ok"] else 1

    print(
        f"Route Verification · ok={report['ok']} · "
        f"tested={report['summary']['total_routes_tested']}"
    )
    for entry in report["routes"]:
        icon = (
            "✅"
            if entry["result"] == "passed"
            else "⚠️"
            if entry["result"] == "failed"
            else "·"
        )
        print(
            f"  {icon} {entry['category']:<9s} {entry['route']:<18s} "
            f"{entry['result']:<8s} "
            f"{entry['actual_provider']}:{entry['actual_model']} → {entry['runtime_path']}"
        )
    return 0 if report["ok"] else 1


def _handle_mcp(args: list[str]) -> int:
    """Handle `hermes-ops-kit mcp ...` subcommands."""
    sub = args[0] if args else "audit"
    rest = args[1:] if len(args) > 1 else []
    try:
        from .mcp_auditor.auditor import (  # pyright: ignore[reportMissingImports]
            run_audit,
            approve_server,
            approve_tool,
            revoke_all,
            show_policy,
        )
        from .mcp_auditor.reporter import fmt_audit  # pyright: ignore[reportMissingImports]
    except Exception as e:
        print(f"MCP auditor unavailable: {e}")
        return 1

    if sub == "audit":
        result = run_audit()
        print(fmt_audit(result))
    elif sub == "list":
        result = run_audit()
        for s in result["servers"]:
            print(
                f"  ● {s['server_id']} ({s['transport']}) tools={len(s['tools'])} risk={s['risk']}"
            )
    elif sub == "risks":
        result = run_audit()
        for r in result["risks"]:
            action = (
                "APPROVED ✓"
                if r.get("approved")
                else "BLOCKED ✗"
                if r.get("blocked")
                else "REQUIRES APPROVAL"
            )
            print(f"  ⚠ {r['full_name']} risk={r['risk']} {action}")
    elif sub == "tools":
        return _mcp_tools(rest)
    elif sub == "approve":
        return _mcp_approve(
            rest, approve_server_fn=approve_server, approve_tool_fn=approve_tool
        )
    elif sub == "revoke":
        result = revoke_all()
        print(
            f"Revoked all MCP approvals ({result['entries_removed']} entries removed)"
        )
    elif sub == "policy":
        result = show_policy()
        policy = result.get("policy", {})
        servers = policy.get("approved_servers", [])
        tools = policy.get("approved_tools", [])
        if not servers and not tools:
            print("MCP POLICY · empty (no approvals)")
            print()
            print("Approve tools or servers:")
            print("  hermes-ops-kit mcp approve --server obsidian-mcp-vault")
            print("  hermes-ops-kit mcp approve --all")
        else:
            print("MCP POLICY\n")
            if servers:
                print(f"  approved servers ({len(servers)}):")
                for s in servers:
                    print(f"    ✓ {s}  (all tools)")
            if tools:
                print(f"  approved tools ({len(tools)}):")
                for t in tools:
                    print(f"    ✓ {t}")
    elif sub == "export":
        result = run_audit()
        import json

        print(json.dumps(result, indent=2, default=str))
    else:
        result = run_audit()
        print(fmt_audit(result))
    return 0


def _mcp_approve(
    args: list[str],
    approve_server_fn,
    approve_tool_fn,
) -> int:
    """Handle `hermes-ops-kit mcp approve ...`."""
    if not args or "--all" in args:
        # Approve all known MCP servers atomically
        server_ids = []
        try:
            from .mcp_auditor.auditor import inventory_servers  # pyright: ignore[reportMissingImports]

            for s in inventory_servers():
                server_ids.append(s["server_id"])
        except Exception:
            pass
        if not server_ids:
            server_ids = ["obsidian-mcp-vault"]
        for sid in server_ids:
            approve_server_fn(sid)
        print(f"Approved all MCP servers: {', '.join(server_ids)}")
        print("Run 'hermes-ops-kit mcp audit' to verify.")
        return 0

    i = 0
    while i < len(args):
        if args[i] == "--server" and i + 1 < len(args):
            approve_server_fn(args[i + 1])
            print(f"Approved server: {args[i + 1]}")
            i += 2
        elif args[i] == "--tool" and i + 1 < len(args):
            approve_tool_fn(args[i + 1])
            print(f"Approved tool: {args[i + 1]}")
            i += 2
        else:
            print(f"Unknown approve flag: {args[i]}")
            print(
                "Usage: hermes-ops-kit mcp approve [--all] [--server ID] [--tool FULL_NAME]"
            )
            return 1
    return 0


def _mcp_tools(args: list[str]) -> int:
    """Handle ``hermes-ops-kit mcp tools --server <id>``.

    Lists every tool for one or all servers with risk level and approval
    status, including low-risk tools omitted by ``mcp risks``.
    """
    # Resolve --server filter
    target_server: str | None = None
    i = 0
    while i < len(args):
        if args[i] == "--server" and i + 1 < len(args):
            target_server = args[i + 1]
            i += 2
        else:
            print(f"Unknown flag: {args[i]}")
            print("Usage: hermes-ops-kit mcp tools [--server SERVER_ID]")
            return 1

    try:
        from .mcp_auditor.auditor import run_audit  # pyright: ignore[reportMissingImports]
    except Exception as e:
        print(f"MCP auditor unavailable: {e}")
        return 1

    result = run_audit()

    for srv in result["servers"]:
        sid = srv["server_id"]
        if target_server and sid != target_server:
            continue
        tools = srv.get("tools", [])
        print(f"\n{sid} — {len(tools)} tools  (server risk: {srv['risk']})\n")
        if not tools:
            print("  (no tools discovered)")
            continue

        # Column widths
        name_w = max(len(t["tool_name"]) for t in tools) if tools else 20
        for t in tools:
            risk = t["risk"]
            # Status label
            if t.get("approved"):
                status = "APPROVED ✓"
            elif t.get("blocked"):
                status = "BLOCKED ✗"
            else:
                status = "REQUIRES APPROVAL"
            # Color-coded risk (no ANSI for compatibility with --no-color)
            print(f"  {t['tool_name']:<{name_w}s}  {risk:<8s}  {status}")
    print()
    return 0


def _handle_budget(args: list[str]) -> int:
    """Handle `hermes-ops-kit budget ...` subcommands."""
    sub = args[0] if args else "status"
    try:
        from .cost_governor.budget import evaluate_budget, check_route_allowed  # pyright: ignore[reportMissingImports]
    except Exception as e:
        print(f"Cost governor unavailable: {e}")
        return 1

    if sub == "status":
        s = evaluate_budget()
        status_icon = {
            "ok": "✅",
            "warn": "⚠",
            "throttle": "⚠",
            "restrict": "⛔",
            "blocked": "🚫",
        }.get(s["budget_status"], "?")
        print(f"BUDGET · {s['budget_status'].upper()}\n")
        print(
            f"  daily    ${s['daily_spend_usd']:.2f} / ${s['daily_budget_usd']:.2f}   {s['daily_percent']}%  {status_icon}"
        )
        print(
            f"  monthly  ${s['monthly_spend_usd']:.2f} / ${s['monthly_budget_usd']:.2f}   {s['monthly_percent']}%"
        )
        print(f"  mode     {s['enforcement_mode']}")
        if s["actions"]:
            print(f"  actions  {', '.join(s['actions'])}")
    elif sub == "check-route" and len(args) >= 2:
        provider = args[1]
        d = check_route_allowed(provider)
        print(
            f"  {provider}: {'✅ allowed' if d.allowed else '🚫 blocked'} — {d.reason}"
        )
    elif sub == "policy":
        s = evaluate_budget()
        print(f"  mode: {s['enforcement_mode']}")
        print(f"  prefer: {', '.join(s['preferred_providers'])}")
    return 0


def _handle_maintenance(args: list[str]) -> int:
    """Handle `hermes-ops-kit maintenance ...` subcommands."""
    sub = args[0] if args else "profiles"
    try:
        from .assistant_tasks.profiles import load_profiles, validate_profile  # pyright: ignore[reportMissingImports]
    except Exception as e:
        print(f"Vault scheduler unavailable: {e}")
        return 1

    profiles = load_profiles()
    if sub in ("profiles", "list"):
        print(f"ASSISTANT TASKS PROFILES ({len(profiles)})\n")
        for name, prof in profiles.items():
            ok, issues = validate_profile(name, prof)
            icon = "✅" if ok else "⚠"
            print(f"  {icon} {name}")
            print(
                f"    schedule: {prof.get('schedule', '?')}  capability: {prof.get('capability', '?')}"
            )
            if issues:
                for i in issues:
                    print(f"    ⚠ {i}")
    elif sub == "run" and len(args) >= 2:
        profile_name = args[1]
        prof = profiles.get(profile_name)
        if not prof:
            print(f"Profile not found: {profile_name}")
            return 1
        ok, issues = validate_profile(profile_name, prof)
        if not ok:
            print(f"Cannot run '{profile_name}':")
            for i in issues:
                print(f"  ⚠ {i}")
            return 1
        print(f"Would run profile '{profile_name}'")
        print(f"  assistant: {prof.get('assistant_id', '?')}")
        print(f"  operations: {', '.join(prof.get('operations', []))}")
        # In production: delegate via ai_assistant_delegate()
        print("  (delegation via ai_assistant_delegate — add to cron for automation)")
    return 0


def _handle_audit(args: list[str]) -> int:
    """Handle `hermes-ops-kit audit ...` subcommands."""
    sub = args[0] if args else "tail"
    rest = args[1:] if len(args) > 1 else []

    try:
        from .audit.ledger import tail_events, search_events  # pyright: ignore[reportMissingImports]
    except Exception as e:
        print(f"Audit ledger unavailable: {e}")
        return 1

    if sub == "tail":
        limit = int(rest[0]) if rest else 20
        events = tail_events(limit)
        if not events:
            print("No audit events")
            return 0
        print(f"AUDIT · last {len(events)} events\n")
        for evt in events:
            ts = evt.get("ts", "?")[-8:].replace("T", " ")
            etype = evt.get("type", "?")
            detail = ""
            if etype == "assistant_called":
                detail = f"{evt.get('assistant', '?')} {evt.get('capability', '?')} {evt.get('status', '?')} {evt.get('duration_ms', '?')}ms"
            elif etype == "key_rotated":
                detail = f"{evt.get('provider', '?')} {evt.get('operation', '?')} {evt.get('status', '?')}"
            elif etype == "policy_denied":
                detail = f"{evt.get('rule', '?')} {evt.get('reason', '?')[:60]}"
            else:
                detail = json.dumps(
                    {k: v for k, v in evt.items() if k not in ("ts", "type")},
                    default=str,
                )[:80]
            print(f"  {ts}  {etype:<25s} {detail}")
        return 0

    elif sub == "search":
        etype = None
        assistant = None
        provider = None
        i = 0
        while i < len(rest):
            if rest[i] == "--type" and i + 1 < len(rest):
                etype = rest[i + 1]
                i += 2
            elif rest[i] == "--assistant" and i + 1 < len(rest):
                assistant = rest[i + 1]
                i += 2
            elif rest[i] == "--provider" and i + 1 < len(rest):
                provider = rest[i + 1]
                i += 2
            else:
                i += 1
        events = search_events(event_type=etype, assistant=assistant, provider=provider)
        for evt in events:
            print(
                f"  {evt.get('ts', '?')}  {evt.get('type', '?')}  {json.dumps({k: v for k, v in evt.items() if k not in ('ts', 'type')}, default=str)[:100]}"
            )
        return 0

    elif sub == "export":
        events = tail_events(200)
        print(json.dumps(events, indent=2, default=str))
        return 0

    else:
        print("Usage: hermes-ops-kit audit [tail|search|export]")
        return 1


def _handle_assistants(args: list[str]) -> int:
    """Handle `hermes-ops-kit assistants ...` subcommands."""
    if not args:
        print("Subcommand required: list, ping, delegate")
        return 1

    sub = args[0]
    rest = args[1:]

    if sub == "list":
        # Quick assistant registry dump
        try:
            from .assistants.registry import load_registry  # pyright: ignore[reportMissingImports]

            registry = load_registry()
            for aid, cfg in registry.items():
                caps = [c["id"] for c in cfg.capabilities]
                print(f"  {cfg.display_name} ({cfg.role})")
                print(f"    capabilities: {', '.join(caps[:5])}")
                print(f"    transport: {cfg.transport}")
        except Exception as e:
            print(f"Error loading registry: {e}")
            return 1
        return 0

    elif sub == "ping":
        if not rest:
            print("Assistant ID required: hermes-ops-kit assistants ping <id>")
            return 1
        aid = rest[0]
        try:
            from .env.loader import load_dotenv  # noqa: E402  # pyright: ignore[reportMissingImports]
            from .assistants.registry import get_assistant  # pyright: ignore[reportMissingImports]
            from .assistants.client import AssistantClient  # pyright: ignore[reportMissingImports]

            load_dotenv()
            config = get_assistant(aid)
            if not config:
                print(f"Assistant '{aid}' not found")
                return 1
            client = AssistantClient(config)
            result = client.healthcheck()
            print(json.dumps(result, indent=2))
        except Exception as e:
            print(f"Error: {e}")
            return 1
        return 0

    elif sub == "delegate":
        if len(rest) < 2:
            print(
                'Usage: hermes-ops-kit assistants delegate <id> --capability X --task "..."'
            )
            return 1
        # Simple arg parsing
        aid = rest[0]
        capability = "review"
        task = ""
        i = 1
        while i < len(rest):
            if rest[i] == "--capability" and i + 1 < len(rest):
                capability = rest[i + 1]
                i += 2
            elif rest[i] == "--task" and i + 1 < len(rest):
                task = rest[i + 1]
                i += 2
            else:
                i += 1

        if not task:
            print("--task is required")
            return 1

        try:
            from .assistants.tool import ai_assistant_delegate  # pyright: ignore[reportMissingImports]

            result = ai_assistant_delegate(aid, task=task, capability=capability)
            print(json.dumps(result, indent=2))
        except Exception as e:
            print(f"Error: {e}")
            return 1
        return 0

    else:
        print(f"Unknown assistants subcommand: {sub}")
        return 1


def _handle_image(args: list[str]) -> int:
    """Handle `hermes-ops-kit image ...` subcommand.

    Delegates to image_routes/manager.py with the provided arguments.
    """
    return _run_script("image_routes/manager.py", args)


def _handle_headroom(args: list[str]) -> int:
    """Handle `hermes-ops-kit headroom ...` subcommand.

    Delegates to headroom_ops/manager.py with the provided arguments.
    """
    return _run_script("headroom_ops/manager.py", args)


def _handle_install(_args: list[str]) -> int:
    """Handle install setup / doctor / repair flow."""
    if not _args:
        _args = ["doctor"]

    subcmd = _args[0]
    rest = _args[1:]

    if subcmd == "setup":
        from .security.plugin_scanner.bootstrap import main as bootstrap_main  # pyright: ignore[reportMissingImports]

        return bootstrap_main(rest)

    if subcmd == "repair":
        backup_path = os.path.expanduser("~/.hermes/config.yaml.bak")
        config_path = os.path.expanduser("~/.hermes/config.yaml")
        if os.path.exists(backup_path):
            from .security.plugin_scanner.enforce import _restore_hermes_config  # pyright: ignore[reportMissingImports]

            try:
                _restore_hermes_config(backup_path)
            except Exception as exc:
                print(f"Repair failed: {exc}")
                return 1
            print(f"Restored {config_path} from {backup_path}")
            return 0
        print(f"No backup found at {backup_path}")
        return 1

    # Runtime installation reconciler (read-only) — flags:
    #   --json    machine-readable HealthReport
    #   --verbose technical detail (dist-info, evidence)
    #   --python <path>  explicit target interpreter (default: current)
    json_output = "--json" in rest
    verbose = "--verbose" in rest or "-v" in rest
    target_python = None
    if "--python" in rest:
        try:
            target_python = rest[rest.index("--python") + 1]
        except IndexError:
            print("--python requires a path argument")
            return 1

    from .install_reconciler.cli import format_report, print_json, run_install_doctor

    report = run_install_doctor(target_python=target_python)

    # Explicit repair mode (M2): only safe plans execute; reinspection
    # after install decides success — installer rc=0 alone is not enough.
    if "--repair" in rest:
        from .install_reconciler.repair import perform_repair

        if json_output:
            print_json(report)
        if report.overall.value == "HEALTHY":
            if not json_output:
                print("Nothing to repair — installation is HEALTHY (no-op).")
            return 0
        outcome = perform_repair(report)
        if json_output:
            plan_dict = outcome.plan.to_dict() if outcome.plan else {}
            print(
                json.dumps(
                    {
                        "repaired": outcome.success,
                        "changed": outcome.changed,
                        "reason": outcome.failure_reason()
                        if not outcome.success
                        else "healthy after reinspection",
                        "plan": plan_dict,
                    },
                    indent=2,
                )
            )
        elif outcome.success:
            print("Repair applied and verified (reinspection HEALTHY).")
        elif not outcome.plan or not outcome.plan.safe_to_apply:
            reason = outcome.plan.safety_reason if outcome.plan else "no plan"
            print(f"Repair withheld — {reason}")
            print("Diagnose with: hermes-ops-kit install doctor --verbose")
        else:
            print(f"Repair FAILED: {outcome.failure_reason()}")
            print(
                "No success declared — runtime state unchanged or degraded; rerun doctor."
            )
        return 0 if outcome.success else 1

    checks = []

    # Python version
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}"
    checks.append(("Python >= 3.11", sys.version_info >= (3, 11), pyver))

    # bw CLI
    import shutil

    bw = shutil.which("bw")
    checks.append(("bw CLI", bw is not None, bw or "not found"))

    # Headroom proxy (optional — informational only, never blocks install)
    headroom_bin = shutil.which("headroom")
    checks.append(
        (
            "headroom (optional)",
            True,
            headroom_bin or "not installed (pipx install headroom-ai)",
        )
    )

    # Env file permissions — check .env.generated first, then .env
    generated = os.path.expanduser("~/.hermes/.env.generated")
    env_path = (
        generated if os.path.exists(generated) else os.path.expanduser("~/.hermes/.env")
    )
    env_ok = False
    if os.path.exists(env_path):
        import stat

        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        env_ok = mode == 0o600
        checks.append(
            ("~/.hermes/.env 0600", env_ok, f"{oct(mode)[2:]}" if not env_ok else "ok")
        )
    else:
        checks.append(("~/.hermes/.env", False, "not found"))

    # Print results (human mode only — --json emits a single pure JSON doc)
    all_ok = True
    for name, ok, detail in checks:
        if not ok:
            all_ok = False
        if not json_output:
            icon = "✅" if ok else "❌"
            print(f"  {icon} {name}: {detail}")

    # Reconciler section (read-only)
    if json_output:
        print_json(report)
        reconciler_ok = report.overall.value == "HEALTHY"
    else:
        print()
        print(format_report(report, verbose=verbose))
        reconciler_ok = report.overall.value == "HEALTHY"
        reconciler_ok = report.overall.value == "HEALTHY"

    return 0 if (all_ok and reconciler_ok) else 1


def _handle_plugin(args: list[str]) -> int:
    """Handle plugin security scanner subcommands.

    Delegates to security/plugin_scanner/cli.py:handle_plugin().
    """
    from .security.plugin_scanner.cli import handle_plugin  # pyright: ignore[reportMissingImports]

    return handle_plugin(args)


def _handle_preflight(args: list[str]) -> int:
    """Handle preflight plugin security enforcement.

    Scans all plugins, compares results against the approval policy,
    and synchronizes ~/.hermes/config.yaml so that blocked/disabled
    plugins are excluded from Hermes' plugin loading.

    Delegates to security/plugin_scanner/enforce.py:main().
    """
    from .security.plugin_scanner.enforce import main  # pyright: ignore[reportMissingImports]

    return main(args)
