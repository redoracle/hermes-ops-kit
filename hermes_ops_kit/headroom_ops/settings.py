"""Hermes Ops Kit — Headroom settings loader.

Desired state + knobs live in ``~/.hermes/ops-kit/headroom.yaml``
(seeded from the bundled ``config/headroom.yaml`` on first use).
This file is never a routing source of truth — the live route is
always ``~/.hermes/config.yaml``, written only by ``reconcile``.
"""

from __future__ import annotations

import os

from .. import ops_config_io  # noqa: E402
from ..ops_config_io import OPS_KIT_DIR, load_yaml, save_yaml  # noqa: E402

BUNDLED_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
    "headroom.yaml",
)
DEPLOYED_CONFIG = os.path.join(OPS_KIT_DIR, "headroom.yaml")

# Flags that would turn Headroom into a coding proxy — never allowed.
FORBIDDEN_FLAGS = ("--code-aware", "--code-graph", "--learn")

DEFAULTS: dict = {
    "enabled": False,
    "port": 8790,
    "base_url": "http://127.0.0.1:8790/v1",
    "upstream": {"mode": "provider", "litellm_url": "http://127.0.0.1:8799/v1"},
    "proxy_flags": [
        "--mode",
        "token",
        "--memory",
        "--memory-storage",
        "project",
        "--no-telemetry",
    ],
    "memory_project_root": "~/.hermes",
    "apply": {"model": True, "aux_routes": []},
    "startup_timeout_seconds": 20,
    "run_dir": "~/.hermes/ops-kit/run",
    "state_file": "~/.hermes/ops-kit/headroom_prev_route.json",
}


def seed_deployed() -> None:
    """Copy the bundled headroom.yaml to ~/.hermes/ops-kit on first use."""
    if os.path.exists(DEPLOYED_CONFIG) or not os.path.exists(BUNDLED_CONFIG):
        return
    ops_config_io.deployed_or_bundled("headroom.yaml", seed=True)
    os.chmod(DEPLOYED_CONFIG, 0o600)


def load_settings() -> dict:
    """Return effective settings: DEFAULTS ← bundled ← deployed."""
    seed_deployed()
    merged = dict(DEFAULTS)
    for path in (BUNDLED_CONFIG, DEPLOYED_CONFIG):
        data = load_yaml(path).get("headroom") or {}
        if isinstance(data, dict):
            merged.update(data)
    # Normalize nested dicts against defaults so partial files stay valid.
    for key in ("upstream", "apply"):
        section = dict(DEFAULTS[key])
        if isinstance(merged.get(key), dict):
            section.update(merged[key])
        merged[key] = section
    merged["run_dir"] = ops_config_io.expand_home(str(merged["run_dir"]))
    merged["state_file"] = ops_config_io.expand_home(str(merged["state_file"]))
    merged["memory_project_root"] = ops_config_io.expand_home(
        str(merged["memory_project_root"])
    )
    return merged


def set_desired_enabled(enabled: bool) -> None:
    """Persist the desired state to the deployed headroom.yaml."""
    seed_deployed()
    data = load_yaml(DEPLOYED_CONFIG)
    if not isinstance(data.get("headroom"), dict):
        data["headroom"] = {}
    data["headroom"]["enabled"] = bool(enabled)
    save_yaml(DEPLOYED_CONFIG, data)


def proxy_root(settings: dict) -> str:
    """Proxy root URL (no /v1) for health/stats endpoints."""
    base = str(settings.get("base_url", "")).rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def sanitized_proxy_flags(settings: dict) -> list[str]:
    """Proxy flags with the no-coding profile enforced."""
    flags = [str(f) for f in settings.get("proxy_flags") or []]
    flags = [f for f in flags if f not in FORBIDDEN_FLAGS]
    if "--no-code-aware" not in flags:
        flags.append("--no-code-aware")
    return flags
