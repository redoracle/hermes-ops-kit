"""Tests for Hermes Ops Kit lifecycle hooks."""

from __future__ import annotations

import hooks


def test_plugin_security_scan_reports_blocked_and_deferred(monkeypatch, capsys):
    monkeypatch.setattr(
        "security.plugin_scanner.enforce.get_enforcement_decisions",
        lambda: {
            "blocked": ["critical-plugin"],
            "deferred": ["high-plugin"],
            "details": {
                "critical-plugin": "CRITICAL risk",
                "high-plugin": "HIGH risk requires approval",
            },
        },
    )

    hooks.plugin_security_scan()

    stderr = capsys.readouterr().err
    assert "CRITICAL: critical-plugin: CRITICAL risk" in stderr
    assert "WARNING: high-plugin: HIGH risk requires approval" in stderr
    assert "hermes-ops-kit preflight" in stderr


def test_plugin_security_scan_fails_open_with_warning(monkeypatch, capsys):
    def fail():
        raise RuntimeError("scanner unavailable")

    monkeypatch.setattr(
        "security.plugin_scanner.enforce.get_enforcement_decisions",
        fail,
    )

    hooks.plugin_security_scan()

    stderr = capsys.readouterr().err
    assert "session-start plugin security scan failed: scanner unavailable" in stderr


def test_on_session_start_runs_permissions_and_scan(monkeypatch, capsys):
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(hooks, "_permission_warnings", lambda: ["unsafe permissions"])
    monkeypatch.setattr(
        hooks, "plugin_security_scan", lambda **kwargs: calls.append(kwargs)
    )

    hooks.on_session_start(session_id="session-1")

    assert "unsafe permissions" in capsys.readouterr().err
    assert calls == [{"session_id": "session-1"}]
