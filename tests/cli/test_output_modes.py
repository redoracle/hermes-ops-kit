"""Hermes Ops Kit — Output Mode Matrix Tests.

Layer 3: Test default, --json, --plain, --compact, NO_COLOR across all commands.
"""

from __future__ import annotations

import os

from tests.cli.cli_runner import run_cli, has_ansi  # pyright: ignore[reportMissingImports]
from tests.cli.assertions import (
    assert_exit_ok,
    assert_no_secrets,
    assert_json_valid,
    assert_no_ansi,
)  # pyright: ignore[reportMissingImports]


# ── hermes-usage output modes ────────────────────────────────────


def test_usage_default_has_sections():
    r = run_cli(["usage_metrics_v2.py"])
    assert_exit_ok(r)
    for section in ["ROUTE", "PROVIDERS", "ASSISTANTS"]:
        assert section in r.stdout, f"Missing section: {section}"


def test_usage_compact_is_short():
    r = run_cli(["usage_metrics_v2.py", "--compact"])
    assert_exit_ok(r)
    lines = [l for l in r.stdout.split("\n") if l.strip()]
    assert len(lines) <= 25, f"Compact too long: {len(lines)} lines"


def test_usage_json_is_valid():
    r = run_cli(["usage_metrics_v2.py", "--json"])
    assert_exit_ok(r)
    assert_json_valid(r)
    assert_no_ansi(r)
    assert_no_secrets(r)


def test_usage_no_color_produces_no_ansi():
    r = run_cli(["usage_metrics_v2.py", "--compact"], env={"NO_COLOR": "1"})
    assert not has_ansi(r.stdout), f"ANSI found with NO_COLOR=1"


def test_usage_plain_no_ansi():
    r = run_cli(["usage_metrics_v2.py", "--plain"])
    assert_exit_ok(r)
    assert not has_ansi(r.stdout)


# ── hermes-assistant-manager output modes ────────────────────────


def test_assistant_list_default_is_human():
    r = run_cli(
        ["hermes_assistant_manager.py", "--config", "config/assistants.yaml", "list"]
    )
    assert_exit_ok(r)
    assert "ASSISTANTS" in r.stdout, f"Missing ASSISTANTS header: {r.stdout[:200]}"


def test_assistant_list_json_is_valid():
    r = run_cli(
        [
            "hermes_assistant_manager.py",
            "--config",
            "config/assistants.yaml",
            "list",
            "--json",
        ]
    )
    assert_exit_ok(r)
    assert_json_valid(r)


# ── hermes-key-rotate output modes ───────────────────────────────


def test_key_rotate_doctor_secrets_is_json():
    r = run_cli(["hermes_key_rotate.py", "--doctor-secrets"])
    # May exit non-zero if Vaultwarden is locked — test output shape regardless
    data = assert_json_valid(r)
    assert (
        "SECRET BACKEND" in data
        or "ENV FILES" in data
        or "backend" in str(data).lower()
    ), f"Missing expected keys: {list(data.keys())[:5]}"


def test_key_rotate_help_json_flag_present():
    r = run_cli(["hermes_key_rotate.py", "--help"])
    assert "--json" in r.stdout


# ── hermes-ops-kit doctor ──────────────────────────────────────


def test_bridge_doctor_has_sections():
    r = run_cli(["bridge.py", "doctor"])
    sections = ["CORE", "ROUTES", "ASSISTANTS", "SECRETS", "WARNINGS", "NEXT"]
    found = [s for s in sections if s in r.stdout]
    assert len(found) >= 4, f"Only found sections: {found}"
    assert_no_secrets(r)


def test_bridge_status_json():
    r = run_cli(["bridge.py", "status"])
    assert_exit_ok(r)


# ── hermes-route-manager ─────────────────────────────────────────


def test_route_manager_show():
    r = run_cli(["hermes_route_manager.py", "show"])
    assert_exit_ok(r)
    assert "Primary" in r.stdout or "primary" in r.stdout.lower()


def test_route_manager_doctor():
    r = run_cli(["hermes_route_manager.py", "doctor"])
    assert_exit_ok(r)


# ── Global NO_COLOR ──────────────────────────────────────────────


def test_no_color_env_global():
    """All commands respect NO_COLOR=1."""
    env = {"NO_COLOR": "1"}
    for cmd_args in [
        ["usage_metrics_v2.py", "--compact"],
        ["bridge.py", "doctor"],
        ["hermes_assistant_manager.py", "--config", "config/assistants.yaml", "list"],
        ["hermes_route_manager.py", "show"],
    ]:
        r = run_cli(cmd_args, env=env)
        assert not has_ansi(r.stdout), (
            f"ANSI in {cmd_args[0]} with NO_COLOR=1: {r.stdout[:80]}"
        )
