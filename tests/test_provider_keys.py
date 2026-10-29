"""One lookup rule for `providers:` / `custom_providers:` blocks, shared by every caller."""

from __future__ import annotations

import pytest
import yaml

from hermes_ops_kit import commands, route_runtime_harness as harness, route_verifier
from hermes_ops_kit.provider_keys import (
    find_provider_block,
    key_env_of,
    provider_key_env,
)

CUSTOM = {
    "custom_providers": [
        {"name": "My Prov", "base_url": "http://x/v1", "key_env": "MY_KEY"}
    ]
}


@pytest.mark.parametrize(
    "requested", ["custom:my-prov", "my-prov", "My Prov", "MY-PROV"]
)
def test_every_identity_core_accepts_finds_the_entry(requested):
    assert provider_key_env(CUSTOM, requested) == "MY_KEY"


@pytest.mark.parametrize("requested", ["", "custom:other", "my", "custom:"])
def test_unknown_identities_find_nothing(requested):
    assert provider_key_env(CUSTOM, requested) == ""


def test_both_key_spellings_and_a_providers_block_are_read():
    cfg = {"providers": {"headroom": {"api_key_env": "H_KEY"}}}
    assert provider_key_env(cfg, "headroom") == "H_KEY"
    assert key_env_of({"key_env": "A", "api_key_env": "B"}) == "A"
    assert key_env_of({"key_env": " ", "api_key_env": "B"}) == "B"


def test_a_key_lookup_does_not_need_an_endpoint_but_the_overlay_does():
    cfg = {"providers": {"x": {"key_env": "K"}}}
    assert find_provider_block(cfg, "x") == {"key_env": "K"}
    assert find_provider_block(cfg, "x", need_endpoint=True) == {}


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        [],
        {"providers": [1], "custom_providers": "no"},
        {"custom_providers": [None, 3]},
    ],
)
def test_malformed_config_is_not_a_crash(cfg):
    assert find_provider_block(cfg, "custom:x") == {}


# ── callers ──────────────────────────────────────────────────────────────────


def _write(tmp_path, monkeypatch, cfg):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(commands.ops_config_io, "hermes_config", lambda: str(path))
    return path


def _doctor(capsys, env):
    import hermes_ops_kit.commands as c

    c._load_hermes_env = lambda: env
    c._handle_doctor()
    return capsys.readouterr().out


def test_doctor_finds_the_headroom_overlays_key(tmp_path, monkeypatch, capsys):
    cfg = {
        "model": {"provider": "headroom", "default": "m"},
        "providers": {
            "headroom": {"base_url": "http://127.0.0.1:8790/v1", "api_key_env": "H_KEY"}
        },
    }
    _write(tmp_path, monkeypatch, cfg)
    monkeypatch.setattr(commands, "_load_hermes_env", lambda: {"H_KEY": "x"})
    commands._handle_doctor()
    out = capsys.readouterr().out
    assert "H_KEY set" in out and "no credential found" not in out


def test_doctor_still_flags_a_missing_overlay_key(tmp_path, monkeypatch, capsys):
    cfg = {
        "model": {"provider": "headroom"},
        "providers": {"headroom": {"api_key_env": "H_KEY"}},
    }
    _write(tmp_path, monkeypatch, cfg)
    monkeypatch.setattr(commands, "_load_hermes_env", lambda: {})
    commands._handle_doctor()
    assert "no credential for headroom" in capsys.readouterr().out


def test_doctor_matches_a_custom_name_the_way_core_does(tmp_path, monkeypatch, capsys):
    cfg = dict(CUSTOM, model={"provider": "custom:my-prov"})
    _write(tmp_path, monkeypatch, cfg)
    monkeypatch.setattr(commands, "_load_hermes_env", lambda: {"MY_KEY": "x"})
    commands._handle_doctor()
    out = capsys.readouterr().out
    assert "MY_KEY set" in out and "no credential for custom:my-prov" not in out


def test_credential_gap_check_matches_a_custom_name_the_way_core_does(
    tmp_path, monkeypatch
):
    cfg = dict(
        CUSTOM,
        auxiliary={"vision": {"provider": "custom:my-prov", "model": "m"}},
    )
    path = _write(tmp_path, monkeypatch, cfg)
    monkeypatch.setattr(route_verifier, "_load_env", lambda: {"MY_KEY": "x"})

    def gaps():  # the other aux slots are 'auto' and always reported
        found = route_verifier.check_credential_gaps(str(path))
        return [g["provider"] for g in found if g["provider"] != "auto"]

    assert gaps() == []
    monkeypatch.setattr(route_verifier, "_load_env", lambda: {})
    assert gaps() == ["custom:my-prov"]


def test_harness_registers_every_identity_of_a_custom_provider(monkeypatch):
    monkeypatch.setenv("MY_KEY", "x")
    monkeypatch.setattr(
        "hermes_ops_kit.image_routes.adapters.base.load_dotenv", lambda *a, **k: None
    )
    results = harness._all_online_results(CUSTOM)
    assert {"custom:My Prov", "custom:my-prov"} <= set(results)
    monkeypatch.delenv("MY_KEY")
    assert "custom:my-prov" not in harness._all_online_results(CUSTOM)
