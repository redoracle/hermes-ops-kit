"""env.loader.get_secret(): per-profile credential reads, no environment mutation (S5.0)."""

from __future__ import annotations

import os
import sys

import pytest

from hermes_ops_kit import ops_config_io
from hermes_ops_kit.env import loader


@pytest.fixture
def standalone(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    return tmp_path


def test_standalone_file_value_beats_the_real_environment(standalone, monkeypatch):
    (standalone / ".env").write_text("K=from-file\n")
    monkeypatch.setenv("K", "from-env")
    assert loader.get_secret("K") == "from-file"


def test_standalone_generated_file_beats_bootstrap_file(standalone):
    (standalone / ".env").write_text("K=bootstrap\n")
    (standalone / ".env.generated").write_text("K=generated\n")
    assert loader.get_secret("K") == "generated"


def test_standalone_falls_back_to_environment_then_default(standalone, monkeypatch):
    monkeypatch.setenv("ONLY_ENV", "e")
    monkeypatch.delenv("ABSENT", raising=False)
    assert loader.get_secret("ONLY_ENV") == "e"
    assert loader.get_secret("ABSENT") is None
    assert loader.get_secret("ABSENT", "dflt") == "dflt"


def test_reading_a_secret_never_mutates_the_environment(standalone, monkeypatch):
    (standalone / ".env").write_text("NOT_IN_ENV=1\n")
    monkeypatch.delenv("NOT_IN_ENV", raising=False)
    before = dict(os.environ)
    assert loader.get_secret("NOT_IN_ENV") == "1"
    assert dict(os.environ) == before


def test_embedded_reads_only_the_active_profiles_files(fake_core, profiles):
    profiles.use("a")
    (profiles.path("a") / ".env").write_text("K=key-a\n")
    profiles.use("b")
    (profiles.path("b") / ".env").write_text("K=key-b\n")

    profiles.use("a")
    assert loader.get_secret("K") == "key-a"
    profiles.use("b")
    assert loader.get_secret("K") == "key-b"


def test_embedded_file_beats_core_and_core_fills_gaps(fake_core, profiles):
    profiles.use("a")
    (profiles.path("a") / ".env.generated").write_text("FROM_FILE=file\n")
    fake_core.core_secrets.update({"FROM_FILE": "core", "FROM_CORE": "core-only"})
    assert loader.get_secret("FROM_FILE") == "file"
    assert loader.get_secret("FROM_CORE") == "core-only"
    assert loader.get_secret("NOWHERE", "dflt") == "dflt"


def test_embedded_does_not_read_the_launch_environment(
    fake_core, profiles, monkeypatch
):
    monkeypatch.setenv("LAUNCH_ONLY", "launch-secret")
    profiles.use("a")
    assert loader.get_secret("LAUNCH_ONLY") is None


def test_embedded_unscoped_read_under_multiplexing_raises_even_if_a_file_has_the_key(
    fake_core, profiles
):
    profiles.use("a")
    (profiles.path("a") / ".env").write_text("K=would-be-the-wrong-profile\n")
    fake_core.multiplex = True
    fake_core.scope = None
    with pytest.raises(fake_core.UnscopedSecretError):
        loader.get_secret("K")


def test_embedded_scoped_read_under_multiplexing_is_allowed(fake_core, profiles):
    profiles.use("a")
    (profiles.path("a") / ".env").write_text("K=ok\n")
    fake_core.multiplex = True
    fake_core.scope = {"K": "ok"}
    assert loader.get_secret("K") == "ok"


def test_embedded_without_core_secret_scope_refuses(fake_core, profiles, monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    profiles.use("a")
    with pytest.raises(ops_config_io.ScopedEnvironmentUnavailable):
        loader.get_secret("K")


def test_an_empty_entry_in_the_env_file_does_not_mask_a_real_key(
    standalone, monkeypatch
):
    (standalone / ".env").write_text("OPENAI_API_KEY=\n")
    monkeypatch.setenv("OPENAI_API_KEY", "real-key")
    assert loader.get_secret("OPENAI_API_KEY") == "real-key"


def test_an_empty_entry_falls_through_to_core_when_embedded(fake_core, profiles):
    (profiles.use("a") / ".env").write_text("K=\n")
    fake_core.core_secrets["K"] = "from-core"
    assert loader.get_secret("K") == "from-core"
