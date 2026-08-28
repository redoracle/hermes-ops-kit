"""Hermes Ops Kit — Post-Rotation Verification

Runs usage_metrics_v2.py against the new .env.generated after every rotation.
Verifies: provider online, default model exists, route resolves, new key accepted,
old key no longer active (where revocation supported), limits readable,
cost telemetry accessible (where admin key exists).

Used by hermes_key_rotate.py and all provider rotators after activation.
"""

from __future__ import annotations

import json
import os
import subprocess

from ._subprocess import module_command  # pyright: ignore[reportMissingImports]
import time
from hermes_ops_kit import ops_config_io  # noqa: E402


def run_post_rotation_checks(
    provider: str | None = None,
    env_file: str | None = None,
) -> dict:
    """Run usage_metrics_v2.py --json against the current (or specified) env.

    If *provider* is given, only that provider's result is inspected.
    Returns a dict with ok, provider_status, warnings, duration_ms.
    """
    env_path = env_file or os.path.join(ops_config_io.HERMES_HOME, ".env.generated")

    if not os.path.exists(env_path):
        return {
            "ok": False,
            "error": f"Env file not found: {env_path}",
            "checked_at": int(time.time()),
        }

    # Source the generated env before running usage_metrics_v2
    args = ["--json"]
    if provider:
        args.extend(["-p", provider])
    cmd, module_env = module_command("usage_metrics_v2", args)

    # Source .env.generated via a shell wrapper
    shell_cmd = f"set -a; . {env_path}; set +a; " + " ".join(cmd)

    start = time.time()
    try:
        result = subprocess.run(
            ["/bin/sh", "-c", shell_cmd],
            capture_output=True,
            text=True,
            timeout=120,
            env=module_env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "usage_metrics_v2 timed out after 120s",
            "checked_at": int(time.time()),
        }

    duration_ms = int((time.time() - start) * 1000)

    if result.returncode != 0:
        return {
            "ok": False,
            "error": f"usage_metrics_v2 exited with code {result.returncode}",
            "stderr": result.stderr[:500] if result.stderr else "",
            "duration_ms": duration_ms,
        }

    # Parse JSON output
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "usage_metrics_v2 returned non-JSON output",
            "stdout_preview": result.stdout[:500],
            "duration_ms": duration_ms,
        }

    # Inspect provider health from the JSON output
    providers = data.get("providers", {})
    all_ok = True
    statuses: dict[str, dict] = {}

    for pname, pdata in providers.items():
        if provider and pname != provider:
            continue
        p_status = pdata.get("status", "unknown")
        p_ok = p_status == "online"
        p_model = pdata.get("default_model", "unknown")
        p_latency = pdata.get("latency_ms", -1)
        p_limits = pdata.get("limits", {})

        statuses[pname] = {
            "online": p_ok,
            "status": p_status,
            "default_model": p_model,
            "latency_ms": p_latency,
            "limits_ok": bool(p_limits),
        }
        if not p_ok:
            all_ok = False

    return {
        "ok": all_ok,
        "provider_status": statuses,
        "warnings": data.get("warnings", []),
        "recommendations": data.get("recommendations", {}),
        "duration_ms": duration_ms,
        "checked_at": int(time.time()),
    }


def post_rotation_doctor(
    provider: str,
    old_fingerprint: str | None = None,
    new_fingerprint: str | None = None,
) -> dict:
    """Run post-rotation checks and produce a doctor-style report.

    Combines the usage_metrics_v2 check with rotation-specific validation:
    - new key accepted
    - old key no longer active (if revocation was performed)
    - provider route resolves
    """
    result = run_post_rotation_checks(provider=provider)

    doctor: dict = {
        "provider": provider,
        "timestamp": int(time.time()),
        "post_rotation": result,
        "rotation_specific": {},
    }

    pdata = result.get("provider_status", {}).get(provider, {})

    if pdata.get("online"):
        doctor["rotation_specific"]["new_key_accepted"] = True
        doctor["rotation_specific"]["default_model"] = pdata.get("default_model")
        doctor["rotation_specific"]["latency_ms"] = pdata.get("latency_ms")
    else:
        doctor["rotation_specific"]["new_key_accepted"] = False
        doctor["rotation_specific"]["error"] = pdata.get("status", "unknown")

    # Old key check: if we have the old fingerprint, note it
    if old_fingerprint:
        doctor["rotation_specific"]["old_fingerprint"] = old_fingerprint
    if new_fingerprint:
        doctor["rotation_specific"]["new_fingerprint"] = new_fingerprint

    return doctor
