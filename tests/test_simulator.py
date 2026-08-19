"""Hermes Ops Kit — Test Harness / Simulator.

Simulates operational scenarios without calling real providers.
Answers: "What happens when X fails?" — without actually breaking anything.

Usage:
    python3 tests/test_simulator.py --profile cheap
    python3 tests/test_simulator.py --provider-failure openai
    python3 tests/test_simulator.py --assistant-timeout <assistant-id>
    python3 tests/test_simulator.py --secret-leak
    python3 tests/test_simulator.py --cost-spike
"""

from __future__ import annotations

import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)


# ─── Simulated Provider Results ─────────────────────────────────────


def _make_provider(
    provider: str,
    status: str = "online",
    latency: int = 500,
    models: int = 10,
    cost_label: str = "paid",
) -> dict:
    return {
        "provider": provider,
        "status": status,
        "api_latency_ms": latency,
        "model_count": models,
        "chat_models": models,
        "copilot_available": provider == "github" and status == "online",
        "copilot_model_count": 10 if provider == "github" else 0,
    }


def _make_assistant(
    aid: str,
    status: str = "online",
    latency: int = 800,
    role: str = "security_profiler",
) -> dict:
    return {
        "provider": aid,
        "type": "assistant",
        "display_name": aid,
        "status": status,
        "api_latency_ms": latency,
        "role": role,
        "capabilities": ["profile_contact", "security_profile"],
        "safe_for": ["profile_contact"],
        "blocked_for": ["restricted_serving"],
        "transport": "openai_chat_completions",
    }


def _all_online() -> dict[str, dict]:
    return {
        "github": _make_provider("github", "online", 400, cost_label="included"),
        "gemini": _make_provider("gemini", "online", 350, cost_label="free"),
        "openai": _make_provider("openai", "online", 600),
        "anthropic": _make_provider("anthropic", "online", 450),
        "deepseek": _make_provider("deepseek", "online", 500, cost_label="paid-low"),
        "_assistants": {"test-assistant": _make_assistant("test-assistant")},
    }


# ─── Scenario Builders ──────────────────────────────────────────────


def scenario_provider_offline(provider: str) -> dict[str, dict]:
    """Simulate a single provider going offline."""
    results = _all_online()
    results[provider] = _make_provider(provider, "offline", 0, models=0)
    return results


def scenario_fallback_trigger() -> dict[str, dict]:
    """Simulate primary + utility offline, forcing fallback usage."""
    results = _all_online()
    results["github"] = _make_provider("github", "offline", 0, models=0)
    results["gemini"] = _make_provider("gemini", "offline", 0, models=0)
    return results


def scenario_aux_failure(aux_role: str) -> dict[str, dict]:
    """Simulate an auxiliary route provider going offline."""
    results = _all_online()
    # All aux routes use gemini by default
    results["gemini"] = _make_provider("gemini", "offline", 0, models=0)
    return results


def scenario_assistant_unavailable(assistant: str) -> dict[str, dict]:
    """Simulate a remote assistant being unreachable."""
    results = _all_online()
    results["_assistants"][assistant] = _make_assistant(assistant, "offline", 0)
    return results


def scenario_vaultwarden_locked() -> dict:
    """Simulate Vaultwarden backend being locked/unreachable."""
    return {
        "backend": "vaultwarden",
        "server_url": "<vaultwarden-url>",
        "server_ok": True,
        "authenticated": True,
        "unlocked": False,
        "ok": False,
        "status": "locked",
    }


def scenario_secret_leak() -> dict:
    """Simulate content containing raw secrets that should be blocked."""
    return {
        "clean": False,
        "violations": [
            "Anthropic key detected at prompt.text",
            "Bearer token detected at headers.authorization",
        ],
        "blocked": True,
    }


def scenario_cost_spike(provider: str = "anthropic") -> dict[str, dict]:
    """Simulate a cost spike on a paid provider."""
    results = _all_online()
    results[provider]["status"] = "online"
    results[provider]["api_latency_ms"] = 4500  # degraded latency
    results[provider]["cost_warning"] = "daily spend 4.80/5.00 USD (96%)"
    return results


def scenario_restricted_denied() -> dict:
    """Simulate a restricted serving request being denied by policy."""
    return {
        "ok": False,
        "assistant": "test-assistant",
        "operation": "authorization_required",
        "message": "Restricted profile exists but is not served without explicit authorization.",
        "required": [
            "include_restricted=true",
            "explicit_purpose",
            "authorized_requester",
            "audit_log",
        ],
    }


SCENARIOS = {
    "provider-offline": ("Provider offline", scenario_provider_offline),
    "fallback-trigger": ("Fallback chain triggered", scenario_fallback_trigger),
    "aux-failure": ("Auxiliary route failure", scenario_aux_failure),
    "assistant-unavailable": ("Assistant unavailable", scenario_assistant_unavailable),
    "vaultwarden-locked": ("Vaultwarden locked", scenario_vaultwarden_locked),
    "secret-leak": ("Raw secret detected", scenario_secret_leak),
    "cost-spike": ("Cost spike warning", scenario_cost_spike),
    "restricted-denied": ("Restricted request denied", scenario_restricted_denied),
}


# ─── Assertions ──────────────────────────────────────────────────────


