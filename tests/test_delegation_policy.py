import json
from unittest.mock import MagicMock

import pytest

from hermes_ops_kit.assistants.base import AssistantConfig, AssistantTask
from hermes_ops_kit.assistants.client import AssistantClient, AssistantClientError
from hermes_ops_kit.assistants.policy import PolicyViolation, check_task


def config(**kwargs):
    return AssistantConfig(
        id="test",
        display_name="Test",
        type="remote_hermes",
        role="remote_worker",
        capabilities=[{"id": "review"}],
        **kwargs,
    )


def task(**kwargs):
    return AssistantTask("test", "test", "review", "Review this", **kwargs)


@pytest.mark.parametrize("field", ["context", "constraints"])
def test_nested_secrets_block_before_transport(monkeypatch, field):
    client = AssistantClient(config())
    transport = MagicMock()
    monkeypatch.setattr(client, "_open", transport)
    with pytest.raises(PolicyViolation):
        client.delegate(task(**{field: {"nested": ["sk-" + "X" * 40]}}))
    transport.assert_not_called()


def test_context_counts_toward_envelope_limit():
    allowed, reasons = check_task(
        task(context={"text": "X" * 500}), config(max_prompt_bytes=400)
    )
    assert not allowed
    assert any("envelope size" in reason for reason in reasons)


@pytest.mark.parametrize(
    "url,token",
    [("http://example.invalid/v1", "synthetic"), ("https://example.invalid/v1", "")],
)
def test_transport_policy_blocks_before_request(monkeypatch, url, token):
    client = AssistantClient(config(require_tls=True))
    client.base_url, client.api_key = url, token
    transport = MagicMock()
    monkeypatch.setattr(client, "_open", transport)
    with pytest.raises(AssistantClientError):
        client.delegate(task())
    transport.assert_not_called()


def test_valid_payload_is_the_validated_envelope(monkeypatch):
    client = AssistantClient(config(require_tls=True))
    client.base_url, client.api_key = "https://example.invalid/v1", "synthetic"
    response = MagicMock()
    response.read.return_value = b'{"choices":[{"message":{"content":"reviewed"}}]}'
    transport = MagicMock()
    transport.return_value.__enter__.return_value = response
    monkeypatch.setattr(client, "_open", transport)
    source = task(context={"repo": "example"}, constraints={"timeout_seconds": 10})
    result = client.delegate(source)
    assert result.ok
    payload = json.loads(transport.call_args.args[0].data)
    assert payload["messages"][1]["content"] == client._build_task_envelope(source)
    assert (
        json.loads(payload["messages"][1]["content"])["constraints"]["no_file_write"]
        is True
    )


def test_response_limit_is_enforced():
    client = AssistantClient(config(max_response_bytes=10))
    response = MagicMock()
    response.read.return_value = b"X" * 11
    with pytest.raises(AssistantClientError):
        client._read_response(response)
    response.read.assert_called_once_with(11)


@pytest.mark.parametrize(
    "name", ["BW_SESSION", "VAULTWARDEN_PASSWORD", "OPENAI_API_KEY"]
)
def test_opaque_bootstrap_values_in_context_are_blocked(name):
    allowed, _ = check_task(task(context={name: "synthetic-opaque-value"}), config())
    assert not allowed
