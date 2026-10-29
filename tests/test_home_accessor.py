"""ops_config_io.home(): the call-time Hermes home accessor (F2, S4.0).

Order: explicit override (HERMES_HOME rebound on the module) > core
get_hermes_home() when core is loaded > HERMES_HOME env read now > ~/.hermes.
"""

from __future__ import annotations

import builtins
import os
import sys
import types
from pathlib import Path

import pytest

from hermes_ops_kit import ops_config_io


@pytest.fixture
def fresh(monkeypatch):
    """No explicit override, no loaded core, no HERMES_HOME in the environment."""
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", ops_config_io._IMPORT_TIME_HOME)
    monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)


def _fake_core(monkeypatch, value):
    """Install a fake ``hermes_constants``; ``value`` may be a callable or a path."""
    module = types.ModuleType("hermes_constants")
    setattr(
        module, "get_hermes_home", value if callable(value) else (lambda: Path(value))
    )
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    return module


@pytest.mark.real_home
def test_default_is_dot_hermes_in_the_user_home(fresh):
    assert ops_config_io.home() == os.path.expanduser("~/.hermes")


def test_env_var_is_read_at_call_time_not_import_time(fresh, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "a"))
    assert ops_config_io.home() == str(tmp_path / "a")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "b"))
    assert ops_config_io.home() == str(tmp_path / "b")


def test_env_var_tilde_is_expanded(fresh, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "~/custom-hermes")
    assert ops_config_io.home() == os.path.expanduser("~/custom-hermes")


def test_explicit_override_beats_env_and_core(fresh, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env"))
    _fake_core(monkeypatch, tmp_path / "core")
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path / "override"))
    assert ops_config_io.home() == str(tmp_path / "override")


def test_loaded_core_decides_when_there_is_no_override(fresh, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env"))
    _fake_core(monkeypatch, tmp_path / "core")
    assert ops_config_io.home() == str(tmp_path / "core")


def test_core_is_asked_on_every_call_so_profiles_can_switch(
    fresh, monkeypatch, tmp_path
):
    current = {"home": tmp_path / "profile-a"}
    _fake_core(monkeypatch, lambda: current["home"])
    assert ops_config_io.home() == str(tmp_path / "profile-a")
    current["home"] = tmp_path / "profile-b"
    assert ops_config_io.home() == str(tmp_path / "profile-b")


def test_core_without_the_accessor_falls_back_to_the_environment(
    fresh, monkeypatch, tmp_path
):
    monkeypatch.setitem(sys.modules, "hermes_constants", types.ModuleType("x"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env"))
    assert ops_config_io.home() == str(tmp_path / "env")


def test_an_unloaded_core_is_never_imported(fresh, monkeypatch, tmp_path):
    """Standalone processes keep the HERMES_HOME contract: no import is attempted."""
    imported = []
    real_import = builtins.__import__

    def spy(name, *args, **kwargs):
        if name == "hermes_constants":
            imported.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", spy)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    ops_config_io.home()
    assert imported == []


def test_derived_paths_follow_home(fresh, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert ops_config_io.config_path() == str(tmp_path / "config.yaml")
    assert ops_config_io.hermes_config() == str(tmp_path / "config.yaml")
    assert ops_config_io.ops_kit_dir() == str(tmp_path / "ops-kit")


def test_derived_paths_follow_a_switching_profile(fresh, monkeypatch, tmp_path):
    current = {"home": tmp_path / "one"}
    _fake_core(monkeypatch, lambda: current["home"])
    assert ops_config_io.ops_kit_dir() == str(tmp_path / "one" / "ops-kit")
    current["home"] = tmp_path / "two"
    assert ops_config_io.ops_kit_dir() == str(tmp_path / "two" / "ops-kit")


def test_expand_home_maps_dot_hermes_to_the_active_home(fresh, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert ops_config_io.expand_home("~/.hermes") == str(tmp_path)
    assert ops_config_io.expand_home("~/.hermes/cache/x") == str(
        tmp_path / "cache" / "x"
    )
    assert ops_config_io.expand_home("~/elsewhere") == os.path.expanduser("~/elsewhere")


def test_deployed_or_bundled_prefers_the_active_profile_and_seeds_into_it(
    fresh, monkeypatch, tmp_path
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    bundled = ops_config_io.bundled_path("routes.yaml")
    assert os.path.exists(bundled)
    assert (
        ops_config_io.deployed_or_bundled("routes.yaml") == bundled
    )  # not deployed yet
    seeded = ops_config_io.deployed_or_bundled("routes.yaml", seed=True)
    assert seeded == str(tmp_path / "ops-kit" / "routes.yaml")
    assert os.path.exists(seeded)


def test_import_time_aliases_remain_for_the_compatibility_window():
    for name in ("HERMES_HOME", "HERMES_CONFIG", "OPS_KIT_DIR"):
        assert isinstance(getattr(ops_config_io, name), str)
    assert ops_config_io.HERMES_CONFIG == os.path.join(
        ops_config_io._IMPORT_TIME_HOME, "config.yaml"
    )
