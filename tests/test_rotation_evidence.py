from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_ops_kit.providers.openai_rotator import OpenAIRotator
from hermes_ops_kit.providers.anthropic_rotator import AnthropicRotator
from hermes_ops_kit.providers.google_rotator import GoogleRotator
from hermes_ops_kit.providers.rotation_state_machine import RotationRunner
from hermes_ops_kit.security.secret_backend import ValidationResult


@pytest.mark.parametrize(
    "rotator_class", [OpenAIRotator, AnthropicRotator, GoogleRotator]
)
@pytest.mark.parametrize("provider_id", [None, "provider-old"])
def test_revocation_uses_captured_provider_identity(
    monkeypatch, rotator_class, provider_id
):
    state = {"value": "old-value", "id": provider_id}
    backend = Mock()
    backend.get_secret.side_effect = lambda ref: SimpleNamespace(value=state["value"])
    backend.get_metadata.side_effect = lambda ref: SimpleNamespace(
        item_id="vault-id", provider_credential_id=state["id"]
    )
    backend.backup_secret.return_value = SimpleNamespace(value="old-value", metadata={})

    def store(*args, **kwargs):
        state.update(value=args[1], id="provider-new")

    backend.set_secret.side_effect = store
    rotator = rotator_class(backend)
    monkeypatch.setattr(
        rotator, "validate_with_retry", lambda key: ValidationResult(True)
    )
    monkeypatch.setattr(rotator, "smoke_test", lambda: (True, "passed"))
    if hasattr(rotator, "_has_admin_key"):
        monkeypatch.setattr(rotator, "_has_admin_key", lambda: True)
    if rotator_class is AnthropicRotator:
        monkeypatch.setattr(rotator, "_create_api_key", lambda *args: None)
    delete = Mock(return_value=True)
    monkeypatch.setattr(
        rotator,
        "_archive_old_key" if rotator_class is AnthropicRotator else "_delete_old_key",
        delete,
    )
    monkeypatch.setattr(
        "hermes_ops_kit.env.render_env.render_env", lambda backend: "/synthetic/env"
    )
    audit = Mock()
    monkeypatch.setattr("hermes_ops_kit.audit.audit_log.audit_rotation_attempt", audit)
    if rotator_class is GoogleRotator:
        monkeypatch.setattr(
            rotator, "_create_api_key", lambda *args: ("new-value", "provider-new")
        )
    result = rotator.rotate(None if rotator_class is GoogleRotator else "new-value")
    assert result["ok"]
    assert audit.call_args.kwargs["old_revoked"] is bool(provider_id)
    assert audit.call_args.kwargs["manual_action"] is (not bool(provider_id))
    if provider_id:
        assert delete.call_args.args[-1] == provider_id
    else:
        delete.assert_not_called()


def test_resume_refuses_without_touching_backend():
    backend = Mock()
    result = RotationRunner(Mock(), backend, Mock()).execute()
    assert result["ok"] is False
    assert result["error_type"] == "UnsupportedOperation"
    assert not backend.mock_calls


def test_openai_delete_requires_confirmed_response(monkeypatch):
    backend = Mock()
    backend.get_secret.return_value = SimpleNamespace(value="project-id")
    delete = Mock(
        return_value=SimpleNamespace(status_code=200, json=lambda: {"deleted": False})
    )
    monkeypatch.setattr("requests.delete", delete)
    rotator = OpenAIRotator(backend)
    assert not rotator._delete_old_key("synthetic-admin", "provider-old")
    assert delete.call_args.args[0].endswith(
        "/organization/projects/project-id/api_keys/provider-old"
    )


def test_anthropic_archives_via_status_update(monkeypatch):
    post = Mock(
        return_value=SimpleNamespace(
            status_code=200, json=lambda: {"status": "archived"}
        )
    )
    monkeypatch.setattr("requests.post", post)
    assert AnthropicRotator(Mock())._archive_old_key("synthetic-admin", "provider-old")
    assert post.call_args.kwargs["json"] == {"status": "archived"}
    assert post.call_args.args[0].endswith("/organizations/api_keys/provider-old")