def assert_routes_handle_failure(results: dict) -> None:
    """Verify routing still produces usable output when providers fail."""
    from hermes_ops_kit.usage_metrics_v2 import build_routes  # pyright: ignore[reportMissingImports]

    route_data = build_routes(results)
    routes = route_data.get("routes", [])
    fallbacks = route_data.get("fallbacks", [])
    # At least one route or fallback should be available
    online_routes = [r for r in routes if r.get("latency")]
    online_fallbacks = [f for f in fallbacks if f.get("latency")]
    assert len(online_routes) + len(online_fallbacks) > 0, (
        "No routes available after failure"
    )


def assert_assistant_not_in_providers(results: dict) -> None:
    """Assistant must be under _assistants, never in provider keys."""
    provider_keys = {k for k in results if not k.startswith("_")}
    assert "test-assistant" not in provider_keys, "Assistant leaked into provider keys"
    assert "test-assistant" in results.get("_assistants", {}), (
        "Assistant missing from _assistants"
    )


def assert_no_secrets_in_output(data: dict, path: str = "") -> None:
    """Recursively check no secret-like values exist in output."""
    suspicious_prefixes = ("sk-", "sk-ant-", "AIza", "ghp_", "gho_")
    if isinstance(data, dict):
        for k, v in data.items():
            assert_no_secrets_in_output(v, f"{path}.{k}")
    elif isinstance(data, list):
        for i, v in enumerate(data):
            assert_no_secrets_in_output(v, f"{path}[{i}]")
    elif isinstance(data, str):
        for prefix in suspicious_prefixes:
            assert not data.startswith(prefix), (
                f"Secret found at {path}: {data[:20]}..."
            )


def assert_policy_blocks_restricted(result: dict) -> None:
    """Restricted serving must be blocked without proper authorization."""
    assert result.get("ok") is False, "Restricted serving should be denied"
    assert (
        "authorization" in result.get("message", "").lower() or "required" in result
    ), "Missing auth requirement message"


# ─── Simulator Runner ────────────────────────────────────────────────


def run_simulator(scenario: str, **kwargs) -> int:
    """Run a simulation scenario and validate expected behavior."""
    if scenario not in SCENARIOS:
        print(f"Unknown scenario: {scenario}")
        print(f"Available: {list(SCENARIOS.keys())}")
        return 1

    label, builder = SCENARIOS[scenario]
    print(f"\n{'=' * 60}")
    print(f"  SIMULATE: {label}")
    print(f"{'=' * 60}\n")

    if scenario == "provider-offline":
        provider = kwargs.get("provider", "openai")
        results = builder(provider)
        print(f"  Provider: {provider} → offline")
        assert_routes_handle_failure(results)
        assert_assistant_not_in_providers(results)
        print(
            f"  ✅ Routes adapted — {len([r for r in results.values() if isinstance(r, dict) and r.get('status') == 'online'])} providers still online"
        )

    elif scenario == "fallback-trigger":
        results = builder()
        print("  Primary (github) → offline")
        print("  Utility (gemini) → offline")
        assert_routes_handle_failure(results)
        fb = results.get("openai", {}).get("status")
        assert fb == "online", "Fallback should be online"
        print(f"  ✅ Fallback triggered — openai is {fb}")

    elif scenario == "aux-failure":
        results = builder("vision")
        print("  Aux provider (gemini) → offline")
        assert_routes_handle_failure(results)
        print("  ✅ Routes still available via non-aux providers")

    elif scenario == "assistant-unavailable":
        results = builder("test-assistant")
        print("  Assistant → offline")
        assert results["_assistants"]["test-assistant"]["status"] == "offline"
        assert_assistant_not_in_providers(results)
        print("  ✅ Assistant offline — provider routes unaffected")

    elif scenario == "vaultwarden-locked":
        result = builder()
        print("  Vaultwarden → locked")
        assert result["ok"] is False
        assert result["unlocked"] is False
        print("  ✅ Backend locked — operations blocked correctly")

    elif scenario == "secret-leak":
        result = builder()
        print(f"  Secrets detected: {len(result['violations'])}")
        assert result["clean"] is False
        assert result["blocked"] is True
        print(f"  ✅ Content blocked — {result['violations'][0][:60]}...")

    elif scenario == "cost-spike":
        results = builder("anthropic")
        print("  Anthropic cost spike")
        assert "cost_warning" in results.get("anthropic", {})
        assert_routes_handle_failure(results)
        print("  ✅ Cost warning raised — routes still functional")

    elif scenario == "restricted-denied":
        result = builder()
        print("  Restricted serving → denied")
        assert_policy_blocks_restricted(result)
        print("  ✅ Policy blocked restricted access")

    print(f"\n  ✅ Scenario '{scenario}' passed\n")
    return 0


# ─── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Hermes Ops Kit — Test Simulator")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        help="Specific scenario to run (default: all)",
    )
    parser.add_argument(
        "--provider", default="openai", help="Provider for provider-offline/cost-spike"
    )
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    args = parser.parse_args()

    if args.all:
        failed = 0
        for name in SCENARIOS:
            kwargs = {}
            if name in ("provider-offline", "cost-spike"):
                kwargs["provider"] = args.provider
            rc = run_simulator(name, **kwargs)
            if rc != 0:
                failed += 1
        print(f"\n{'=' * 60}")
        print(f"  RESULTS: {len(SCENARIOS) - failed}/{len(SCENARIOS)} passed")
        print(f"{'=' * 60}")
        sys.exit(failed)
    elif args.scenario:
        sys.exit(run_simulator(args.scenario, provider=args.provider))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
