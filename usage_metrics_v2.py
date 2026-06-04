#!/usr/bin/env python3
"""
Hermes Ops Kit — Unified Usage Metrics v2

Hierarchical output: ROUTE → PROVIDERS → LIMITS → WARNINGS.
Answers: "What should Hermes route to right now, and is anything risky?"

Usage:
    hermes-usage                      # Rich boxed view (default)
    hermes-usage --compact            # Minimal routing view
    hermes-usage --models             # Model inventory
    hermes-usage --limits             # Rate limits detail
    hermes-usage --costs              # Usage/cost telemetry (needs admin keys)
    hermes-usage --verbose            # All sections
    hermes-usage --json               # Machine-readable JSON
    hermes-usage -p github            # Single provider (openai|anthropic|github|gemini|deepseek)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List

# ─── Provider registry (single source of truth) ──────────────────
# Every "which providers exist" decision derives from here so a new provider
# can't silently vanish from one view while showing in another.

PROVIDERS = ["openai", "anthropic", "github", "gemini", "deepseek"]
# Display order: free/included first, then paid.
DISPLAY_ORDER = ["github", "gemini", "openai", "anthropic", "deepseek"]
PROVIDER_NAMES = {
    "openai": "OPENAI",
    "anthropic": "ANTHROPIC",
    "github": "GITHUB",
    "gemini": "GEMINI",
    "deepseek": "DEEPSEEK",
}

# ─── Assistants (remote agent runtimes, not model providers) ─────


def _registry_assistant_ids() -> list:
    """Assistant IDs from the registry (honors HERMES_ASSISTANTS_CONFIG).

    Derived dynamically so a configured assistant is never reported as
    "not in registry" merely because an id was hardcoded here.
    Aliases are filtered out — only primary assistant IDs are returned.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from assistants.registry import (  # pyright: ignore[reportMissingImports]
            load_registry,
            list_assistants,
        )

        all_ids = list_assistants()
        if not all_ids:
            return []

        # Deduplicate by object identity — aliases point to the same
        # AssistantConfig instance as their primary ID.
        registry = load_registry()
        seen: set[int] = set()
        primary: list[str] = []
        for aid in all_ids:
            cfg = registry.get(aid)
            if cfg is not None:
                oid = id(cfg)
                if oid not in seen:
                    seen.add(oid)
                    primary.append(aid)
        return primary
    except Exception:
        return []


ASSISTANTS = _registry_assistant_ids()
ASSISTANT_NAMES: dict = {}

# ─── HTTP helper ─────────────────────────────────────────────────

HTTP_TIMEOUT = 15  # seconds; the org cost endpoint can take ~3-4s under load


def _urlopen(req, timeout: int = HTTP_TIMEOUT, retries: int = 1):
    """urlopen with one retry on transient timeouts/connection errors.

    HTTPError (4xx/5xx) is re-raised immediately so callers can handle it;
    only read/connect timeouts and URLErrors are retried.
    """
    attempt = 0
    while True:
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (TimeoutError, urllib.error.URLError):
            if attempt >= retries:
                raise
            attempt += 1
            time.sleep(0.5)


def _timeout_reason(e: Exception) -> str:
    """Classify a network exception as 'timeout' or 'error' for display."""
    s = str(e).lower()
    return (
        "timeout"
        if isinstance(e, TimeoutError) or "timed out" in s or "timeout" in s
        else "error"
    )


# ─── Env loading ─────────────────────────────────────────────────


