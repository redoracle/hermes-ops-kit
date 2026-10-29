"""child_env()/minimal_env(): environments for children that act for a profile (S5.0)."""

from __future__ import annotations

import os
import sys

import pytest

from hermes_ops_kit import _subprocess, ops_config_io


@pytest.fixture
def standalone(monkeypatch, tmp_path):
    """No core loaded; an explicit home so the child resolves a known profile."""
    monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    return tmp_path


def test_standalone_child_env_is_the_parent_environment_plus_home(
    standalone, monkeypatch
):
    monkeypatch.setenv("SOME_PARENT_VAR", "kept")
    env = _subprocess.child_env({"EXTRA": "1"})
    assert env["SOME_PARENT_VAR"] == "kept"
    assert env["HERMES_HOME"] == str(standalone)
    assert env["EXTRA"] == "1"
    assert "HERMES_HOME" not in os.environ or os.environ["HERMES_HOME"] != "sentinel"


def test_standalone_never_calls_core(standalone, fake_core):
    sys.modules.pop(
        "hermes_constants"
    )  # fake_core installs it via profiles; simulate standalone
    _subprocess.child_env()
    assert fake_core.child_env_calls == []


def test_embedded_child_env_comes_from_core_for_the_active_profile(fake_core, profiles):
    home = profiles.use("a")
    env = _subprocess.child_env()
    assert fake_core.child_env_calls == [
        {"target_home": str(home), "inherit_credentials": True}
    ]
    assert env["HERMES_HOME"] == str(home)

    other = profiles.use("b")
    env = _subprocess.child_env()
    assert fake_core.child_env_calls[-1]["target_home"] == str(other)
    assert env["HERMES_HOME"] == str(other)


def test_credentials_flag_is_forwarded_to_core(fake_core, profiles):
    profiles.use("a")
    _subprocess.child_env(credentials=False)
    assert fake_core.child_env_calls[-1]["inherit_credentials"] is False


def test_embedded_child_env_does_not_inherit_the_launch_environment(
    fake_core, profiles, monkeypatch
):
    monkeypatch.setenv("LAUNCH_PROFILE_API_KEY", "launch-secret")
    fake_core.child_env_result = {"PATH": "/usr/bin"}  # core scrubbed everything else
    profiles.use("a")
    env = _subprocess.child_env()
    assert "LAUNCH_PROFILE_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"


def test_caller_extras_are_applied_last(fake_core, profiles):
    profiles.use("a")
    assert (
        _subprocess.child_env({"HERMES_HOME": "/override"})["HERMES_HOME"]
        == "/override"
    )


def test_embedded_without_the_core_helper_refuses_instead_of_falling_back(
    fake_core, profiles, monkeypatch
):
    monkeypatch.delattr(fake_core.local_module, "served_profile_child_env")
    profiles.use("a")
    with pytest.raises(
        ops_config_io.ScopedEnvironmentUnavailable, match="launch profile"
    ):
        _subprocess.child_env()


def test_embedded_without_the_core_module_refuses(fake_core, profiles, monkeypatch):
    monkeypatch.setitem(sys.modules, "tools.environments.local", None)  # import fails
    profiles.use("a")
    with pytest.raises(ops_config_io.ScopedEnvironmentUnavailable):
        _subprocess.child_env()


def test_core_refusal_propagates(fake_core, profiles, monkeypatch):
    def refuse(*args, **kwargs):
        raise fake_core.UnscopedSecretError("", "no scope under multiplexing")

    monkeypatch.setattr(fake_core.local_module, "served_profile_child_env", refuse)
    profiles.use("a")
    with pytest.raises(fake_core.UnscopedSecretError):
        _subprocess.child_env()


def test_module_command_uses_the_profile_environment(fake_core, profiles, monkeypatch):
    home = profiles.use("a")
    fake_core.child_env_result = {
        "PATH": "/usr/bin",
        "PYTHONPATH": os.pathsep.join(["relative/dir", "", "/abs/one"]),
    }
    cmd, env = _subprocess.module_command("bridge", ["capabilities"])
    assert cmd[1:4] == ["-P", "-m", "hermes_ops_kit.bridge"]
    assert env["HERMES_HOME"] == str(home)
    entries = env["PYTHONPATH"].split(os.pathsep)
    assert entries[0] == _subprocess.package_root()
    assert "/abs/one" in entries and "relative/dir" not in entries and "" not in entries


def test_minimal_env_has_only_what_a_plain_tool_needs(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy")
    env = _subprocess.minimal_env({"X": "1"})
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u" and env["X"] == "1"
    assert "OPENAI_API_KEY" not in env and "HTTPS_PROXY" not in env
