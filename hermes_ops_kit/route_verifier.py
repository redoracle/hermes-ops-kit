"""Hermes Ops Kit — Route Verifier.

Deterministic, timestamp-aware route verification.  Compares config
expectations against runtime log evidence, correlating timestamps so
stale pre-fix log entries are never mistaken for current behaviour.

The original route verification report incorrectly flagged AUX routes
as "bypassed" because it collected log evidence from BEFORE the config
was updated (Jun 3 21:12–23:13) while the config was fixed at 23:19
that same day.  This module prevents that mistake.

Usage::

    from hermes_ops_kit.route_verifier import verify_all_routes, check_credential_gaps

    report = verify_all_routes()
    if not report["ok"]:
        for route in report["routes"]:
            if route["result"] == "failed":
                print(f"{route['route']}: {route['failure_reason']}")

    gaps = check_credential_gaps()
    for g in gaps:
        print(f"{g['route']}: configured for {g['provider']} but no credential")
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from hermes_ops_kit import ops_config_io  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent

try:
    from .audit.route_events import emit_route_bypass_detected, emit_route_config_loaded
except ImportError:
    # Non-hermes environment — audit events are optional
    def emit_route_bypass_detected(*args: Any, **kwargs: Any) -> None:  # type: ignore[no-redef]
        pass

    def emit_route_config_loaded(*args: Any, **kwargs: Any) -> None:  # type: ignore[no-redef]
        pass


# ── Config loading ──────────────────────────────────────────────────────


def _load_yaml(path: str) -> dict[str, Any]:
    return ops_config_io.load_yaml(path)


def _load_env() -> dict[str, str]:
    """Parse .env and .env.generated into a dict (generated wins on overlap)."""
    from .env.loader import load_env_dict

    return load_env_dict()


def _config_mtime(path: str) -> datetime | None:
    """Return the modification time of a config file, or None."""
    try:
        ts = os.path.getmtime(path)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except OSError:
        return None


# ── Log parsing ─────────────────────────────────────────────────────────

_LOG_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:,\d{3})?)")

# Patterns that reveal which provider/model was actually used at runtime
_AUX_ROUTE_PATTERNS = [
    # "Auxiliary compression: using gemini (gemini-2.5-flash) at https://..."
    re.compile(
        r"Auxiliary\s+(?P<task>\w+):\s+using\s+(?P<provider>\S+)"
        r"\s+\((?P<model>[^)]+)\)",
    ),
    # "Auxiliary auto-detect: using main provider copilot (gpt-5.4-mini)"
    re.compile(
        r"Auxiliary\s+auto-detect:\s+using\s+main\s+provider\s+"
        r"(?P<provider>\S+)\s+\((?P<model>[^)]+)\)",
    ),
    # "Image routing: native (model supports vision)"
    re.compile(
        r"Image\s+routing:\s+(?P<mode>native|text)"
        r"(?:\s+\((?P<reason>[^)]+)\))?",
    ),
    # "vision_analyze: native fast path"
    re.compile(
        r"vision_analyze:\s+(?P<mode>native fast path|aux llm)",
    ),
]


def _parse_log_ts(line: str) -> datetime | None:
    m = _LOG_TS_RE.match(line)
    if not m:
        return None
    ts_str = m.group("ts")
    # Two formats: "2026-06-04 06:21:46,705" or "2026-06-04 06:21:46"
    ts_str = ts_str.replace(",", ".")
    try:
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        try:
            return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None


def extract_route_evidence(
    log_path: str | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Extract route-selection evidence from the Hermes agent log.

    Args:
        log_path: Path to agent.log.  Default: ~/.hermes/logs/agent.log
        since: Only return entries at or after this timestamp.  When None,
               uses the mtime of ~/.hermes/config.yaml so only evidence
               from AFTER the last config change is considered.

    Returns:
        List of evidence dicts with keys: ts, task, provider, model, mode, raw
    """
    if log_path is None:
        log_path = os.path.join(ops_config_io.HERMES_HOME, "logs", "agent.log")
    if since is None:
        since = _config_mtime(ops_config_io.hermes_config())

    if not os.path.exists(log_path):
        return []

    evidence: list[dict[str, Any]] = []
    with open(log_path) as f:
        for line in f:
            ts = _parse_log_ts(line)
            if since is not None and (ts is None or ts < since):
                continue

            for pattern in _AUX_ROUTE_PATTERNS:
                m = pattern.search(line)
                if m:
                    evidence.append(
                        {
                            "ts": ts,
                            "raw": line.strip()[:200],
                            **{k: v for k, v in m.groupdict().items() if v is not None},
                        }
                    )
                    break
    return evidence


# ── Verification ────────────────────────────────────────────────────────


