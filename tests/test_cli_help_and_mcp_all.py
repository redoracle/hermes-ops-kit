"""`--help` must not run the action; `mcp approve --all` must not invent a server."""

from __future__ import annotations

import pytest

from hermes_ops_kit import commands


@pytest.mark.parametrize("sub", ["setup", "repair", "update"])
def test_install_help_does_not_run_the_action(sub, capsys, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("action ran for --help")

    monkeypatch.setattr(
        "hermes_ops_kit.install_reconciler.updater.perform_update", boom
    )
    monkeypatch.setattr(
        "hermes_ops_kit.security.plugin_scanner.enforce._restore_hermes_config", boom
    )
    assert commands._handle_install([sub, "--help"]) == 0
    assert "Usage: hermes-ops-kit install" in capsys.readouterr().out


@pytest.mark.parametrize(
    "sub", ["scan", "approve", "revoke", "cache", "rules", "disable"]
)
def test_plugin_help_prints_usage(sub, capsys):
    from hermes_ops_kit.security.plugin_scanner.cli import handle_plugin

    assert handle_plugin([sub, "--help"]) == 0
    assert "Plugin Security Scanner" in capsys.readouterr().out


def test_mcp_approve_all_without_servers_approves_nothing(capsys, monkeypatch):
    approved = []
    monkeypatch.setattr(
        "hermes_ops_kit.mcp_auditor.auditor.inventory_servers", lambda *a, **k: []
    )
    code = commands._mcp_approve(
        ["--all"], approve_server_fn=approved.append, approve_tool_fn=lambda t: None
    )
    assert code == 0
    assert approved == []
    assert "nothing to approve" in capsys.readouterr().out
