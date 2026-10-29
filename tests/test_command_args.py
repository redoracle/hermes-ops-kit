"""Hand-dispatched commands: `-h` never acts, unknown flags are refused, input is validated."""

from __future__ import annotations

import pytest

from hermes_ops_kit import commands

GROUPS = {
    "doctor": "_handle_doctor",
    "mcp": "_handle_mcp",
    "budget": "_handle_budget",
    "maintenance": "_handle_maintenance",
    "audit": "_handle_audit",
    "assistants": "_handle_assistants",
    "assistant": "_handle_assistants",
}


@pytest.fixture()
def no_action(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a handler ran")

    for name in set(GROUPS.values()):
        monkeypatch.setattr(commands, name, boom)


@pytest.mark.parametrize("group", sorted(GROUPS))
@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_and_runs_nothing(group, flag, no_action, capsys):
    assert commands.handle_ops_kit_command([group, flag]) == 0
    assert "Usage: hermes-ops-kit" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["mcp", "revoke", "-h"],
        ["mcp", "approve", "--all", "-h"],
        ["mcp", "approve", "--server", "s", "--help"],
        ["audit", "export", "-h"],
        ["maintenance", "run", "-h"],
        ["assistants", "delegate", "-h"],
        ["budget", "policy", "-h"],
    ],
)
def test_help_after_a_mutating_subcommand_runs_nothing(args, no_action, capsys):
    assert commands.handle_ops_kit_command(args) == 0
    assert "Usage:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["doctor", "--bogus"],
        ["mcp", "list", "--bogus"],
        ["mcp", "risks", "--bogus"],
        ["mcp", "approve", "--bogus"],
        ["budget", "status", "--bogus"],
        ["audit", "tail", "--bogus"],
        ["audit", "search", "--bogus", "x"],
        ["maintenance", "run", "--bogus"],
        ["assistants", "list", "--bogus"],
    ],
)
def test_unknown_flags_are_refused(args, no_action, capsys, monkeypatch):
    if args == ["mcp", "tools", "--server"]:
        pytest.skip(
            "a trailing value flag is accepted by the guard and rejected by the handler"
        )
    assert commands.handle_ops_kit_command(args) == 2
    out = capsys.readouterr().out
    assert "unknown option" in out and "Accepted:" in out


@pytest.mark.parametrize(
    "args",
    [
        ["mcp", "tools", "--server", "s"],
        ["mcp", "approve", "--all"],
        ["mcp", "approve", "--server", "s"],
        ["mcp", "approve", "--tool", "mcp_s_t"],
        ["audit", "search", "--type", "t", "--assistant", "a", "--provider", "p"],
        ["audit", "tail", "5"],
        ["assistants", "delegate", "id", "--capability", "c", "--task", "do -h now"],
        ["maintenance", "run", "profile"],
        ["budget", "check-route", "openai"],
    ],
)
def test_documented_forms_pass_the_guard(args):
    assert commands._check_args(args[0], args[1:]) is None


def test_a_flag_value_may_not_look_like_a_flag():
    """`--task -h` must not run a delegation whose task is "-h"; a value cannot be an option."""
    assert commands._check_args("assistants", ["delegate", "id", "--task", "-h"]) == 2
    assert commands._check_args("mcp", ["approve", "--server", "--all"]) == 2
    assert commands._check_args("mcp", ["approve", "--server", "-h"]) == 2
    assert commands._check_args("mcp", ["approve", "--tool"]) == 2


@pytest.mark.parametrize("value", ["abc", "-3", "1.5", "--json"])
def test_audit_tail_rejects_a_non_number_without_a_traceback(value, capsys):
    assert commands.handle_ops_kit_command(["audit", "tail", value]) == 2
    out = capsys.readouterr().out
    assert "audit tail takes a number" in out or "unknown option" in out


def test_audit_tail_accepts_a_number(monkeypatch, capsys):
    monkeypatch.setattr("hermes_ops_kit.audit.ledger.tail_events", lambda n: [])
    assert commands.handle_ops_kit_command(["audit", "tail", "3"]) == 0


@pytest.mark.parametrize(
    "args",
    [
        ["doctor", "--bogus"],
        ["doctor", "--json", "--bogus"],
        ["update", "--bogus"],
        ["repair", "--force"],
    ],
)
def test_install_refuses_unknown_flags_before_acting(args, capsys, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("an install action ran")

    monkeypatch.setattr(
        "hermes_ops_kit.install_reconciler.cli.run_install_doctor", boom
    )
    monkeypatch.setattr(
        "hermes_ops_kit.install_reconciler.updater.perform_update", boom
    )
    monkeypatch.setattr(
        "hermes_ops_kit.security.plugin_scanner.enforce._restore_hermes_config", boom
    )
    assert commands._handle_install(args) == 2
    assert "unknown option" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["doctor", "--json", "--verbose", "--python", "/x/python"],
        ["doctor", "-v", "--repair"],
        ["update", "--dry-run", "--python", "./-weird-path"],
    ],
)
def test_install_documented_flags_pass(args):
    sub, rest = args[0], args[1:]
    assert (
        commands._check_flags("install", commands._INSTALL_FLAGS[sub], rest, None)
        is None
    )


def test_every_chat_allowlisted_command_survives_every_presentation_flag():
    """`/ops-kit doctor --json` etc. must not be refused by the strict flag guard."""
    from hermes_ops_kit import slash
    from hermes_ops_kit.security.plugin_scanner import cli

    for entry, spec in slash.ALLOWED_COMMANDS.items():
        group, rest = entry[0], list(entry[1:])
        positional = ["5"] if spec.positional is not None else []
        for flag in sorted(slash._PRESENTATION_FLAGS):
            args = [*rest, *positional, flag]
            if group in commands._GUARDED_FLAGS:
                assert commands._check_args(group, args) is None, (entry, flag)
            elif group == "install":
                assert (
                    commands._check_flags(
                        "install",
                        commands._INSTALL_FLAGS[args[0]],
                        args[1:],
                        ignored=commands.PRESENTATION_FLAGS,
                    )
                    is None
                ), (entry, flag)
            elif group == "plugin":
                accepted = cli._PLUGIN_FLAGS.get(args[0], {})
                assert (
                    commands._check_flags(
                        "plugin",
                        accepted,
                        args[1:],
                        ignored=commands.PRESENTATION_FLAGS,
                    )
                    is None
                ), (entry, flag)
