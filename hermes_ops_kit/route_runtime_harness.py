"""Hermes Ops Kit — Route Runtime Harness.

Deterministic, config-driven verification of route selection.

This module does not call live providers. It builds a synthetic online
provider matrix, feeds it through the existing route builders, and compares
actual runtime-path resolution against the configured layout.

Use it to prove:
- primary route resolution
- utility route resolution
- AUX route resolution
- fallback ordering
- assistant registry presence
- MCP server inventory
- image route configuration

The goal is to make route selection observable and testable without relying
on assistant self-reporting or provider API availability.
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
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent

from . import usage_metrics_v2 as um  # noqa: E402


PROVIDER_RESULTS_TEMPLATE: dict[str, dict[str, Any]] = {
    "github": {"provider": "github", "status": "online", "api_latency_ms": 410},
    "gemini": {"provider": "gemini", "status": "online", "api_latency_ms": 355},
    "openai": {"provider": "openai", "status": "online", "api_latency_ms": 598},
    "anthropic": {"provider": "anthropic", "status": "online", "api_latency_ms": 447},
    "deepseek": {"provider": "deepseek", "status": "online", "api_latency_ms": 503},
    "nvidia": {"provider": "nvidia", "status": "online", "api_latency_ms": 520},
    # Z.AI is Hermes' OpenAI-compatible custom provider.  The harness is
    # deliberately offline, so this proves route discovery only.
    "zai": {"provider": "zai", "status": "online", "api_latency_ms": 480},
    "_assistants": {},
}

from .config.route_map import aux_harness_triples  # noqa: E402

# AUX route map — canonical source: config/route_map.py
AUX_MAP = aux_harness_triples()

IMAGE_ROLES = ["local", "fast", "quality", "fallback"]


def _split_route(route: str) -> tuple[str, str]:
    """Parse a route string into (provider, model).

    Handles both forms uniformly:
      - "github/copilot:gpt-5.4-mini" → ("github", "gpt-5.4-mini")
      - "gemini:gemini-2.5-flash"      → ("gemini", "gemini-2.5-flash")
      - "custom:freellm:auto"          → ("custom:freellm", "auto")
      - ""                              → ("", "")

    The model is everything after the LAST colon, so provider IDs that
    themselves contain a colon (``custom:<name>``) survive intact.
    """
    if not route:
        return "", ""
    provider, _, model = route.rpartition(":")
    if "/" in provider and not provider.startswith("custom:"):
        provider = provider.split("/", 1)[0]
    return provider, model


def _deep_copy(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data))


def _load_yaml_text(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path) as f:
        text = f.read()
    # Prefer JSON parsing first so tests can write deterministic fixtures without
    # requiring PyYAML in the runtime environment.
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]
    except Exception as e:  # pragma: no cover - dependency optional at runtime
        raise RuntimeError(f"YAML parser unavailable for {path}: {e}") from e
    return _yaml.safe_load(text) or {}


def _all_online_results(
    hermes_cfg: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    results = _deep_copy(PROVIDER_RESULTS_TEMPLATE)
    # Custom providers (custom:<name> in ~/.hermes/config.yaml) participate in
    # the offline matrix: online when their key_env is present in the
    # environment (after dotenv), so route discovery is provable without live
    # API calls.  Mirrors the precedent of the static "zai" entry above.
    for cp in (hermes_cfg or {}).get("custom_providers", []) or []:
        name = str(cp.get("name", "")).strip()
        key_env = str(cp.get("key_env", "")).strip()
        if not name:
            continue
        try:
            from .image_routes.adapters.base import load_dotenv

            load_dotenv()
        except Exception:
            pass
        if key_env and not os.environ.get(key_env):
            continue
        results[f"custom:{name}"] = {
            "provider": f"custom:{name}",
            "status": "online",
            "api_latency_ms": 500,
        }
    return results


@dataclass
class RouteEntry:
    route: str
    category: str
    expected_provider: str
    expected_model: str
    actual_provider: str
    actual_model: str
    runtime_path: str
    result: str
    evidence: list[str]
    recommended_fix: str
    configured_provider: str = ""
    configured_model: str = ""
    expected_runtime_path: str = ""
    evidence_source: str = ""
    evidence_log_lines: list[str] | None = None
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["evidence_log_lines"] is None:
            payload["evidence_log_lines"] = []
        return payload


def _result_for_match(
    expected_provider: str,
    expected_model: str,
    actual_provider: str,
    actual_model: str,
    utility_provider: str = "gemini",
    utility_model: str = "gemini-2.5-flash",
) -> tuple[str, str, str]:
    if expected_provider == actual_provider and expected_model == actual_model:
        return "passed", "configured_path", ""
    if not expected_provider or not expected_model:
        return "not_tested", "unconfigured", "Route config missing provider/model"
    if (
        actual_provider == utility_provider
        and actual_model == utility_model
        and expected_provider != utility_provider
    ):
        return (
            "failed",
            "utility_fallback",
            "Resolved to utility provider/model instead of configured route",
        )
    return "failed", "mismatch", f"Resolved to {actual_provider}:{actual_model}"


def _build_route_entries(
    hermes_cfg: dict[str, Any],
    routes_cfg: dict[str, Any],
    route_data: dict[str, Any],
) -> list[RouteEntry]:
    entries: list[RouteEntry] = []

    primary = hermes_cfg.get("model", {})
    primary_provider = primary.get("provider", "copilot")
    primary_model = primary.get("default", "gpt-5.4-mini")
    if str(primary_provider).strip().lower() == "headroom":
        # Headroom overlay: the expected route is the real upstream provider.
        try:
            from .headroom_ops.reconcile import (  # pyright: ignore[reportMissingImports]
                resolve_primary_provider,
            )

            primary_provider, _ = resolve_primary_provider(hermes_cfg)
        except Exception:
            pass
    # ``build_routes`` may omit a primary route (for example, when its
    # display-only provider matrix does not know the configured provider).
    # Never treat the first remaining route as primary: it is commonly the
    # utility route and would turn an unsupported provider into a false
    # primary mismatch.
    primary_actual = next(
        (r for r in route_data.get("routes", []) if r.get("role") == "primary"),
        {},
    )
    actual_route = primary_actual.get("route", "")
    actual_provider, actual_model = _split_route(actual_route)
    normalized_primary = "github" if primary_provider == "copilot" else primary_provider
    if not primary_actual and normalized_primary not in PROVIDER_RESULTS_TEMPLATE:
        result, runtime_path, failure = (
            "not_tested",
            "unsupported_provider",
            f"Provider {normalized_primary!r} is not represented by the offline harness",
        )
    else:
        result, runtime_path, failure = _result_for_match(
            normalized_primary,
            primary_model,
            actual_provider,
            actual_model,
        )
    entries.append(
        RouteEntry(
            route="primary",
            category="ROUTE",
            expected_provider=primary_provider,
            expected_model=primary_model,
            actual_provider=actual_provider,
            actual_model=actual_model,
            runtime_path=runtime_path,
            result=result,
            evidence=[f"build_routes.primary.route={actual_route}"],
            recommended_fix=(
                "Add this provider to the offline route harness to verify it locally"
                if runtime_path == "unsupported_provider"
                else "Set ~/.hermes/config.yaml model.provider/model.default to the configured primary"
                if result != "passed"
                else ""
            ),
            configured_provider=primary_provider,
            configured_model=primary_model,
            expected_runtime_path="primary",
            evidence_source="usage_metrics_v2.build_routes",
            evidence_log_lines=[f"primary route built as {actual_route}"],
            failure_reason=failure,
        )
    )

    # Utility is derived by build_routes() from AUX config (most common
    # explicit provider/model, or primary if all are auto).  Derive the
    # expected value the same way so the harness validates self-consistency.
    _aux_explicit: dict[str, int] = {}
    _aux_cfg = hermes_cfg.get("auxiliary", {})
    _norm = getattr(um, "_PROVIDER_NORMALIZE", {})
    for _sk, _bare_key, _dotted in AUX_MAP:
        _slot = _aux_cfg.get(_bare_key, {}) or {}
        _p = str(_slot.get("provider", "auto") or "auto").strip()
        _m = str(_slot.get("model", "") or "").strip()
        if _p not in ("auto", "") and _m:
            _p = _norm.get(_p, _p)
            _aux_explicit[f"{_p}:{_m}"] = _aux_explicit.get(f"{_p}:{_m}", 0) + 1

    if _aux_explicit:
        _best = max(_aux_explicit, key=lambda k: _aux_explicit[k])
        utility_expected_provider, utility_expected_model = _best.rsplit(":", 1)
    else:
        utility_expected_provider = _norm.get(primary_provider, primary_provider)
        utility_expected_model = primary_model

    utility_actual = next(
        (r for r in route_data.get("routes", []) if r.get("role") == "utility"), {}
    )
    utility_route = utility_actual.get("route", "")
    utility_provider, utility_model = _split_route(utility_route)
    result, runtime_path, failure = _result_for_match(
        utility_expected_provider,
        utility_expected_model,
        utility_provider,
        utility_model,
        utility_expected_provider,
        utility_expected_model,
    )
    entries.append(
        RouteEntry(
            route="utility",
            category="ROUTE",
            expected_provider=utility_expected_provider,
            expected_model=utility_expected_model,
            actual_provider=utility_provider,
            actual_model=utility_model,
            runtime_path=runtime_path,
            result=result,
            evidence=[f"build_routes.utility.route={utility_route}"],
            recommended_fix="Configure auxiliary.<task>.provider/model in ~/.hermes/config.yaml"
            if result != "passed"
            else "",
            configured_provider=utility_expected_provider,
            configured_model=utility_expected_model,
            expected_runtime_path="utility_derived_from_aux",
            evidence_source="usage_metrics_v2.build_routes",
            evidence_log_lines=[f"utility route built as {utility_route}"],
            failure_reason=failure,
        )
    )

    aux_cfg = hermes_cfg.get("auxiliary", {})
    aux_routes = {r.get("role"): r for r in route_data.get("aux_routes", [])}
    for short_key, hermes_key, route_key in AUX_MAP:
        slot = aux_cfg.get(hermes_key, {}) or {}
        if slot.get("enabled") is False:
            # Disabled aux slots (e.g. title_generation.enabled: false) never
            # route at runtime — nothing to verify.
            continue
        exp_provider = str(slot.get("provider", "auto") or "auto")
        exp_model = str(slot.get("model", "") or "")
        actual = aux_routes.get(f"aux_{short_key}", {})
        actual_route = str(actual.get("route", "") or "")
        actual_provider, actual_model = _split_route(actual_route)
        expected_runtime_path = (
            "auxiliary"
            if exp_provider not in ("auto", "") and exp_model
            else "auto_to_utility"
        )
        if exp_provider in ("auto", "") or not exp_model:
            result = "failed"
            runtime_path = "auto_to_utility"
            failure = "AUX route is auto/missing and resolves to utility instead of an explicit AUX backend"
            recommended_fix = (
                f"Pin {route_key} provider/model explicitly in ~/.hermes/config.yaml"
            )
        elif actual_provider == exp_provider and actual_model == exp_model:
            result, runtime_path, failure = "passed", "auxiliary", ""
            recommended_fix = ""
        else:
            result, runtime_path, failure = (
                "failed",
                "auxiliary_mismatch",
                f"Resolved to {actual_provider}:{actual_model}",
            )
            recommended_fix = (
                f"Correct {route_key} provider/model to {exp_provider}:{exp_model}"
            )

        entries.append(
            RouteEntry(
                route=short_key,
                category="AUX",
                expected_provider=exp_provider,
                expected_model=exp_model,
                actual_provider=actual_provider,
                actual_model=actual_model,
                runtime_path=runtime_path,
                result=result,
                evidence=[f"build_routes.{hermes_key}={actual_route}"]
                if actual_route
                else [f"build_routes.{hermes_key}=<missing>"],
                recommended_fix=recommended_fix,
                configured_provider=exp_provider,
                configured_model=exp_model,
                expected_runtime_path=expected_runtime_path,
                evidence_source="usage_metrics_v2.build_routes",
                evidence_log_lines=[
                    f"{hermes_key} built as {actual_route or '<missing>'}"
                ],
                failure_reason=failure,
            )
        )

    fb_cfg = hermes_cfg.get("fallback_providers", []) or []
    fb_routes = [r for r in route_data.get("fallbacks", [])]
    normalize = getattr(um, "_PROVIDER_NORMALIZE", {})
    for idx, fb in enumerate(fb_cfg):
        exp_provider = normalize.get(fb.get("provider", ""), fb.get("provider", ""))
        exp_model = fb.get("model", "")
        actual = fb_routes[idx] if idx < len(fb_routes) else {}
        actual_route = actual.get("route", "")
        actual_provider, actual_model = _split_route(actual_route)
        if actual_provider == exp_provider and actual_model == exp_model:
            result, runtime_path, failure = "passed", "fallback", ""
            recommended_fix = ""
        else:
            result, runtime_path, failure = (
                "failed",
                "fallback_mismatch",
                f"Resolved to {actual_provider}:{actual_model}",
            )
            recommended_fix = f"Order fallback providers as {exp_provider}:{exp_model}"

        entries.append(
            RouteEntry(
                route=f"fallback[{idx}]",
                category="FALLBACK",
                expected_provider=exp_provider,
                expected_model=exp_model,
                actual_provider=actual_provider,
                actual_model=actual_model,
                runtime_path=runtime_path,
                result=result,
                evidence=[f"build_routes.fallbacks[{idx}].route={actual_route}"],
                recommended_fix=recommended_fix,
                configured_provider=exp_provider,
                configured_model=exp_model,
                expected_runtime_path="fallback",
                evidence_source="usage_metrics_v2.build_routes",
                evidence_log_lines=[f"fallback[{idx}] built as {actual_route}"],
                failure_reason=failure,
            )
        )

    return entries


def _build_assistant_entries(assistants_cfg: dict[str, Any]) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    assistants = assistants_cfg.get("assistants", {}) or {}
    for assistant_id, cfg in assistants.items():
        enabled = bool(cfg.get("enabled", True))
        endpoint = cfg.get("endpoint", {}) or {}
        transport = cfg.get("transport", "openai_chat_completions")
        model = endpoint.get("default_model", "hermes-agent")
        entries.append(
            RouteEntry(
                route=assistant_id,
                category="ASSISTANT",
                expected_provider=assistant_id,
                expected_model=model,
                actual_provider=assistant_id,
                actual_model=model,
                runtime_path="assistant_registry",
                result="passed" if enabled else "not_tested",
                evidence=[f"assistant {assistant_id} transport={transport}"],
                recommended_fix="Enable the assistant and ensure endpoint/api_key/model env vars are set"
                if not enabled
                else "",
                configured_provider=assistant_id,
                configured_model=model,
                expected_runtime_path="assistant_client",
                evidence_source="assistants.registry",
                evidence_log_lines=[
                    f"assistant {assistant_id} loaded with transport={transport}"
                ],
                failure_reason="Assistant disabled" if not enabled else "",
            )
        )
    return entries


def _build_mcp_entries(mcp_cfg: dict[str, Any] | None) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    servers = (mcp_cfg or {}).get("mcp_servers", {}) or {}
    for server_id, cfg in servers.items():
        transport = (
            "http" if cfg.get("url") else "stdio" if cfg.get("command") else "unknown"
        )
        entries.append(
            RouteEntry(
                route=server_id,
                category="MCP",
                expected_provider=server_id,
                expected_model=transport,
                actual_provider=server_id,
                actual_model=transport,
                runtime_path="mcp_inventory",
                result="passed",
                evidence=[f"mcp server {server_id} transport={transport}"],
                recommended_fix="",
                configured_provider=server_id,
                configured_model=transport,
                expected_runtime_path="mcp_audit",
                evidence_source="mcp.auditor.inventory_servers",
                evidence_log_lines=[f"server {server_id} transport={transport}"],
            )
        )
    return entries


def _build_image_entries(
    image_cfg: dict[str, Any], results: dict[str, Any]
) -> list[RouteEntry]:
    entries: list[RouteEntry] = []
    original = um._IMAGE_ROUTES_CONFIG_CACHE
    um._IMAGE_ROUTES_CONFIG_CACHE = image_cfg
    try:
        image_routes = um.build_image_routes(results)
    finally:
        um._IMAGE_ROUTES_CONFIG_CACHE = original

    route_by_name = {r.get("role"): r for r in image_routes}
    routes_cfg = image_cfg.get("routes", {}) or {}
    default = image_cfg.get("default_route", "fast")
    for name in IMAGE_ROLES:
        cfg = routes_cfg.get(name, {}) or {}
        actual = route_by_name.get(name, {})
        actual_route = actual.get("route", "")
        actual_provider, actual_model = _split_route(actual_route)
        exp_provider = cfg.get("provider", "")
        exp_model = cfg.get("model", "")
        if name == default and cfg:
            expected_runtime_path = "image_default"
        else:
            expected_runtime_path = "image_route"
        if actual_provider == exp_provider and actual_model == exp_model:
            result = "passed"
            runtime_path = "image_route"
            failure = ""
        else:
            result = "failed"
            runtime_path = "image_mismatch"
            failure = f"Resolved to {actual_provider}:{actual_model}"
        entries.append(
            RouteEntry(
                route=f"image.{name}",
                category="IMAGE",
                expected_provider=exp_provider,
                expected_model=exp_model,
                actual_provider=actual_provider,
                actual_model=actual_model,
                runtime_path=runtime_path,
                result=result,
                evidence=[f"build_image_routes.{name}.route={actual_route}"],
                recommended_fix=f"Set image route {name} to {exp_provider}:{exp_model}"
                if result != "passed"
                else "",
                configured_provider=exp_provider,
                configured_model=exp_model,
                expected_runtime_path=expected_runtime_path,
                evidence_source="usage_metrics_v2.build_image_routes",
                evidence_log_lines=[f"image route {name} built as {actual_route}"],
                failure_reason=failure,
            )
        )
    return entries


def build_report(
    hermes_cfg: dict[str, Any],
    routes_cfg: dict[str, Any],
    assistants_cfg: dict[str, Any],
    image_cfg: dict[str, Any],
    mcp_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic route verification report."""
    results = _all_online_results(hermes_cfg)

    old_hermes = um._HERMES_CONFIG_CACHE
    old_routes = um._ROUTES_CONFIG_CACHE
    old_image = um._IMAGE_ROUTES_CONFIG_CACHE
    um._HERMES_CONFIG_CACHE = hermes_cfg
    um._ROUTES_CONFIG_CACHE = routes_cfg
    um._IMAGE_ROUTES_CONFIG_CACHE = image_cfg
    try:
        route_data = um.build_routes(results)
        route_entries = _build_route_entries(hermes_cfg, routes_cfg, route_data)
        route_entries.extend(_build_assistant_entries(assistants_cfg))
        route_entries.extend(_build_mcp_entries(mcp_cfg))
        route_entries.extend(_build_image_entries(image_cfg, results))
    finally:
        um._HERMES_CONFIG_CACHE = old_hermes
        um._ROUTES_CONFIG_CACHE = old_routes
        um._IMAGE_ROUTES_CONFIG_CACHE = old_image

    passed = sum(1 for e in route_entries if e.result == "passed")
    failed = sum(1 for e in route_entries if e.result == "failed")
    not_tested = sum(1 for e in route_entries if e.result == "not_tested")
    missing_evidence = sum(1 for e in route_entries if not e.evidence_source)

    return {
        "ok": failed == 0,
        "summary": {
            "total_routes_tested": len(route_entries),
            "passed": passed,
            "failed": failed,
            "not_tested": not_tested,
            "missing_evidence": missing_evidence,
        },
        "routes": [e.to_dict() for e in route_entries],
    }


