"""Actual backend state semantics with a synthetic Bitwarden client."""

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest

from hermes_ops_kit.env.render_env import render_env_content, render_env
from hermes_ops_kit.security.vaultwarden_backend import (
    VaultwardenSecretBackend,
    internal_ref_to_item_name,
)

REF = "hermes/openai/api_key"


@pytest.fixture
def backend(monkeypatch):
    backend = object.__new__(VaultwardenSecretBackend)
    backend._items_cache = None
    items = [
        {
            "id": "vault-id",
            "name": internal_ref_to_item_name(REF),
            "notes": "old-value",
            "fields": [],
        }
    ]
    client = Mock()
    client.list_items.side_effect = lambda: copy.deepcopy(items)
    client.edit_item.side_effect = lambda ident, data: items.__setitem__(
        0, {**data, "id": ident}
    )
    client.create_item.side_effect = lambda data: (
        items.append({**data, "id": "created"}) or {"id": "created"}
    )
    backend._client = client
    for method in ("authenticate", "unlock", "sync"):
        monkeypatch.setattr(backend, method, lambda: None)
    return backend, items


def test_write_read_and_render_observe_new_value(backend):
    vault, _ = backend
    assert vault.get_secret(REF).value == "old-value"
    vault.set_secret(REF, "new-value")
    assert vault.get_secret(REF).value == "new-value"
    assert 'OPENAI_API_KEY="new-value"' in render_env_content(
        vault, projection={"OPENAI_API_KEY": REF}
    )


@pytest.mark.parametrize(
    "classification,permission",
    [
        ("admin", "false"),
        ("runtime", "false"),
        ("invalid", "true"),
        ("runtime", "invalid"),
    ],
)
def test_explicit_denial_survives_reads_and_updates(
    backend, classification, permission
):
    vault, items = backend
    items[0]["fields"] = [
        {"name": "secret_class", "value": classification},
        {"name": "renderable_to_env", "value": permission},
    ]
    assert not vault.get_metadata(REF).renderable_to_env
    vault.set_secret(REF, "replacement")
    assert not vault.get_metadata(REF).renderable_to_env
    assert 'OPENAI_API_KEY="' not in render_env_content(
        vault, projection={"OPENAI_API_KEY": REF}
    )


def test_rollback_preserves_classification_and_provider_identity(backend):
    vault, _ = backend
    vault.set_secret(
        REF,
        "old-value",
        {"renderable_to_env": False, "provider_credential_id": "provider-old"},
    )
    backup = vault.backup_secret(REF)
    vault.set_secret(REF, "new-value", {"provider_credential_id": "provider-new"})
    vault.restore_secret(REF, backup)
    meta = vault.get_metadata(REF)
    assert vault.get_secret(REF).value == "old-value"
    assert meta.provider_credential_id == "provider-old"
    assert meta.item_id == "vault-id"
    assert not meta.renderable_to_env


def test_failed_write_does_not_replace_secret(backend):
    vault, _ = backend
    vault._client.edit_item.side_effect = RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError):
        vault.set_secret(REF, "new-value")
    assert vault.get_secret(REF).value == "old-value"


def test_replacing_value_cannot_inherit_old_provider_identity(backend):
    vault, _ = backend
    vault.set_secret(
        REF,
        "old-value",
        {"provider_credential_id": "old-provider", "renderable_to_env": False},
    )
    vault.set_secret(REF, "replacement", {"rotation_mode": "emergency"})
    meta = vault.get_metadata(REF)
    assert meta.provider_credential_id is None
    assert not meta.renderable_to_env


def test_delayed_cache_reader_cannot_reinstall_pre_write_snapshot(backend):
    vault, items = backend
    captured, release, written = Event(), Event(), Event()
    first = True

    def list_items():
        nonlocal first
        snapshot = copy.deepcopy(items)
        if first:
            first = False
            captured.set()
            assert release.wait(3)
        return snapshot

    vault._client.list_items.side_effect = list_items

    def write():
        vault.set_secret(REF, "replacement")
        written.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(vault.get_secret, REF)
        try:
            assert captured.wait(3)
            writer = pool.submit(write)
            assert not written.wait(0.05)
        finally:
            release.set()
        reader.result()
        writer.result()
    assert vault.get_secret(REF).value == "replacement"


def test_cached_second_instance_cannot_publish_preceding_snapshot(backend, tmp_path):
    writer, _ = backend
    renderer = object.__new__(VaultwardenSecretBackend)
    renderer._items_cache = None
    renderer._client = writer._client
    renderer.authenticate = lambda: None
    renderer.unlock = lambda: None
    renderer.sync = lambda: None
    assert renderer.get_secret(REF).value == "old-value"
    writer.set_secret(REF, "replacement")
    target = tmp_path / "env"
    render_env(renderer, str(target), {"OPENAI_API_KEY": REF})
    assert 'OPENAI_API_KEY="replacement"' in target.read_text()