def _parse_env_file(path: str) -> None:
    """Parse a .env-style file into os.environ (no-op if missing)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "#" in line:
                for i, c in enumerate(line):
                    if c in ('"', "'"):
                        break
                    elif c == "#":
                        line = line[:i].strip()
                        break
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key:
                os.environ[key] = value.strip().strip('"').strip("'")


def load_env_file(path: str | None = None) -> None:
    """Load .env and .env.generated into os.environ.

    .env is loaded first, then .env.generated on top — generated
    keys take precedence, but vars set only in .env are preserved.
    When *path* is given, loads only that single file.
    """
    if path is not None:
        _parse_env_file(path)
        return
    _parse_env_file(os.path.expanduser("~/.hermes/.env"))
    _parse_env_file(os.path.expanduser("~/.hermes/.env.generated"))


# ─── Shared redaction ────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from security.redaction import redact  # noqa: E402  # pyright: ignore[reportMissingImports]

# ─── Provider Checks ────────────────────────────────────────────


def _cat_openai(data: dict) -> dict:
    cats = {
        "chat": [],
        "embedding": [],
        "tts": [],
        "stt": [],
        "moderation": [],
        "image": [],
        "other": [],
    }
    for m in data.get("data", []):
        mid = m.get("id", "")
        if any(p in mid for p in ("gpt-", "o1", "o3", "o4", "chatgpt-")):
            cats["chat"].append(mid)
        elif "embedding" in mid or "text-embedding" in mid:
            cats["embedding"].append(mid)
        elif "tts" in mid:
            cats["tts"].append(mid)
        elif "whisper" in mid:
            cats["stt"].append(mid)
        elif "moderation" in mid or "omni-moderation" in mid:
            cats["moderation"].append(mid)
        elif "dall-e" in mid:
            cats["image"].append(mid)
        else:
            cats["other"].append(mid)
    return {k: sorted(v) for k, v in cats.items() if v}


def _fetch_openai_models(key: str):
    """GET the model list; returns (data, headers). Raises on HTTP/network error."""
    req = urllib.request.Request(
        "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}
    )
    resp = _urlopen(req)
    return json.loads(resp.read().decode()), resp.headers


def check_openai(api_key: str | None = None) -> dict:
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return {
            "provider": "openai",
            "status": "offline",
            "error": "OPENAI_API_KEY not set",
        }
    t0 = time.time()
    admin_key = os.environ.get("OPENAI_ADMIN_KEY", "")
    # Fire the independent reads concurrently: models (status gate) + usage + costs.
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_models = ex.submit(_fetch_openai_models, key)
        f_usage = ex.submit(_fetch_openai_usage, key, admin_key)
        f_costs = ex.submit(_fetch_openai_costs, admin_key) if admin_key else None
        try:
            data, headers = f_models.result()
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300] if e.fp else ""
            return {
                "provider": "openai",
                "status": "error",
                "http_status": e.code,
                "api_latency_ms": int((time.time() - t0) * 1000),
                "error": redact(body),
            }
        except Exception as e:
            return {
                "provider": "openai",
                "status": "error",
                "api_latency_ms": int((time.time() - t0) * 1000),
                "error": str(e),
            }
        cats = _cat_openai(data)
        result = {
            "provider": "openai",
            "status": "online",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "total_models": len(data.get("data", [])),
            "chat_models": len(cats.get("chat", [])),
            "model_categories": {k: len(v) for k, v in cats.items()},
            "models": cats.get("chat", []),
            "embedding_models": cats.get("embedding", []),
            "tts_models": cats.get("tts", []),
            "stt_models": cats.get("stt", []),
            "moderation_models": cats.get("moderation", []),
            "has_quota_api": False,
            "quota_url": "platform.openai.com/usage",
            "organization": headers.get("openai-organization", "unknown"),
            "request_id": headers.get("x-request-id", "unknown"),
        }
        # Rate-limit probe (POST, gated): only after status is confirmed — billable +
        # non-idempotent, so we don't fire it speculatively on a bad key.
        if not os.environ.get("SKIP_RATELIMIT_PROBE"):
            result["rate_limits"] = _fetch_openai_rate_limits(key)
        usage = f_usage.result()
        result["usage"] = usage
        result["has_usage_api"] = bool(usage.get("tokens_today", 0))
        if f_costs is not None:
            costs = f_costs.result()
            result["costs"] = costs
            result["has_cost_api"] = costs.get("ok", False)
            if not costs.get("ok"):
                result["costs_note"] = costs.get("error", "cost API call failed")
        else:
            result["has_cost_api"] = False
            result["costs_note"] = "admin key missing"
        return result


def check_anthropic(api_key: str | None = None) -> dict:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {
            "provider": "anthropic",
            "status": "offline",
            "error": "ANTHROPIC_API_KEY not set",
        }
    t0 = time.time()
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        resp = _urlopen(req)
        data = json.loads(resp.read().decode())
        models, cats = [], {"opus": [], "sonnet": [], "haiku": []}
        for m in data.get("data", []):
            mid = m.get("id", "")
            info = {
                "id": mid,
                "display_name": m.get("display_name", mid),
                "created": m.get("created_at", "unknown"),
            }
            if "opus" in mid:
                info["tier"] = "power"
                cats["opus"].append(info)
            elif "sonnet" in mid:
                info["tier"] = "balanced"
                cats["sonnet"].append(info)
            elif "haiku" in mid:
                info["tier"] = "fast"
                cats["haiku"].append(info)
            models.append(info)
        latest = {
            k: (
                max(v, key=lambda x: x["created"] if x["created"] != "unknown" else "")[
                    "id"
                ]
                if v
                else None
            )
            for k, v in cats.items()
        }
        result = {
            "provider": "anthropic",
            "status": "online",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "models": models,
            "model_count": len(models),
            "model_categories": {k: len(v) for k, v in cats.items()},
            "latest_per_tier": latest,
            "has_quota_api": False,
            "quota_url": "console.anthropic.com/settings/usage",
            "request_id": resp.headers.get("request-id", "unknown"),
        }
        admin_key = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
        if admin_key:
            costs = _fetch_anthropic_costs(admin_key)
            result["costs"] = costs
            result["has_cost_api"] = costs.get("ok", False)
        else:
            result["has_cost_api"] = False
            result["costs_note"] = (
                "requires sk-ant-admin key (individual accounts: unavailable)"
            )
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300] if e.fp else ""
        return {
            "provider": "anthropic",
            "status": "error",
            "http_status": e.code,
            "api_latency_ms": int((time.time() - t0) * 1000),
            "error": redact(body),
        }
    except Exception as e:
        return {
            "provider": "anthropic",
            "status": "error",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "error": str(e),
        }


def check_github() -> dict:
    if not shutil.which("gh"):
        return {
            "provider": "github",
            "status": "offline",
            "error": "gh CLI not installed",
        }
    t0 = time.time()
    env = os.environ.copy()
    try:
        rl = subprocess.run(
            ["gh", "api", "/rate_limit"],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        rate_data = json.loads(rl.stdout) if rl.returncode == 0 else {}
        resources = {}
        core_reset = None
        for name, data in sorted((rate_data.get("resources", {}) or {}).items()):
            if isinstance(data, dict) and data.get("remaining") is not None:
                lim = data.get("limit", 1)
                rem = data["remaining"]
                resources[name] = {
                    "remaining": rem,
                    "limit": lim,
                    "used_pct": (
                        round((1 - rem / max(lim, 1)) * 100, 2) if lim > 0 else 0.0
                    ),
                }
                if name == "core" and data.get("reset"):
                    core_reset = data["reset"]
        cp = subprocess.run(
            ["gh", "copilot", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        copilot_ok = cp.returncode == 0
        # Normalize: extract just version number from noisy CLI output
        copilot_ver = (
            cp.stdout.strip().split("\n")[0].replace("GitHub Copilot CLI ", "")
            if copilot_ok
            else None
        )
        if copilot_ver and "." in copilot_ver:
            copilot_ver = copilot_ver.strip().rstrip(".")
        auth = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        authed = auth.returncode == 0 or rl.returncode == 0
        # Check if GITHUB_TOKEN env var is set (enables 5000 req/hr vs 60 unauthenticated)
        has_token = bool(os.environ.get("GITHUB_TOKEN"))
        core = resources.get("core", {})
        # Copilot model catalog (from GH_COPILOT_STUDIO, June 2026 — not queryable via API)
        copilot_models = {
            "anthropic": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"],
            "openai": [
                "gpt-5.4-mini",
                "gpt-5.4-nano",
                "gpt-5.2",
                "gpt-5.2-codex",
                "gpt-5.3-codex",
                "gpt-5.4",
                "gpt-5.5",
            ],
            "google": ["gemini-2.5-pro", "gemini-3.5-flash", "gemini-3.1-pro"],
            "github": ["raptor-mini"],
        }
        return {
            "provider": "github",
            "status": "online" if authed else "offline",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "rate_limits": resources,
            "rate_limits_reset_epoch": core_reset,
            "rate_limits_reset_iso": (
                datetime.fromtimestamp(core_reset).isoformat() if core_reset else None
            ),
            "core_used_pct": (
                round(
                    (1 - core.get("remaining", 0) / max(core.get("limit", 1), 1)) * 100,
                    2,
                )
                if core.get("limit")
                else 0
            ),
            "core_remaining": core.get("remaining"),
            "core_limit": core.get("limit"),
            "copilot_available": copilot_ok,
            "copilot_version": copilot_ver,
            "copilot_models": copilot_models,
            "copilot_model_count": sum(len(v) for v in copilot_models.values()),
            "has_github_token": has_token,
            "has_quota_api": True,
        }
    except Exception as e:
        return {
            "provider": "github",
            "status": "error",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "error": str(e),
        }


def check_gemini(api_key: str | None = None) -> dict:
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {
            "provider": "gemini",
            "status": "offline",
            "error": "GEMINI_API_KEY not set",
        }
    t0 = time.time()
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        resp = _urlopen(urllib.request.Request(url))
        data = json.loads(resp.read().decode())
        gemini_models = []
        for m in data.get("models", []):
            name = m.get("name", "").replace("models/", "")
            if "gemini" in name.lower():
                gemini_models.append(
                    {
                        "id": name,
                        "display_name": m.get("displayName", name),
                        "description": (m.get("description", "") or "")[:120],
                        "input_tokens": m.get("inputTokenLimit"),
                        "output_tokens": m.get("outputTokenLimit"),
                        "methods": m.get("supportedGenerationMethods", []),
                    }
                )
        max_ctx = (
            max((m["input_tokens"] or 0) for m in gemini_models) if gemini_models else 0
        )
        return {
            "provider": "gemini",
            "status": "online",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "models": gemini_models,
            "model_count": len(gemini_models),
            "all_models_count": len(data.get("models", [])),
            "free_tier_rpd_flash": 1500,
            "free_tier_rpd_pro": 100,
            "max_ctx": max_ctx,
            "has_quota_api": False,
            "quota_note": "Free: 1500 RPD (Flash) | 100 RPD (Pro)",
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300] if e.fp else ""
        return {
            "provider": "gemini",
            "status": "error",
            "http_status": e.code,
            "api_latency_ms": int((time.time() - t0) * 1000),
            "error": redact(body),
        }
    except Exception as e:
        return {
            "provider": "gemini",
            "status": "error",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "error": str(e),
        }


def _fetch_deepseek_balance(key: str) -> dict:
    """DeepSeek /user/balance — the cost/quota analog (no admin key needed).

    Returns {"ok": True, "is_available", "currency", "total", "granted",
    "topped_up"} on success (prefers a USD bucket, else the first), or
    {"ok": False, "error": ...} on failure. Never raises.
    """
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"},
        )
        resp = _urlopen(req)
        data = json.loads(resp.read().decode())
        infos = data.get("balance_infos", []) or []
        pick = next(
            (b for b in infos if b.get("currency") == "USD"), infos[0] if infos else {}
        )
        return {
            "ok": True,
            "is_available": data.get("is_available"),
            "currency": pick.get("currency"),
            "total": pick.get("total_balance"),
            "granted": pick.get("granted_balance"),
            "topped_up": pick.get("topped_up_balance"),
        }
    except Exception as e:
        return {"ok": False, "error": redact(str(e)[:200])}


def check_deepseek(api_key: str | None = None) -> dict:
    key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return {
            "provider": "deepseek",
            "status": "offline",
            "error": "DEEPSEEK_API_KEY not set",
        }
    t0 = time.time()
    # Fire the two independent reads concurrently: models (status gate) + balance.
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_balance = ex.submit(_fetch_deepseek_balance, key)
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            resp = _urlopen(req)
            data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300] if e.fp else ""
            return {
                "provider": "deepseek",
                "status": "error",
                "http_status": e.code,
                "api_latency_ms": int((time.time() - t0) * 1000),
                "error": redact(body),
            }
        except Exception as e:
            return {
                "provider": "deepseek",
                "status": "error",
                "api_latency_ms": int((time.time() - t0) * 1000),
                "error": str(e),
            }
        models = [
            {"id": m.get("id", ""), "owned_by": m.get("owned_by", "deepseek")}
            for m in data.get("data", [])
            if m.get("id")
        ]
        balance = f_balance.result()
        result = {
            "provider": "deepseek",
            "status": "online",
            "api_latency_ms": int((time.time() - t0) * 1000),
            "models": models,
            "model_count": len(models),
            "balance": balance,
            "has_cost_api": balance.get("ok", False),  # balance is the cost analog
            "has_quota_api": False,  # no rate-limit API; DeepSeek doesn't enforce hard limits
            "quota_url": "platform.deepseek.com/usage",
        }
        if not balance.get("ok"):
            result["costs_note"] = balance.get("error", "balance API call failed")
        return result


# ─── Bridge health ──────────────────────────────────────────────


def check_bridge_health() -> dict:
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "providers")
    adapters = {}
    for p, f in [
        ("openai", "openai_adapter.py"),
        ("anthropic", "claude_adapter.py"),
        ("github", "github_adapter.py"),
        ("gemini", "gemini_adapter.py"),
        ("deepseek", "deepseek_adapter.py"),
    ]:
        fp = os.path.join(d, f)
        adapters[p] = os.path.exists(fp) and os.access(fp, os.X_OK)

    def _cli_version(item):
        name, cmd = item
        try:
            r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return name, "error"
            first_line = r.stdout.strip().split("\n")[0]
            # Strip the tool's own name prefix to avoid "gh gh"
            # e.g. "gh version 2.71.2 (2026-05-01)" → "2.71.2"
            # e.g. "codex cli 0.135.0" → "0.135.0"
            # e.g. "GitHub Copilot CLI 1.0.59." → "1.0.59"
            for prefix in (
                f"{name} version ",
                f"{name}-cli ",
                f"{name} cli ",
                f"{name} ",
                f"{name}-",
                "GitHub Copilot CLI ",
                "GitHub Copilot ",
            ):
                if first_line.lower().startswith(prefix.lower()):
                    first_line = first_line[len(prefix) :]
                    break
            return name, first_line
        except Exception:
            return name, "not found"

    cli_cmds = [
        ("codex", "codex --version"),
        ("gh", "gh --version"),
        ("gh copilot", "gh copilot --version"),
        ("gemini", "gemini --version"),
        ("claude", "claude --version"),
    ]
    # Probe the CLIs in parallel — Node-based CLIs (claude/gemini) have slow cold starts
    with ThreadPoolExecutor(max_workers=len(cli_cmds)) as ex:
        clis = dict(ex.map(_cli_version, cli_cmds))
    return {
        "bridge_version": "0.2.0",
        "deployed_on": f"{os.uname().nodename} ({os.uname().sysname})",
        "adapters_installed": adapters,
        "clis": clis,
    }


def _resolve_gateway_port() -> int:
    """Read the gateway port from config, defaulting to 8642."""
    try:
        cfg = _get_hermes_config()
        gw_cfg = cfg.get("gateway", {})
        port = gw_cfg.get("port")
        if port is not None:
            return int(port)
    except Exception:
        pass
    return 8642


def _gateway_candidates() -> list[str]:
    """Build a list of IPs/hosts where the Hermes gateway might be listening.

    Order: localhost, env-var overrides, config hints, local network
    interfaces, Tailscale IPs, fallback hostname.
    """
    import socket as _socket
    import subprocess as _sp

    candidates: list[str] = ["127.0.0.1", "localhost"]

    # Env-var overrides
    for env_var in ("HERMES_GATEWAY_HOST", "HERMES_HOST"):
        host = (os.environ.get(env_var) or "").strip()
        if host and host not in candidates:
            candidates.append(host)

    # Config-file hints
    try:
        cfg = _get_hermes_config()
        gw_cfg = cfg.get("gateway", {})
        for key in ("host", "bind"):
            host = str(gw_cfg.get(key, "")).strip()
            if host and host not in candidates:
                candidates.append(host)
    except Exception:
        pass

    # Tailscale IPs (fast CLI lookup)
    try:
        ts = _sp.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if ts.returncode == 0:
            ts_ip = ts.stdout.strip()
            if ts_ip and ts_ip not in candidates:
                candidates.append(ts_ip)
    except Exception:
        pass

    # Local non-loopback IPs from network interfaces (Linux / macOS)
    for if_name in ("en0", "eth0", "wlan0"):
        try:
            import fcntl
            import struct

            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            ip = _socket.inet_ntoa(
                fcntl.ioctl(
                    s.fileno(),
                    0xC0206921,  # SIOCGIFADDR
                    struct.pack("256s", if_name[:15].encode()),
                )[20:24]
            )
            if ip and ip not in candidates:
                candidates.append(ip)
            s.close()
        except Exception:
            pass

    # Hostname as last resort
    try:
        hostname = _socket.gethostname()
        if hostname and hostname not in candidates:
            candidates.append(hostname)
            candidates.append(f"{hostname}.local")
    except Exception:
        pass

    return candidates


def check_hermes_status() -> dict:
    hd = os.path.expanduser("~/.hermes")
    gw = False
    port = _resolve_gateway_port()

    for ip in _gateway_candidates():
        try:
            resp = urllib.request.urlopen(f"http://{ip}:{port}/health", timeout=3)
            if resp.status == 200:
                gw = True
                break
        except Exception:
            pass

    return {
        "agent": "hermes-agent",
        "version": "0.15.1",
        "gateway_running": gw,
        "bridge_skill_loaded": any(
            os.path.isfile(os.path.join(root, "SKILL.md"))
            for root, _, _ in os.walk(os.path.join(hd, "skills"))
        ),
    }


# ─── Assistants ──────────────────────────────────────────────────


def check_assistant(aid: str) -> dict:
    """Healthcheck a single registered remote Hermes assistant by id."""
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from assistants.client import (
        AssistantClient,
    )  # pyright: ignore[reportMissingImports]
    from assistants.registry import (
        get_assistant,
    )  # pyright: ignore[reportMissingImports]

    config = get_assistant(aid)
    if not config:
        return {"assistant": aid, "status": "error", "error": "not in registry"}

    # In test mode never make live network calls — just confirm registration.
    if os.environ.get("HERMES_TEST_MODE"):
        return {
            "assistant": aid,
            "status": "skipped",
            "role": config.role,
            "reason": "test mode (no network healthcheck)",
        }

    # Load env vars (they may be set by load_env_file or .env.generated)
    for env_var, env_key in [
        ("ASSISTANT_API_BASE", config.base_url_env),
        ("ASSISTANT_API_KEY", config.api_key_env),
        ("ASSISTANT_MODEL", config.model_env),
    ]:
        val = os.environ.get(env_var)
        if val and not os.environ.get(env_key):
            os.environ[env_key] = val

    client = AssistantClient(config)
    return client.healthcheck()


def check_all_assistants() -> dict[str, dict]:
    """Run all registered assistant healthchecks concurrently."""
    results: dict[str, dict] = {}
    if not ASSISTANTS:
        return results
    with ThreadPoolExecutor(max_workers=max(len(ASSISTANTS), 1)) as ex:
        futures = {ex.submit(check_assistant, aid): aid for aid in ASSISTANTS}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = {
                    "assistant": name,
                    "status": "error",
                    "error": redact(str(e)),
                }
    return results


# ─── Routing ────────────────────────────────────────────────────


def _load_hermes_config() -> dict:
    """Load ~/.hermes/config.yaml for model/auxiliary/fallback data."""
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        with open(config_path) as f:
            return _yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_routes_config() -> dict:
    """Load routes.yaml display config (fallback to bundled)."""
    paths = [
        os.path.expanduser("~/.hermes/ops-kit/routes.yaml"),
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "routes.yaml"
        ),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                with open(p) as f:
                    return _yaml.safe_load(f) or {}
            except Exception:
                pass
    return {}


_HERMES_CONFIG_CACHE: dict | None = None
_ROUTES_CONFIG_CACHE: dict | None = None


def _get_hermes_config() -> dict:
    global _HERMES_CONFIG_CACHE
    if _HERMES_CONFIG_CACHE is None:
        _HERMES_CONFIG_CACHE = _load_hermes_config()
    return _HERMES_CONFIG_CACHE


def _get_routes_config() -> dict:
    global _ROUTES_CONFIG_CACHE
    if _ROUTES_CONFIG_CACHE is None:
        _ROUTES_CONFIG_CACHE = _load_routes_config()
    return _ROUTES_CONFIG_CACHE


_IMAGE_ROUTES_CONFIG_CACHE: dict | None = None


def _load_image_routes_config() -> dict:
    """Load image_routes.yaml config (fallback to bundled)."""
    paths = [
        os.path.expanduser("~/.hermes/ops-kit/image_routes.yaml"),
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "image_routes.yaml"
        ),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                with open(p) as f:
                    return _yaml.safe_load(f) or {}
            except Exception:
                pass
    return {}


def _get_image_routes_config() -> dict:
    global _IMAGE_ROUTES_CONFIG_CACHE
    if _IMAGE_ROUTES_CONFIG_CACHE is None:
        _IMAGE_ROUTES_CONFIG_CACHE = _load_image_routes_config()
    return _IMAGE_ROUTES_CONFIG_CACHE


def build_image_routes(results: dict) -> list:
    """Build image route display data from image_routes.yaml.

    Returns list of dicts with: role, route, reason, latency, cost, online.
    """
    img_cfg = _get_image_routes_config()
    routes = img_cfg.get("routes", {})
    default = img_cfg.get("default_route", "fast")
    # TODO: use img_cfg['policies'] for cost enforcement / route gating

    image_routes = []
    for route_name in ["local", "fast", "quality", "fallback"]:
        cfg = routes.get(route_name)
        if not cfg:
            continue
        provider = cfg.get("provider", "?")
        model = cfg.get("model", "?")
        label = cfg.get("label", "")
        cost = cfg.get("cost_class", "unknown")

        # Check provider availability from results
        normalized = _PROVIDER_NORMALIZE.get(provider, provider)
        pdata = results.get(normalized, {})
        online = pdata.get("status") == "online"
        latency = pdata.get("api_latency_ms")

        # Special: local-comfyui doesn't go through bridge providers
        if provider == "local-comfyui":
            try:
                from image_routes.adapters.local_comfyui import LocalComfyUIAdapter

                adapter = LocalComfyUIAdapter(
                    endpoint=cfg.get("endpoint", "http://127.0.0.1:8188")
                )
                online = adapter.is_available()
            except Exception:
                online = False
            latency = None

        default_marker = " ★" if route_name == default else ""
        image_routes.append(
            {
                "role": route_name,
                "route": f"{provider}:{model}",
                "reason": label + default_marker,
                "latency": latency,
                "cost": cost,
                "online": online,
            }
        )

    return image_routes


# Provider name normalization: hermes config → bridge internal name
_PROVIDER_NORMALIZE = {
    "copilot": "github",
    "github": "github",
    "gemini": "gemini",
    "openai": "openai",
    "openai-api": "openai",
    "anthropic": "anthropic",
    "anthropic-api": "anthropic",
    "deepseek": "deepseek",
}

from config.route_map import aux_display_triples  # noqa: E402

# Aux route display mapping — canonical source: config/route_map.py
_AUX_DISPLAY = aux_display_triples()


def build_routes(results: dict) -> dict:
    """Build config-driven route groups: primary, utility, aux, fallbacks.

    Reads ~/.hermes/config.yaml for model/auxiliary/fallback data.
    Falls back to hardcoded defaults if config is unavailable.
    Returns dict with keys: routes, aux_routes, fallbacks
    """
    hermes_cfg = _get_hermes_config()
    routes_cfg = _get_routes_config()

    routes = []
    aux_routes = []
    fallback_routes = []

    # Primary: from Hermes config or hardcoded
    model_cfg = hermes_cfg.get("model", {})
    primary_provider_raw = model_cfg.get("provider", "copilot")
    primary_model = model_cfg.get("default", "gpt-5.4-mini")
    primary_provider = _PROVIDER_NORMALIZE.get(
        primary_provider_raw, primary_provider_raw
    )
    primary_data = results.get(primary_provider, {})

    if primary_data.get("status") == "online":
        routes.append(
            {
                "role": "primary",
                "route": f"{primary_provider}/{primary_provider_raw}:{primary_model}",
                "reason": routes_cfg.get("routes", {})
                .get("primary", {})
                .get("label", "coding/default"),
                "latency": primary_data.get("api_latency_ms"),
                "cost": "included" if primary_provider == "github" else "paid",
            }
        )
    # Fallback: hardcoded primary if Hermes config didn't work
    elif results.get("github", {}).get("status") == "online" and results["github"].get(
        "copilot_available"
    ):
        routes.append(
            {
                "role": "primary",
                "route": "github/copilot:gpt-5.4-mini",
                "reason": "included · coding/cheap",
                "latency": results["github"].get("api_latency_ms"),
                "cost": "included",
            }
        )

    # Utility: derived from explicit AUX config (most common provider/model).
    # When no AUX route is explicitly configured, falls back to the primary
    # provider (matching Hermes' native _resolve_auto behaviour).
    aux_cfg = hermes_cfg.get("auxiliary", {})
    _aux_explicit: dict[str, int] = {}
    for _role, bare_key, _short_key in _AUX_DISPLAY:
        slot = aux_cfg.get(bare_key, {})
        p = str(slot.get("provider", "auto") or "auto").strip()
        m = str(slot.get("model", "") or "").strip()
        if p not in ("auto", "") and m:
            p = _PROVIDER_NORMALIZE.get(p, p)
            _aux_explicit[f"{p}:{m}"] = _aux_explicit.get(f"{p}:{m}", 0) + 1

    if _aux_explicit:
        _best = max(_aux_explicit, key=lambda k: _aux_explicit[k])  # type: ignore[arg-type]
        util_provider, util_model = _best.split(":", 1)
    else:
        util_provider = primary_provider
        util_model = primary_model

    utility_cfg = routes_cfg.get("routes", {}).get("utility", {})
    util_data = results.get(util_provider, {})
    if util_data.get("status") == "online":
        routes.append(
            {
                "role": "utility",
                "route": f"{util_provider}:{util_model}",
                "reason": utility_cfg.get("label", "free-tier · 1M ctx"),
                "latency": util_data.get("api_latency_ms"),
                "cost": utility_cfg.get("cost_class", "free"),
            }
        )

    # Auxiliary routes: from Hermes auxiliary config.
    # When provider is "auto" or model is empty, fall back to primary provider
    # (matching Hermes' native _resolve_auto Step-1 behaviour).
    for role, bare_key, _short_key in _AUX_DISPLAY:
        slot = aux_cfg.get(bare_key, {})
        aux_provider_raw = str(slot.get("provider", "auto") or "auto")
        aux_model = str(slot.get("model", "") or "")
        if aux_provider_raw in ("auto", "") or not aux_model:
            aux_provider_raw = str(primary_provider)
            aux_model = str(primary_model)
        aux_provider: str = _PROVIDER_NORMALIZE.get(aux_provider_raw, aux_provider_raw)  # type: ignore[arg-type]
        aux_data = results.get(aux_provider, {})
        label = (
            routes_cfg.get("routes", {})
            .get("aux", {})
            .get(_short_key, {})
            .get("label", _short_key)
        )
        aux_routes.append(
            {
                "role": role,
                "route": f"{aux_provider}:{aux_model}",
                "reason": label,
                "latency": aux_data.get("api_latency_ms"),
                "cost": "free" if aux_provider == "gemini" else "paid",
                "online": aux_data.get("status") == "online" and aux_model != "",
            }
        )

    # Fallbacks: from Hermes fallback_providers list
    fb_list = hermes_cfg.get("fallback_providers", [])
    if not fb_list:
        # Hardcoded defaults
        fb_list = [
            {"provider": "openai-api", "model": "gpt-5.4-mini"},
            {"provider": "anthropic-api", "model": "claude-sonnet-4-6"},
            {"provider": "deepseek", "model": "deepseek-v4-flash"},
        ]
    for fb in fb_list:
        fb_provider_raw = fb.get("provider", "")
        fb_model = fb.get("model", "")
        fb_provider = _PROVIDER_NORMALIZE.get(fb_provider_raw, fb_provider_raw)
        fb_data = results.get(fb_provider, {})
        if fb_data.get("status") == "online":
            fallback_routes.append(
                {
                    "role": "fallback",
                    "route": f"{fb_provider}:{fb_model}",
                    "reason": fb.get("label", "paid"),
                    "latency": fb_data.get("api_latency_ms"),
                    "cost": "paid",
                }
            )

    return {"routes": routes, "aux_routes": aux_routes, "fallbacks": fallback_routes}


# ─── Display helpers ─────────────────────────────────────────────


def _icon(s: str) -> str:
    return {"online": "●", "offline": "○", "error": "◐"}.get(s, "?")


def _ms(data: dict) -> str:
    ms = data.get("api_latency_ms")
    return f"{ms}ms" if ms is not None else "?"


def _ds_balance_str(data: dict) -> str:
    """Format DeepSeek balance as the quota/cost line (e.g. 'bal 5.00 USD')."""
    b = data.get("balance", {})
    if b.get("ok") and b.get("total") is not None:
        return f"bal {b['total']} {b.get('currency', '')}".strip()
    return "balance N/A"


def _quota_str(data: dict, provider: str) -> str:
    if provider == "github":
        return f"core {data.get('core_remaining', '?')}/{data.get('core_limit', '?')} ({data.get('core_used_pct', 0)}%)"
    elif provider == "gemini":
        return data.get("quota_note", "free tier")
    elif provider == "deepseek":
        return _ds_balance_str(data)
    else:
        return "usage → dashboard"


def _rec(data: dict, provider: str) -> str:
    return {
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-sonnet-4-6",
        "github": "gpt-5.4-mini",
        "gemini": "gemini-2.5-flash",
        "deepseek": "deepseek-v4-flash",
    }.get(provider, "?")


def _collect_warnings(results: dict) -> List[str]:
    warns = []
    gh = results.get("github", {})
    if gh.get("status") == "online":
        if not gh.get("has_github_token"):
            warns.append(
                "No GITHUB_TOKEN set (60 req/hr unauthenticated — set in ~/.hermes/.env for 5,000/hr)"
            )
        core = gh.get("core_used_pct", 0)
        if core > 80:
            warns.append(f"GitHub core rate limit {core:.0f}% used — near exhaustion")
    bridge = results.get("_bridge", {})
    for name, ver in (bridge.get("clis") or {}).items():
        if ver == "error":
            warns.append(f"{name} CLI version check failed")
    return warns


# ─── Output flag processing ──────────────────────────────────────

_UNICODE_TO_ASCII = {
    "╭": "+",
    "╮": "+",
    "╰": "+",
    "╯": "+",
    "─": "-",
    "│": "|",
    "●": "*",
    "○": "o",
    "◐": "?",
    "🟢": "[OK]",
    "🔴": "[OFF]",
    "✓": "[v]",
    "✗": "[x]",
    "⚠": "!!",
    "🧮": "[M]",
    "🔊": "[TTS]",
    "🎤": "[STT]",
    "🛡️": "[MOD]",
    "✅": "[v]",
    "❌": "[x]",
}


def _apply_output_flags(text: str, plain: bool, no_color: bool) -> str:
    """Post-process formatter output for --plain / --no-color flags."""
    import re

    if plain:
        for uni, ascii_repl in _UNICODE_TO_ASCII.items():
            text = text.replace(uni, ascii_repl)
    if no_color:
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text


# ─── Output formatters ──────────────────────────────────────────


def fmt_compact(results: dict) -> str:
    """Minimal routing-focused output."""
    lines = []
    online = sum(1 for p in PROVIDERS if results.get(p, {}).get("status") == "online")
    asst_online = sum(
        1
        for a in ASSISTANTS
        if results.get("_assistants", {}).get(a, {}).get("status") == "online"
    )
    lines.append(
        f"HERMES OPS KIT · PROVIDERS {online}/{len(PROVIDERS)} · ASSISTANTS {asst_online}/{len(ASSISTANTS)} · {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    route_data = build_routes(results)
    for section in ["routes", "aux_routes", "fallbacks"]:
        for r in route_data.get(section, []):
            if section == "aux_routes" and not r.get("online"):
                continue
            ms = f"{r['latency']}ms" if r.get("latency") else "?"
            cost = r.get("cost", "")
            lines.append(
                f"  {r['role']:<10s} {r['route']:<38s} {ms:>6s}  {cost:<9s} {r['reason']}"
            )
    gh = results.get("github", {})
    ge = results.get("gemini", {})
    ds = results.get("deepseek", {})
    limit_parts = []
    if gh.get("status") == "online":
        limit_parts.append(
            f"GitHub core {gh.get('core_remaining', '?')}/{gh.get('core_limit', '?')}"
        )
    if ge.get("status") == "online":
        limit_parts.append(f"Gemini Flash {ge.get('free_tier_rpd_flash', '?')} RPD")
    if ds.get("status") == "online":
        limit_parts.append(f"DeepSeek {_ds_balance_str(ds)}")
    limit_parts.append("OpenAI/Anthropic: dashboard")
    lines.append(f"  limits   {' · '.join(limit_parts)}")
    warns = _collect_warnings(results)
    if warns:
        lines.append(f"  warn     {' · '.join(warns[:3])}")
    return "\n".join(lines)


def fmt_rich(results: dict) -> str:
    """Full boxed hierarchical view."""
    lines = []
    online = sum(1 for p in PROVIDERS if results.get(p, {}).get("status") == "online")
    asst_online = sum(
        1
        for a in ASSISTANTS
        if results.get("_assistants", {}).get(a, {}).get("status") == "online"
    )

    header_text = f"HERMES OPS KIT · PROVIDERS {online}/{len(PROVIDERS)} · ASSISTANTS {asst_online}/{len(ASSISTANTS)}"
    # w = number of ─ between corners.
    # ╭─ + w×─ + ╮ must equal │ + space + header + space + │
    # 3 + w = 4 + len(header_text)  →  w = len(header_text) + 1
    w = len(header_text) + 1
    lines.append(f"╭─{'─' * w}╮")
    lines.append(f"│ {header_text} │")
    lines.append(f"╰─{'─' * w}╯")

    # Infrastructure (from V1)
    bridge = results.get("_bridge", {})
    hermes = results.get("_hermes", {})
    clis = bridge.get("clis", {})
    cli_line = " · ".join(
        f"{k} {v.split(chr(32))[0] if v else '?'}" for k, v in clis.items()
    )
    gw = "🟢" if hermes.get("gateway_running") else "🔴"
    skill = "✓" if hermes.get("bridge_skill_loaded") else "✗"
    lines.append("")
    lines.append("INFRA")
    lines.append(f"  bridge v{bridge.get('bridge_version', '?')} · {cli_line}")
    lines.append(
        f"  hermes {hermes.get('agent', '?')} v{hermes.get('version', '?')} · gateway {gw} · skill {skill}"
    )

    # ROUTE
    route_data = build_routes(results)
    main_routes = route_data.get("routes", [])
    aux_routes = route_data.get("aux_routes", [])
    fb_routes = route_data.get("fallbacks", [])

    if main_routes:
        lines.append("")
        lines.append("ROUTE")
        for r in main_routes:
            ms = f"{r['latency']}ms" if r.get("latency") else "?"
            cost = r.get("cost", "")
            lines.append(
                f"  {r['role']:<10s} {r['route']:<36s} {ms:>6s}  {cost:<9s} {r['reason']}"
            )

    active_aux = [r for r in aux_routes if r.get("online")]
    if active_aux:
        lines.append("")
        lines.append("AUX ROUTES")
        for r in active_aux:
            ms = f"{r['latency']}ms" if r.get("latency") else "?"
            cost = r.get("cost", "")
            role_short = r["role"].replace("aux_", "")
            lines.append(
                f"  {role_short:<12s} {r['route']:<36s} {ms:>6s}  {cost:<9s} {r['reason']}"
            )

    # IMAGE ROUTES — separate from AUX ROUTES
    image_routes = build_image_routes(results)
    active_images = [r for r in image_routes if r.get("online")]
    if active_images:
        lines.append("")
        lines.append("IMAGE ROUTES")
        for r in active_images:
            ms = f"{r['latency']}ms" if r.get("latency") else ""
            cost = r.get("cost", "")
            lines.append(
                f"  {r['role']:<10s} {r['route']:<36s} {ms:>6s}  {cost:<9s} {r['reason']}"
            )
    # Show offline image routes too (dimmed marker)
    offline_images = [r for r in image_routes if not r.get("online")]
    if offline_images and not active_images:
        lines.append("")
        lines.append("IMAGE ROUTES")
        for r in offline_images:
            lines.append(
                f"  {r['role']:<10s} {r['route']:<36s} {'':>6s}  {'OFFLINE':<9s} {r['reason']}"
            )

    if fb_routes:
        lines.append("")
        lines.append("FALLBACKS")
        for r in fb_routes:
            ms = f"{r['latency']}ms" if r.get("latency") else "?"
            cost = r.get("cost", "")
            lines.append(
                f"  {r['role']:<10s} {r['route']:<36s} {ms:>6s}  {cost:<9s} {r['reason']}"
            )

    # ASSISTANTS
    assistants = results.get("_assistants", {})
    if assistants:
        lines.append("")
        lines.append("ASSISTANTS")
        for aid in ASSISTANTS:
            ad = assistants.get(aid, {})
            if not ad:
                continue
            icon = _icon(ad.get("status", "unknown"))
            name = ASSISTANT_NAMES.get(aid, aid.upper())
            ms = _ms(ad)
            if ad.get("status") == "online":
                caps = ad.get("safe_for", [])
                cap_str = (
                    " · ".join(caps[:3]) if caps else ad.get("role", "remote_worker")
                )
                lines.append(
                    f"  {icon} {name:<12s} {ms:>6s}  READY    {ad.get('role', 'remote_worker'):<18s} {cap_str}"
                )
            else:
                err = ad.get("error", "offline")
                lines.append(f"  {icon} {name:<12s} {ms:>6s}  {err[:40]}")

    # PROVIDERS
    lines.append("")
    lines.append("PROVIDERS")
    # Free/included first, then paid
    for p in DISPLAY_ORDER:
        d = results.get(p, {})
        if not d:
            continue
        icon = _icon(d.get("status", "unknown"))
        name = PROVIDER_NAMES[p]
        ms = _ms(d)
        if d.get("status") == "online":
            if p == "github":
                cpm = d.get("copilot_model_count", 0)
                mc_str = f"{cpm:>3d}"
                copilot_ver = d.get("copilot_version", "")
                quota = f"v{copilot_ver}" if copilot_ver else "included"
                cost_label = "FREE"
            else:
                mc = d.get("model_count", d.get("chat_models", 0))
                mc_str = f"{mc:>3d}"
                quota = _quota_str(d, p)
                cost_label = {
                    "gemini": "FREE*",
                    "openai": "PAID",
                    "anthropic": "PAID",
                    "deepseek": "PAID",
                }.get(p, "")
            rec = _rec(d, p)
            lines.append(
                f"  {icon} {name:<12s} {ms:>6s}  {mc_str} models  {cost_label:<6s} rec: {rec:<22s} {quota}"
            )
        else:
            err = d.get("error", "")[:60]
            lines.append(f"  {icon} {name:<12s} {ms:>6s}  OFFLINE  {err}")

    # LIMITS
    lines.append("")
    lines.append("LIMITS")
    gh = results.get("github", {})
    if gh.get("status") == "online" and gh.get("rate_limits"):
        rl = gh["rate_limits"]
        active = {
            k: v
            for k, v in sorted(rl.items(), key=lambda x: -x[1].get("used_pct", 0))
            if v.get("used_pct", 0) > 0
        }
        if active:
            parts = [f"{k} {v['remaining']}/{v['limit']}" for k, v in active.items()]
            if gh.get("rate_limits_reset_iso"):
                parts.append(f"reset {gh['rate_limits_reset_iso'][:16]}")
            lines.append(f"  GitHub     {' · '.join(parts[:6])}")
    ge = results.get("gemini", {})
    if ge.get("status") == "online":
        lines.append(
            f"  Gemini     Flash {ge.get('free_tier_rpd_flash', '?')} RPD · Pro {ge.get('free_tier_rpd_pro', '?')} RPD · ctx {ge.get('max_ctx', '?')}"
        )
    # OpenAI usage + rate limits
    oa = results.get("openai", {})
    rl = oa.get("rate_limits", {})
    if rl and rl.get("req_limit") not in (None, "unknown"):
        # Real rate limits from API probe
        parts = [f"req {rl.get('req_remaining', '?')}/{rl.get('req_limit', '?')}"]
        if rl.get("req_reset") and rl["req_reset"] != "0s":
            parts.append(f"reset {rl['req_reset']}")
        if rl.get("tok_limit") not in (None, "unknown"):
            parts.append(
                f"tok {rl.get('tok_remaining', '?')}/{rl.get('tok_limit', '?')}"
            )
        lines.append(f"  OpenAI     {' · '.join(parts)}")
    else:
        lines.append("  OpenAI     rate limits available")
    # Usage + costs
    usage = oa.get("usage", {})
    if usage.get("tokens_today", 0) > 0:
        parts = [f"today {usage['tokens_today']:,} tok"]
        if usage.get("requests_today"):
            parts.append(f"{usage['requests_today']} req")
        if usage.get("cached_pct") is not None:
            parts.append(f"cache {usage['cached_pct']}%")
        lines.append(f"             {' · '.join(parts)}")
    elif usage.get("error"):
        lines.append(f"             usage: unavailable ({usage['error']})")
    # Cost data
    if oa.get("has_cost_api"):
        c = oa.get("costs", {})
        lines.append(
            "             today ${:.2f} · 7d ${:.2f}".format(
                c.get("today_usd", 0), c.get("total_7d_usd", 0)
            )
        )
    else:
        lines.append(
            f"             cost data: {oa.get('costs_note', 'admin key missing')}"
        )

    # Anthropic
    an = results.get("anthropic", {})
    if an.get("has_cost_api"):
        c = an.get("costs", {})
        lines.append(
            "  Anthropic  today ${:.2f} / 7d ${:.2f}".format(
                c.get("today_usd", 0), c.get("total_7d_usd", 0)
            )
        )
        lines.append(
            "             subscription: resets ~5h (Pro) | API rate limits: org only"
        )
    else:
        lines.append(
            f"  Anthropic  cost data: {an.get('costs_note', 'requires sk-ant-admin (individual: N/A)')}"
        )

    # DeepSeek (balance is the cost analog — no admin key required)
    ds = results.get("deepseek", {})
    if ds.get("status") == "online":
        lines.append(
            f"  DeepSeek   {_ds_balance_str(ds)} · no hard rate limits (dynamic)"
        )

    # WARNINGS
    warns = _collect_warnings(results)
    if warns:
        lines.append("")
        lines.append("WARNINGS")
        for w in warns:
            lines.append(f"  ⚠  {w}")
    else:
        lines.append("")
        lines.append("WARNINGS")
        lines.append("  ✓  No warnings")

    # NEXT
    lines.append("")
    lines.append("NEXT")
    lines.append("  hermes-usage --models      model inventory")
    lines.append("  hermes-usage --limits      rate limits detail")
    lines.append("  hermes-usage --costs       usage/cost telemetry")
    lines.append("  hermes-usage --verbose     all sections")
    lines.append("  hermes-usage --json        machine-readable")
    lines.append("  hermes-usage --compact     minimal view")

    return "\n".join(lines)


def fmt_models(results: dict) -> str:
    lines = ["MODEL INVENTORY", "=" * 60]
    # Copilot first (included)
    gh = results.get("github", {})
    if gh.get("copilot_models"):
        lines.append("\nGITHUB COPILOT — included in subscription")
        for vendor, models in gh["copilot_models"].items():
            lines.append(f"  {vendor}: {', '.join(models)}")
        lines.append(
            f"  total: {gh.get('copilot_model_count', 0)} models across 4 vendors"
        )
    for p in ["openai", "anthropic", "gemini", "deepseek"]:
        d = results.get(p, {})
        if d.get("status") != "online":
            continue
        lines.append(f"\n{p.upper()}")
        if p == "openai":
            for cat, count in d.get("model_categories", {}).items():
                lines.append(f"  {cat}: {count}")
            for label, key in [
                ("🧮 Embedding", "embedding_models"),
                ("🔊 TTS", "tts_models"),
                ("🎤 STT", "stt_models"),
                ("🛡️ Moderation", "moderation_models"),
            ]:
                models = d.get(key, [])
                if models:
                    lines.append(f"  {label}: {', '.join(models)}")
        elif p == "anthropic":
            for tier in ["opus", "sonnet", "haiku"]:
                tier_models = [
                    m
                    for m in d.get("models", [])
                    if m.get("id", "").startswith(f"claude-{tier}")
                ]
                if tier_models:
                    ids = [
                        f"{m['display_name']} ({m['created'][:10]})"
                        for m in sorted(
                            tier_models, key=lambda x: x["created"], reverse=True
                        )
                    ]
                    lines.append(f"  {tier.title()}: {', '.join(ids)}")
        elif p == "gemini":
            for m in d.get("models", [])[:15]:
                meth = [
                    x.replace("generateContent", "gen")
                    .replace("embedContent", "embed")
                    .replace("countTokens", "count")
                    for x in m.get("methods", [])[:4]
                ]
                ctx = (
                    f"{m.get('input_tokens', '?'):>7}" if m.get("input_tokens") else "?"
                )
                out = (
                    f"{m.get('output_tokens', '?'):>6}"
                    if m.get("output_tokens")
                    else "?"
                )
                lines.append(
                    f"  {m['display_name']:<30s} ctx={ctx} out={out} [{', '.join(meth)}]"
                )
        elif p == "deepseek":
            for m in d.get("models", []):
                lines.append(
                    f"  {m['id']:<30s} owned_by={m.get('owned_by', 'deepseek')}"
                )
    return "\n".join(lines)


def fmt_limits(results: dict) -> str:
    lines = ["RATE LIMITS", "=" * 60]
    gh = results.get("github", {})
    if gh.get("status") == "online" and gh.get("rate_limits"):
        lines.append("\nGITHUB — all resource types")
        for name, data in sorted(gh["rate_limits"].items()):
            pct = f" ({data['used_pct']}% used)" if data.get("used_pct", 0) > 0 else ""
            lines.append(
                f"  {name:<35s} {data['remaining']:>6}/{data['limit']:<6}{pct}"
            )
        if gh.get("rate_limits_reset_iso"):
            lines.append(f"  reset: {gh['rate_limits_reset_iso']}")
    for p, label in [
        ("gemini", "GEMINI"),
        ("openai", "OPENAI"),
        ("anthropic", "ANTHROPIC"),
        ("deepseek", "DEEPSEEK"),
    ]:
        d = results.get(p, {})
        if d.get("status") != "online":
            continue
        lines.append(f"\n{label}")
        if p == "gemini":
            lines.append(
                f"  Flash: {d.get('free_tier_rpd_flash', '?')} RPD · Pro: {d.get('free_tier_rpd_pro', '?')} RPD"
            )
            lines.append(f"  Max context: {d.get('max_ctx', '?'):,} tokens")
        elif p == "deepseek":
            lines.append(
                "  No hard rate limits (DeepSeek does not enforce fixed RPM/TPM)"
            )
            lines.append(f"  Balance: {_ds_balance_str(d)}")
            lines.append(f"  See: {d.get('quota_url', '?')}")
        else:
            lines.append("  Rate limits not exposed via API")
            lines.append(f"  See: {d.get('quota_url', '?')}")
    return "\n".join(lines)


# ─── Usage / Cost APIs (requires admin keys for costs) ────────────


def _fetch_openai_rate_limits(key: str) -> dict:
    """Fetch OpenAI rate limits via a minimal API call (costs ~1 token)."""
    try:
        data = json.dumps(
            {
                "model": "gpt-4.1-nano",
                "messages": [{"role": "user", "content": "hi"}],
                "max_completion_tokens": 1,
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        resp = _urlopen(
            req, retries=0
        )  # POST: don't retry (not idempotent — would double-bill)
        # Consume response body
        resp.read()
        return {
            "req_limit": resp.headers.get("x-ratelimit-limit-requests", "unknown"),
            "req_remaining": resp.headers.get(
                "x-ratelimit-remaining-requests", "unknown"
            ),
            "req_reset": resp.headers.get("x-ratelimit-reset-requests", "unknown"),
            "tok_limit": resp.headers.get("x-ratelimit-limit-tokens", "unknown"),
            "tok_remaining": resp.headers.get(
                "x-ratelimit-remaining-tokens", "unknown"
            ),
            "tok_reset": resp.headers.get("x-ratelimit-reset-tokens", "unknown"),
        }
    except Exception:
        return {}


def _fetch_openai_usage(key: str, admin_key: str = "") -> dict:
    """Today's token usage.

    Prefers the current org usage endpoint (/v1/organization/usage/completions,
    admin key); falls back to the legacy /v1/usage endpoint (regular key) when no
    admin key is available. On failure, sets usage["error"] = 'timeout' | 'error'
    so callers can distinguish a failed call from a genuine zero.
    """
    from datetime import datetime as dt, timezone as tz

    usage: dict = {
        "tokens_today": 0,
        "requests_today": 0,
        "input_today": 0,
        "output_today": 0,
        "cached_pct": None,
        "source": None,
        "error": None,
    }
    today_str = dt.now(tz.utc).strftime("%Y-%m-%d")
    day = {"tokens": 0, "input": 0, "output": 0, "cached": 0, "requests": 0}

    try:
        if admin_key:
            usage["source"] = "org-usage"
            start = int(dt.now(tz.utc).timestamp()) - 2 * 86400
            url = (
                f"https://api.openai.com/v1/organization/usage/completions"
                f"?start_time={start}&bucket_width=1d"
            )
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {admin_key}"}
            )
            resp = _urlopen(req)
            data = json.loads(resp.read().decode())
            for bucket in data.get("data", []):
                if (bucket.get("start_time_iso", "") or "")[:10] != today_str:
                    continue
                for r in bucket.get("results", []):
                    inp = r.get("input_tokens", 0) or 0
                    out = r.get("output_tokens", 0) or 0
                    day["input"] += inp
                    day["output"] += out
                    day["tokens"] += inp + out
                    day["cached"] += r.get("input_cached_tokens", 0) or 0
                    day["requests"] += r.get("num_model_requests", 0) or 0
        else:
            usage["source"] = "legacy"
            req = urllib.request.Request(
                f"https://api.openai.com/v1/usage?date={today_str}",
                headers={"Authorization": f"Bearer {key}"},
            )
            resp = _urlopen(req)
            data = json.loads(resp.read().decode())
            for b in data.get("data", []):
                ctx = b.get("context_breakdown", {})
                obj = b.get("object_breakdown", {})
                day["tokens"] += ctx.get("total_tokens", 0) or 0
                day["input"] += (ctx.get("input_cached_tokens", 0) or 0) + (
                    ctx.get("input_uncached_tokens", 0) or 0
                )
                day["output"] += ctx.get("output_tokens", 0) or 0
                day["cached"] += ctx.get("input_cached_tokens", 0) or 0
                day["requests"] += obj.get("num_model_requests", 0) or 0
    except Exception as e:
        usage["error"] = _timeout_reason(e)
        return usage

    usage["tokens_today"] = day["tokens"]
    usage["requests_today"] = day["requests"]
    usage["input_today"] = day["input"]
    usage["output_today"] = day["output"]
    usage["cached_pct"] = (
        round(day["cached"] / max(day["input"], 1) * 100, 1) if day["input"] else None
    )
    return usage


def _fetch_openai_costs(admin_key: str) -> dict:
    """Cost data from /v1/organization/costs (needs OPENAI_ADMIN_KEY with api.usage.read)."""
    import time as _time

    try:
        # start_time required — use last 7 days
        end = int(_time.time())
        start = end - 7 * 86400
        url = f"https://api.openai.com/v1/organization/costs?start_time={start}&end_time={end}&bucket_size=daily"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {admin_key}"}
        )
        resp = _urlopen(req)
        data = json.loads(resp.read().decode())
        costs = data.get("data", []) if isinstance(data, dict) else []
        total = sum(c.get("amount", 0) or 0 for c in costs)
        today_cost = costs[-1].get("amount", 0) if costs else 0
        return {
            "ok": True,
            "total_7d_usd": round(total, 4),
            "today_usd": round(today_cost, 4),
            "buckets": len(costs),
        }
    except Exception as e:
        return {"ok": False, "error": redact(str(e)[:200])}


def _fetch_anthropic_costs(admin_key: str) -> dict:
    """Cost data from Anthropic admin API (needs sk-ant-admin key)."""
    from datetime import datetime as dt, timedelta as td, timezone as tz

    try:
        end = dt.now(tz.utc)
        start = end - td(days=7)
        url = (
            f"https://api.anthropic.com/v1/organizations/cost_report"
            f"?starting_at={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&ending_at={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
        req = urllib.request.Request(
            url, headers={"x-api-key": admin_key, "anthropic-version": "2023-06-01"}
        )
        resp = _urlopen(req)
        data = json.loads(resp.read().decode())
        # Response: {"data": [{"starting_at":..., "ending_at":..., "results": [...]}]}
        total = 0.0
        buckets = 0
        for bucket in data.get("data", []):
            for r in bucket.get("results", []):
                total += r.get("cost_usd", 0) or 0
            buckets += 1
        today_start = dt.now(tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_cost = 0.0
        for bucket in data.get("data", []):
            if bucket.get("starting_at", "").startswith(
                today_start.strftime("%Y-%m-%d")
            ):
                today_cost = sum(
                    r.get("cost_usd", 0) or 0 for r in bucket.get("results", [])
                )
                break
        return {
            "ok": True,
            "total_7d_usd": round(total, 4),
            "today_usd": round(today_cost, 4),
            "buckets": buckets,
        }
    except Exception as e:
        return {"ok": False, "error": redact(str(e)[:200])}


def fmt_costs(results: dict) -> str:
    """Show usage/cost data with degradation for missing keys."""
    lines = ["USAGE / COSTS", "=" * 60]

    for p, label in [("openai", "OPENAI"), ("anthropic", "ANTHROPIC")]:
        d = results.get(p, {})
        lines.append(f"\n{label}")

        if p == "openai":
            # Token usage
            usage = d.get("usage", {})
            if usage.get("tokens_today", 0) > 0:
                parts = [f"today {usage['tokens_today']:,} tok"]
                if usage.get("requests_today"):
                    parts.append(f"{usage['requests_today']} req")
                if usage.get("input_today"):
                    parts.append(f"{usage['input_today']:,}↓")
                if usage.get("output_today"):
                    parts.append(f"{usage['output_today']:,}↑")
                if usage.get("cached_pct") is not None:
                    parts.append(f"cache {usage['cached_pct']}%")
                lines.append(f"  {' · '.join(parts)}")
            elif usage.get("error"):
                lines.append(f"  usage: unavailable ({usage['error']})")
            else:
                lines.append("  no usage today")
            # Cost data
            if d.get("has_cost_api"):
                c = d.get("costs", {})
                lines.append(
                    "  today ${:.2f} · 7d ${:.2f} ({} daily buckets)".format(
                        c.get("today_usd", 0),
                        c.get("total_7d_usd", 0),
                        c.get("buckets", 0),
                    )
                )
            else:
                lines.append(
                    f"  cost data: {d.get('costs_note', 'admin key missing (OPENAI_ADMIN_KEY)')}"
                )

        elif p == "anthropic":
            if d.get("has_cost_api"):
                c = d.get("costs", {})
                lines.append(
                    "  today ${:.2f} · 7d ${:.2f} ({} daily buckets)".format(
                        c.get("today_usd", 0),
                        c.get("total_7d_usd", 0),
                        c.get("buckets", 0),
                    )
                )
            else:
                err = d.get("costs", {}).get("error", "")
                if err:
                    lines.append(f"  cost API: {err[:100]}")
                else:
                    lines.append(
                        f"  cost data: {d.get('costs_note', 'requires sk-ant-admin (individual: N/A)')}"
                    )

    lines.append("\nGITHUB")
    gh = results.get("github", {})
    if gh.get("status") == "online":
        # Rate limit breakdown
        rl = gh.get("rate_limits", {})
        active = {
            k: v
            for k, v in sorted(rl.items(), key=lambda x: -x[1].get("used_pct", 0))
            if v.get("used_pct", 0) > 0
        }
        if active:
            parts = [
                f"{k} {v['remaining']}/{v['limit']} ({v['used_pct']}%)"
                for k, v in active.items()
            ]
            lines.append(f"  rate limits: {' · '.join(parts[:5])}")
        else:
            parts = [
                f"{k} {v['remaining']}/{v['limit']}"
                for k, v in list(sorted(rl.items()))[:5]
            ]
            lines.append(f"  rate limits: {' · '.join(parts)}")
        # Copilot status
        if gh.get("copilot_available"):
            ver = gh.get("copilot_version", "?")
            models = gh.get("copilot_model_count", 0)
            token = gh.get("has_github_token", False)
            rate = "5,000 req/hr" if token else "60 req/hr"
            lines.append(
                f"  copilot: v{ver} · {models} models across 4 vendors · {rate}"
            )
            # Seats/credits for Pro plan
            lines.append(
                "  plan: Pro ($10/mo) · 1,500 AI Credits/mo · included in subscription"
            )
        else:
            lines.append("  copilot: not available")
        # Reset
        reset = gh.get("rate_limits_reset_iso")
        if reset:
            lines.append(f"  reset: {reset[:16]}")
    else:
        lines.append(f"  offline: {gh.get('error', 'unknown error')[:80]}")
    lines.append("\nGEMINI")
    ge = results.get("gemini", {})
    if ge.get("status") == "online":
        lines.append(
            f"  Free tier: {ge.get('free_tier_rpd_flash', '?')} RPD Flash · {ge.get('free_tier_rpd_pro', '?')} RPD Pro"
        )

    lines.append("\nDEEPSEEK")
    ds = results.get("deepseek", {})
    if ds.get("status") == "online":
        b = ds.get("balance", {})
        if b.get("ok"):
            avail = "available" if b.get("is_available") else "INSUFFICIENT"
            lines.append(
                f"  balance: {b.get('total', '?')} {b.get('currency', '')} ({avail})"
            )
            extra = []
            if b.get("granted") is not None:
                extra.append(f"granted {b['granted']}")
            if b.get("topped_up") is not None:
                extra.append(f"topped-up {b['topped_up']}")
            if extra:
                lines.append(f"  {' · '.join(extra)}")
            lines.append(
                "  (no admin key needed — balance is DeepSeek's cost telemetry)"
            )
        else:
            lines.append(
                f"  balance: unavailable ({ds.get('costs_note', 'balance API failed')})"
            )
    elif ds.get("status") == "offline":
        lines.append(f"  offline: {ds.get('error', 'DEEPSEEK_API_KEY not set')}")
    else:
        lines.append(f"  error: {ds.get('error', 'unknown')[:80]}")
    return "\n".join(lines)


# ─── Main ───────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Hermes Ops Kit — Usage Metrics v2")
    parser.add_argument(
        "--provider", "-p", choices=PROVIDERS, help="Check a single provider only"
    )
    parser.add_argument(
        "--json", "-j", action="store_true", help="Machine-readable JSON"
    )
    parser.add_argument(
        "--compact", "-c", action="store_true", help="Minimal routing view"
    )
    parser.add_argument(
        "--rich", "-r", action="store_true", help="Boxed hierarchical view (default)"
    )
    parser.add_argument("--models", action="store_true", help="Model inventory")
    parser.add_argument("--limits", action="store_true", help="Rate limits detail")
    parser.add_argument(
        "--costs", action="store_true", help="Usage/cost telemetry (needs admin keys)"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="All sections")
    parser.add_argument(
        "--plain", action="store_true", help="Plain output: no Unicode, no color"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color codes"
    )
    parser.add_argument(
        "--env-file", default=None, help="Path to env file (default: ~/.hermes/.env)"
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    providers = [args.provider] if args.provider else list(PROVIDERS)
    checks = {
        "openai": check_openai,
        "anthropic": check_anthropic,
        "github": check_github,
        "gemini": check_gemini,
        "deepseek": check_deepseek,
    }
    # Run all probes concurrently — total wall time ≈ slowest single probe
    # instead of the sum (providers + bridge + hermes are independent).
    tasks = {p: checks[p] for p in providers}
    tasks["_bridge"] = check_bridge_health
    tasks["_hermes"] = check_hermes_status
    tasks["_assistants"] = check_all_assistants
    results = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks.items()}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}
    results["_timestamp"] = datetime.now().isoformat()

    _out = lambda text: print(  # noqa: E731
        _apply_output_flags(text, args.plain, args.no_color)
    )

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    elif args.compact:
        _out(fmt_compact(results))
    elif args.models:
        _out(fmt_models(results))
    elif args.limits:
        _out(fmt_limits(results))
    elif args.costs:
        _out(fmt_costs(results))
    elif args.verbose:
        _out(fmt_rich(results))
        _out("\n" + fmt_models(results))
        _out("\n" + fmt_limits(results))
    else:
        _out(fmt_rich(results))


if __name__ == "__main__":
    main()
