"""Without a vault configured, key-rotate says what to set instead of echoing a placeholder."""

from __future__ import annotations

import json
import sys

import pytest

from hermes_ops_kit import hermes_key_rotate


@pytest.fixture()
def home(tmp_path, monkeypatch):
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes_key_rotate, "load_hermes_env", lambda: {})
    return tmp_path


@pytest.mark.parametrize("env", [{}, {"VAULTWARDEN_SERVER_URL": "  "}])
def test_missing_server_url_is_named(home, env, capsys, monkeypatch):
    monkeypatch.setattr(hermes_key_rotate, "load_hermes_env", lambda: env)
    monkeypatch.setattr(sys, "argv", ["hermes-key-rotate", "--healthcheck"])
    with pytest.raises(SystemExit) as exit_info:
        hermes_key_rotate.main()
    assert exit_info.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error_type"] == "SecretBackendNotConfigured"
    assert "VAULTWARDEN_SERVER_URL" in out["error"] and str(home) in out["error"]
    assert "<vaultwarden-url>" not in out["error"]


def test_a_configured_http_url_still_reports_the_https_rule(home, capsys, monkeypatch):
    monkeypatch.setattr(
        hermes_key_rotate,
        "load_hermes_env",
        lambda: {"VAULTWARDEN_SERVER_URL": "http://vault.example"},
    )
    monkeypatch.setattr(sys, "argv", ["hermes-key-rotate", "--healthcheck"])
    with pytest.raises(SystemExit):
        hermes_key_rotate.main()
    out = json.loads(capsys.readouterr().out)
    assert (
        out["error_type"] == "InsecureSecretBackendError"
        and "http://vault.example" in out["error"]
    )