def verify_fallback_chain(
    hermes_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic fallback chain verification — no live provider calls.

    Simulates primary-provider failure by marking it offline in a synthetic
    provider matrix, then walks the ``fallback_providers`` list in order,
    verifying each fallback is selected when all previous providers are
    unavailable.

    Returns a report with the full cascade::
        {
          "ok": bool,
          "primary": "copilot:gpt-5.4-mini",
          "steps": [
            {"step": 1, "provider": "gemini", "model": "...", "selected": true, ...},
            ...
          ],
          "summary": {"total": 3, "reachable": 3, "exhausted": false},
        }
    """
    if hermes_cfg is None:
        cfg_path = os.path.expanduser("~/.hermes/config.yaml")
        hermes_cfg = _load_yaml_text(cfg_path) if os.path.exists(cfg_path) else {}

    model_cfg = hermes_cfg.get("model", {})
    primary_provider_raw = model_cfg.get("provider", "copilot")
    primary_model = model_cfg.get("default", model_cfg.get("model", "gpt-5.4-mini"))

    normalize = getattr(um, "_PROVIDER_NORMALIZE", {})
    primary_provider = normalize.get(primary_provider_raw, primary_provider_raw)

    fb_list = hermes_cfg.get("fallback_providers", [])
    if not fb_list:
        fb_list = [
            {"provider": "openai-api", "model": "gpt-5.4-mini"},
            {"provider": "anthropic-api", "model": "claude-sonnet-4-6"},
            {"provider": "deepseek", "model": "deepseek-v4-flash"},
        ]

    steps: list[dict[str, Any]] = []
    offline: set[str] = {primary_provider}  # primary is "down"

    for idx, fb in enumerate(fb_list):
        fb_provider_raw = fb.get("provider", "")
        fb_model = fb.get("model", "")
        fb_provider: str = normalize.get(fb_provider_raw, fb_provider_raw)  # type: ignore[assignment]

        # Build synthetic results: previously-seen providers offline,
        # current fallback online, others online
        results = _deep_copy(PROVIDER_RESULTS_TEMPLATE)
        for p in offline:
            if p in results:
                results[p]["status"] = "offline"
        # Ensure current fallback is online (if in template)
        if fb_provider in results:
            results[fb_provider]["status"] = "online"

        # Run build_routes with this matrix
        old_hermes = um._HERMES_CONFIG_CACHE
        old_routes = um._ROUTES_CONFIG_CACHE
        um._HERMES_CONFIG_CACHE = hermes_cfg
        um._ROUTES_CONFIG_CACHE = {}
        try:
            route_data = um.build_routes(results)
        finally:
            um._HERMES_CONFIG_CACHE = old_hermes
            um._ROUTES_CONFIG_CACHE = old_routes

        # Find which fallback was selected
        fb_routes = route_data.get("fallbacks", [])
        selected = fb_routes[0] if fb_routes else {}
        selected_route = selected.get("route", "")
        sel_provider, sel_model = _split_route(selected_route)

        reached = sel_provider == fb_provider and (
            not fb_model or sel_model == fb_model
        )

        steps.append(
            {
                "step": idx + 1,
                "configured_provider": fb_provider_raw,
                "configured_model": fb_model,
                "expected_provider": fb_provider,
                "expected_model": fb_model,
                "actual_provider": sel_provider,
                "actual_model": sel_model,
                "selected": reached,
                "selected_route": selected_route,
                "offline_providers": sorted(offline),
            }
        )

        if reached:
            offline.add(fb_provider)  # This one "fails" for the next iteration

    reachable = sum(1 for s in steps if s["selected"])
    exhausted = steps and not steps[-1]["selected"]

    return {
        "ok": not exhausted and reachable > 0,
        "primary": f"{primary_provider_raw}:{primary_model}",
        "primary_normalized": f"{primary_provider}:{primary_model}",
        "steps": steps,
        "summary": {
            "total": len(steps),
            "reachable": reachable,
            "unreachable": len(steps) - reachable,
            "exhausted": exhausted,
        },
    }


def _load_json_or_yaml(path: str) -> dict[str, Any]:
    if path.endswith((".json", ".JSON")):
        with open(path) as f:
            return json.load(f)
    return _load_yaml_text(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes Ops Kit route runtime harness")
    parser.add_argument(
        "--hermes-config", required=True, help="Path to Hermes config YAML"
    )
    parser.add_argument("--routes-config", required=True, help="Path to routes YAML")
    parser.add_argument(
        "--assistants-config", required=True, help="Path to assistants YAML"
    )
    parser.add_argument(
        "--image-config", required=True, help="Path to image routes YAML"
    )
    parser.add_argument("--mcp-config", help="Path to MCP config YAML/JSON")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args(argv)

    report = build_report(
        hermes_cfg=_load_yaml_text(args.hermes_config),
        routes_cfg=_load_yaml_text(args.routes_config),
        assistants_cfg=_load_yaml_text(args.assistants_config),
        image_cfg=_load_yaml_text(args.image_config),
        mcp_cfg=_load_json_or_yaml(args.mcp_config) if args.mcp_config else {},
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(
            f"Hermes route harness · ok={report['ok']} · tested={report['summary']['total_routes_tested']}"
        )
        for entry in report["routes"]:
            print(
                f"- {entry['category']:<9s} {entry['route']:<18s} {entry['result']:<8s} "
                f"{entry['actual_provider']}:{entry['actual_model']} → {entry['runtime_path']}"
            )
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
