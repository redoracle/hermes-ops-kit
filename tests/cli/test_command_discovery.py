"""Hermes Ops Kit — Command Discovery Tests.

Layer 1: Verify every command binary is reachable and its --help works.
"""

from __future__ import annotations

import pytest

from tests.cli.cli_runner import run_cli  # pyright: ignore[reportMissingImports]
from tests.cli.assertions import assert_exit_ok  # pyright: ignore[reportMissingImports]

COMMANDS = [
    "hermes_assistant_manager.py",
    "hermes_export.py",
    "hermes_key_rotate.py",
    "hermes_route_manager.py",
    "hermes_skill_factory.py",
    "usage_metrics_v2.py",
    "bridge.py",
]


def test_all_commands_help_works():
    """Every command binary responds to --help with exit 0."""
    for cmd in COMMANDS:
        r = run_cli([cmd, "--help"])
        assert_exit_ok(r)
        assert (
            "usage:" in r.stdout.lower()
            or "usage" in r.stdout.lower()
            or "Hermes" in r.stdout
        ), f"{cmd} --help missing usage info: {r.stdout[:100]}"


def test_bridge_help_lists_subcommands():
    r = run_cli(["bridge.py", "--help"])
    for sub in [
        "health",
        "doctor",
        "assistants",
        "mcp",
        "budget",
        "maintenance",
        "audit",
    ]:
        assert sub in r.stdout, f"bridge.py --help missing subcommand: {sub}"


def test_bridge_forwards_audit_search_filters(monkeypatch):
    """Bridge must pass audit filters through to commands.py unchanged."""
    import sys

    from hermes_ops_kit import bridge
    from hermes_ops_kit import commands

    received: list[str] = []
    monkeypatch.setattr(commands, "handle_ops_kit_command", lambda args: received.extend(args) or 0)
    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes-ops-kit", "audit", "search", "--type", "policy_denied"],
    )

    with pytest.raises(SystemExit) as exc:
        bridge.main()

    assert exc.value.code == 0
    assert received == ["audit", "search", "--type", "policy_denied"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["assistants", "delegate", "example", "--capability", "review", "--task", "x"],
            ["assistants", "delegate", "example", "--capability", "review", "--task", "x"],
        ),
        (["budget", "check-route", "nvidia"], ["budget", "check-route", "nvidia"]),
        (["maintenance", "run", "weekly-index-audit"], ["maintenance", "run", "weekly-index-audit"]),
    ],
)
def test_bridge_forwards_action_arguments(monkeypatch, argv, expected):
    import sys

    from hermes_ops_kit import bridge
    from hermes_ops_kit import commands

    received: list[str] = []
    monkeypatch.setattr(commands, "handle_ops_kit_command", lambda args: received.extend(args) or 0)
    monkeypatch.setattr(sys, "argv", ["hermes-ops-kit", *argv])

    with pytest.raises(SystemExit) as exc:
        bridge.main()

    assert exc.value.code == 0
    assert received == expected


def test_delegate_failure_returns_nonzero(monkeypatch):
    from hermes_ops_kit import commands

    monkeypatch.setattr(
        "hermes_ops_kit.assistants.tool.ai_assistant_delegate",
        lambda *_args, **_kwargs: {"ok": False, "error": "simulated server failure"},
    )

    assert (
        commands._handle_assistants(
            ["delegate", "example", "--capability", "review", "--task", "test"]
        )
        == 1
    )


def test_usage_help_lists_modes():
    r = run_cli(["usage_metrics_v2.py", "--help"])
    for mode in ["--json", "--compact", "--models", "--limits"]:
        assert mode in r.stdout, f"usage_metrics_v2.py --help missing: {mode}"


def test_key_rotate_help_has_flag_descriptions():
    r = run_cli(["hermes_key_rotate.py", "--help"])
    for flag in ["--dry-run", "--doctor-secrets", "--render-env"]:
        assert flag in r.stdout, f"Missing flag: {flag}"


def test_route_manager_help_shows_subcommands():
    r = run_cli(["hermes_route_manager.py", "--help"])
    for sub in ["show", "doctor", "apply-profile", "fallback"]:
        assert sub in r.stdout, f"Missing: {sub}"


def test_assistant_manager_help_shows_subcommands():
    r = run_cli(["hermes_assistant_manager.py", "--help"])
    for sub in ["list", "get", "add", "validate", "doctor", "ping", "discover"]:
        assert sub in r.stdout, f"Missing: {sub}"
