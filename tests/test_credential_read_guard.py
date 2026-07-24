"""Hermes Ops Kit — credential-read guard tests.

Exercises the local denylist fallback (mirrors Hermes core
agent/file_safety.py). Core delegation is forced off so the tests are
deterministic regardless of whether agent.file_safety is importable.

Run with: python3 -m pytest tests/test_credential_read_guard.py -v
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import security.credential_read_guard as guard  # pyright: ignore[reportMissingImports]


@pytest.fixture
def local_only(monkeypatch):
    """Force the local denylist (ignore any core agent.file_safety present)."""
    monkeypatch.setattr(guard, "_core_raise", None)
    monkeypatch.setattr(guard, "_core_error", None)
    return guard


def test_blocks_env_file(local_only, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    env = tmp_path / ".env"
    env.write_text("SECRET=x")
    with pytest.raises(ValueError):
        local_only.raise_if_read_blocked(str(env))
    assert local_only.get_read_block_error(str(env)) is not None


def test_blocks_envrc_and_env_variants(local_only, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for name in (".env.local", ".env.production", ".envrc"):
        p = tmp_path / name
        p.write_text("x")
        with pytest.raises(ValueError):
            local_only.raise_if_read_blocked(str(p))


def test_blocks_hermes_auth_json(local_only, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    with pytest.raises(ValueError):
        local_only.raise_if_read_blocked(str(auth))


def test_blocks_skills_hub_cache(local_only, tmp_path, monkeypatch):
    # Core blocks ONLY skills/.hub (the prompt-injection cache).
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    hub = tmp_path / "skills" / ".hub" / "evil.md"
    hub.parent.mkdir(parents=True)
    hub.write_text("inject")
    with pytest.raises(ValueError):
        local_only.raise_if_read_blocked(str(hub))


def test_allows_user_skill_assets(local_only, tmp_path, monkeypatch):
    # User skill files (not the .hub cache) must NOT be blocked — core parity.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    asset = tmp_path / "skills" / "my-skill" / "assets" / "ref.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"\x89PNG")
    assert local_only.get_read_block_error(str(asset)) is None
    local_only.raise_if_read_blocked(str(asset))  # must NOT raise


def test_blocks_root_credential_store_in_profile_mode(local_only, tmp_path, monkeypatch):
    # In profile mode HERMES_HOME=<root>/profiles/<name>; a model-supplied read
    # of the ROOT <root>/auth.json must still be blocked (dual-root check:
    # HERMES_HOME + the global Hermes root ~/.hermes). Monkeypatch HOME so the
    # global root resolves inside the temp dir for isolation.
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "dev"
    profile.mkdir(parents=True)
    auth = root / "auth.json"
    auth.write_text("{}")
    monkeypatch.setenv("HERMES_HOME", str(profile))
    with pytest.raises(ValueError):
        local_only.raise_if_read_blocked(str(auth))


def test_allows_legit_image(local_only, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG")
    assert local_only.get_read_block_error(str(img)) is None
    local_only.raise_if_read_blocked(str(img))  # must NOT raise


def test_allows_opskit_workflow_path(local_only, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    wf = tmp_path / "ops-kit" / "workflows" / "flux.json"
    wf.parent.mkdir(parents=True)
    wf.write_text("{}")
    assert local_only.get_read_block_error(str(wf)) is None
    local_only.raise_if_read_blocked(str(wf))  # must NOT raise


def test_empty_path_no_raise(local_only):
    assert local_only.get_read_block_error("") is None
    local_only.raise_if_read_blocked("")  # must NOT raise


def test_path_outside_hermes_home_env_still_blocked(local_only, tmp_path, monkeypatch):
    """Project .env* files are blocked anywhere on disk, not just under HERMES_HOME."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    proj_env = tmp_path / "project" / ".env"
    proj_env.parent.mkdir(parents=True)
    proj_env.write_text("x")
    with pytest.raises(ValueError):
        local_only.raise_if_read_blocked(str(proj_env))