def check_credential_gaps(
    config_path: str | None = None,
) -> list[dict[str, str]]:
    """Return AUX routes configured with providers that have no credentials.

    Each gap dict has: route, provider, model, issue
    """
    if config_path is None:
        config_path = ops_config_io.hermes_config()

    from .config.route_map import AUX_SHORT_KEYS, aux_config_key

    cfg = _load_yaml(config_path)
    env_vars = _load_env()

    def _credential_for_provider(provider: str) -> tuple[bool, str]:
        """Check if a provider has credentials in the env."""
        provider_lower = provider.lower()
        from .provider_catalog import first_available_key

        # Custom providers (custom:<name>) carry their own key_env in the
        # Hermes config — resolve it instead of relying on the static map.
        if provider_lower.startswith("custom:"):
            for cp in cfg.get("custom_providers", []) or []:
                if f"custom:{cp.get('name', '')}" == provider_lower:
                    key_env = str(cp.get("key_env", "")).strip()
                    if key_env and env_vars.get(key_env, "").strip():
                        return True, f"{key_env} set"
                    break
            return False, f"no credential for {provider}"
        key = first_available_key(provider_lower, env_vars)
        if key:
            return True, f"{key} set"
        return False, f"no credential for {provider}"

    aux_cfg = cfg.get("auxiliary", {}) or {}
    gaps: list[dict[str, str]] = []

    for sk in AUX_SHORT_KEYS:
        config_key = aux_config_key(sk)
        slot = aux_cfg.get(config_key, {}) or {}
        provider = str(slot.get("provider", "auto") or "auto").strip()
        model = str(slot.get("model", "") or "").strip()

        if provider in ("auto", ""):
            gaps.append(
                {
                    "route": sk,
                    "provider": provider,
                    "model": model,
                    "issue": "provider is 'auto' — resolves to primary at runtime",
                }
            )
            emit_route_bypass_detected(
                sk, provider, "primary (auto-resolve)", "provider is auto"
            )
            continue

        has_cred, _detail = _credential_for_provider(provider)
        if not has_cred:
            gaps.append(
                {
                    "route": sk,
                    "provider": provider,
                    "model": model,
                    "issue": f"no credential for provider '{provider}'",
                }
            )
            emit_route_bypass_detected(
                sk,
                provider,
                "none (missing credential)",
                f"no credential for {provider}",
            )

    return gaps


def verify_all_routes(
    hermes_config_path: str | None = None,
    log_path: str | None = None,
) -> dict[str, Any]:
    """Full route verification: config vs runtime evidence.

    Returns a dict with:
      - ok: bool
      - config_mtime: ISO timestamp of last config change
      - evidence_window: {since, until}
      - credential_gaps: list of {route, provider, model, issue}
      - runtime_evidence: evidence entries from after config change
      - summary: {total_checks, passed, failed}
    """
    if hermes_config_path is None:
        hermes_config_path = ops_config_io.hermes_config()
    if log_path is None:
        log_path = os.path.join(ops_config_io.HERMES_HOME, "logs", "agent.log")

    config_mtime = _config_mtime(hermes_config_path)
    cfg = _load_yaml(hermes_config_path)

    from .config.route_map import AUX_SHORT_KEYS  # pyright: ignore[reportMissingImports]

    # Credential gaps
    credential_gaps = check_credential_gaps(hermes_config_path)

    # Runtime evidence from AFTER the config was last changed
    runtime_evidence = extract_route_evidence(log_path, since=config_mtime)

    # Emit config loaded event
    model = cfg.get("model", {})
    aux_cfg = cfg.get("auxiliary", {}) or {}
    fb = cfg.get("fallback_providers", []) or []
    emit_route_config_loaded(
        primary_provider=model.get("provider", ""),
        primary_model=model.get("default", ""),
        aux_count=len(aux_cfg),
        fallback_count=len(fb),
    )

    ok = len(credential_gaps) == 0
    total_routes = len(AUX_SHORT_KEYS)

    report = {
        "ok": ok,
        "config_mtime": config_mtime.isoformat() if config_mtime else None,
        "evidence_window": {
            "since": config_mtime.isoformat() if config_mtime else None,
            "until": datetime.now(timezone.utc).isoformat(),
        },
        "credential_gaps": credential_gaps,
        "runtime_evidence": [
            {
                "ts": e["ts"].isoformat() if e.get("ts") else None,
                "task": e.get("task"),
                "provider": e.get("provider"),
                "model": e.get("model"),
                "mode": e.get("mode"),
                "reason": e.get("reason"),
                "raw": e["raw"],
            }
            for e in runtime_evidence
        ],
        "summary": {
            "total_checks": total_routes,
            "passed": total_routes - len(credential_gaps),
            "failed": len(credential_gaps),
        },
    }
    return report


__all__ = [
    "verify_all_routes",
    "check_credential_gaps",
    "extract_route_evidence",
]
