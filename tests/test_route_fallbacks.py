"""`route-manager fallback add/remove`: no duplicate entries, honest removal counts."""

from __future__ import annotations

import argparse

import pytest

from hermes_ops_kit import hermes_route_manager as rm
from hermes_ops_kit import ops_config_io


@pytest.fixture()
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("model:\n  provider: openai\n")
    return tmp_path / "config.yaml"


def _fallbacks():
    return rm._load_yaml(rm.hermes_config()).get("fallback_providers", [])


def _run(action, provider=None, model=None):
    rm.cmd_fallback(
        argparse.Namespace(fb_action=action, provider=provider, model=model)
    )


def test_adding_the_same_fallback_twice_keeps_one_entry(config, capsys):
    _run("add", "openai", "gpt-5.4-mini")
    _run("add", "openai", "gpt-5.4-mini")
    assert _fallbacks() == [{"provider": "openai", "model": "gpt-5.4-mini"}]
    assert "Already a fallback" in capsys.readouterr().out


def test_same_provider_with_another_model_is_a_distinct_fallback(config):
    _run("add", "openai", "gpt-5.4-mini")
    _run("add", "openai", "gpt-5.4")
    assert len(_fallbacks()) == 2


def test_remove_reports_how_many_entries_went(config, capsys):
    _run("add", "openai", "a")
    _run("add", "openai", "b")
    _run("add", "anthropic", "c")
    capsys.readouterr()
    _run("remove", "openai")
    assert "Removed 2 fallback(s)" in capsys.readouterr().out
    assert [f["provider"] for f in _fallbacks()] == ["anthropic"]


def test_removing_an_unknown_provider_says_so(config, capsys):
    _run("remove", "nobody")
    assert "No fallback for provider: nobody" in capsys.readouterr().out
