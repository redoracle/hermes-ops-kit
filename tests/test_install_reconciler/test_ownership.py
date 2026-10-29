"""Ownership classification: who owns an interpreter's environment (F3, S6.0)."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from hermes_ops_kit.install_reconciler.discovery import (
    classify_ownership,
    resolve_runtime_context,
)
from hermes_ops_kit.install_reconciler.state import RuntimeContext


def _venv(root: Path) -> Path:
    """A fake venv: ``root/bin/python`` exists. Returns the interpreter path."""
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python"
    python.write_text("#!/bin/sh\n")
    return python


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake home with a Hermes tree, and no Hermes-related environment variables."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("HERMES_AGENT_SOURCE", "HERMES_INSTALL_ROOT"):
        monkeypatch.delenv(name, raising=False)
    for name in ("pm.environments", "pm.paths"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return tmp_path


def test_a_venv_outside_every_hermes_location_is_standalone(machine):
    owned = classify_ownership(str(_venv(machine / "projects" / "app" / ".venv")))
    assert (owned.role, owned.mutable) == ("standalone", True)


def test_core_install_state_environments_are_managed(machine):
    python = _venv(machine / ".hermes/installs/abc123/environments/def456/venv")
    owned = classify_ownership(str(python))
    assert owned.role == "managed" and not owned.mutable
    assert "install state" in owned.reason


def test_core_source_tree_environments_are_managed(machine):
    for name in ("venv", ".venv"):
        python = _venv(machine / ".hermes/hermes-agent" / name)
        assert classify_ownership(str(python)).role == "managed"


def test_an_explicit_agent_source_is_honoured(machine, monkeypatch):
    source = machine / "elsewhere" / "agent"
    monkeypatch.setenv("HERMES_AGENT_SOURCE", str(source))
    assert classify_ownership(str(_venv(source / "venv"))).role == "managed"
    assert (
        classify_ownership(str(_venv(machine / "other" / "venv"))).role == "standalone"
    )


def test_a_symlinked_environment_cannot_hide_inside_a_managed_location(machine):
    managed = machine / ".hermes/installs/abc/environments/def/venv"
    _venv(managed)
    link = machine / "innocent-looking"
    link.symlink_to(managed, target_is_directory=True)
    assert classify_ownership(str(link / "bin" / "python")).role == "managed"


def test_a_venv_python_symlinked_to_its_base_does_not_escape_classification(machine):
    """The binary is never resolved: a venv's bin/python points at its base interpreter."""
    base = machine / "base" / "python3"
    base.parent.mkdir()
    base.write_text("#!/bin/sh\n")
    venv_root = machine / ".hermes/installs/abc/environments/def/venv"
    (venv_root / "bin").mkdir(parents=True)
    (venv_root / "bin" / "python").symlink_to(base)
    assert classify_ownership(str(venv_root / "bin" / "python")).role == "managed"
    # and a standalone venv built from the same base stays standalone
    standalone = machine / "mine" / ".venv"
    (standalone / "bin").mkdir(parents=True)
    (standalone / "bin" / "python").symlink_to(base)
    assert classify_ownership(str(standalone / "bin" / "python")).role == "standalone"


def test_an_unresolvable_target_is_unknown_not_standalone(machine):
    owned = classify_ownership(str(machine / "no" / "such" / "bin" / "python"))
    assert owned.role == "unknown" and not owned.mutable


def _fake_pm(monkeypatch, committed):
    environments = types.ModuleType("pm.environments")
    setattr(environments, "committed_venv", committed)
    paths = types.ModuleType("pm.paths")
    setattr(paths, "install_root", lambda: Path("/core"))
    monkeypatch.setitem(sys.modules, "pm.environments", environments)
    monkeypatch.setitem(sys.modules, "pm.paths", paths)


def test_a_loaded_pm_decides_for_its_committed_environment(machine, monkeypatch):
    venv = machine / "custom-location" / "venv"
    python = _venv(venv)
    _fake_pm(monkeypatch, lambda root: venv)
    owned = classify_ownership(str(python))
    assert owned.role == "managed" and "committed" in owned.reason


def test_a_loaded_pm_with_no_committed_environment_falls_back_to_path_evidence(
    machine, monkeypatch
):
    _fake_pm(monkeypatch, lambda root: None)
    assert classify_ownership(str(_venv(machine / "x" / "venv"))).role == "standalone"


def test_a_corrupt_core_record_refuses_instead_of_guessing(machine, monkeypatch):
    def corrupt(root):
        raise RuntimeError("invalid dependency environment record")

    _fake_pm(monkeypatch, corrupt)
    owned = classify_ownership(str(_venv(machine / "x" / "venv")))
    assert owned.role == "unknown" and "unreadable" in owned.reason


def test_resolve_runtime_context_records_the_role_and_its_evidence(machine):
    python = _venv(machine / ".hermes/installs/a/environments/b/venv")
    context = resolve_runtime_context(str(python))
    assert context.runtime_role == "managed"
    assert "install state" in context.ownership_reason
    assert context.to_dict()["runtime_role"] == "managed"


def test_the_role_is_never_assumed(machine):
    assert RuntimeContext(python_executable="/x/bin/python").runtime_role == "unknown"
    assert os.path.exists(str(machine))  # fixture sanity


# --- review follow-ups: identity-based containment and the full Hermes-owned tree ---------


def _case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.write_text("x")
    return (directory / "caseprobe").exists()


def test_a_case_variant_spelling_cannot_hide_a_managed_environment(machine):
    if not _case_insensitive(machine):
        pytest.skip("case-sensitive filesystem: a case variant is a different path")
    python = _venv(machine / ".hermes/hermes-agent/venv")
    variant = (
        str(python).replace("hermes-agent", "HERMES-AGENT").replace("/venv/", "/VENV/")
    )
    assert classify_ownership(variant).role == "managed"


def test_core_package_manager_runtime_interpreters_are_managed(machine):
    python = _venv(machine / ".hermes/installs/abc/pm-runtime/generations/g1")
    owned = classify_ownership(str(python))
    assert owned.role == "managed" and "install state" in owned.reason


def test_the_managed_python_store_is_hermes_owned(machine):
    python = _venv(machine / ".hermes/tools/python-3.14.9+20261021-darwin-arm64")
    assert classify_ownership(str(python)).role == "managed"


@pytest.mark.parametrize("name", ["venv", ".venv"])
def test_an_environment_directly_in_the_hermes_root_is_managed(machine, name):
    assert classify_ownership(str(_venv(machine / ".hermes" / name))).role == "managed"


def test_other_environments_below_the_hermes_root_stay_standalone(machine):
    python = _venv(machine / ".hermes/plugins/some-plugin/.venv")
    assert classify_ownership(str(python)).role == "standalone"


def test_a_hardlinked_or_symlinked_root_is_followed_by_identity(machine):
    real = machine / "real-install"
    _venv(real / "venv")
    (machine / ".hermes").mkdir()
    (machine / ".hermes" / "installs").symlink_to(real, target_is_directory=True)
    assert classify_ownership(str(real / "venv" / "bin" / "python")).role == "managed"
