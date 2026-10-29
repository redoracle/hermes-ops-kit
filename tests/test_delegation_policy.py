import json
from unittest.mock import MagicMock

import pytest

from hermes_ops_kit.assistants.base import AssistantConfig, AssistantTask
from hermes_ops_kit.assistants.client import AssistantClient, AssistantClientError
from hermes_ops_kit.assistants.policy import (
    PolicyViolation,
    check_task,
    effective_constraints,
    serialize_task,
)


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


RESERVED = [
    "no_secret_access",
    "no_env_dump",
    "no_file_write",
    "no_shell_execution",
    "no_network_scan",
]
NOT_BOOLEAN = [0, 1, None, "false", "true", "", [], {}, "no"]
# Reserved flags a config can ever allow a caller to relax, and the field that does so.
RELAXABLE = {
    "no_secret_access": "allow_secret_prompts",
    "no_file_write": "allow_file_mutation",
    "no_shell_execution": "allow_shell_execution",
}


def transmitted(source, cfg):
    return json.loads(serialize_task(source, cfg))["constraints"]


@pytest.mark.parametrize("flag", RESERVED)
@pytest.mark.parametrize("value", NOT_BOOLEAN)
def test_reserved_flag_must_be_exact_boolean(flag, value):
    cfg, source = config(), task(constraints={flag: value})
    allowed, reasons = check_task(source, cfg)
    assert not allowed
    assert any(flag in reason and "boolean" in reason for reason in reasons)
    # The bad value never reaches the remote worker; the restriction stays in force.
    assert transmitted(source, cfg)[flag] is True


@pytest.mark.parametrize("flag", RESERVED)
def test_caller_cannot_relax_a_configured_restriction(flag):
    cfg, source = config(), task(constraints={flag: False})
    allowed, _ = check_task(source, cfg)
    assert not allowed
    assert transmitted(source, cfg)[flag] is True


@pytest.mark.parametrize("flag", ["no_env_dump", "no_network_scan"])
def test_unconfigurable_flags_can_never_be_relaxed(flag):
    cfg = config(
        allow_secret_prompts=True, allow_file_mutation=True, allow_shell_execution=True
    )
    allowed, _ = check_task(task(constraints={flag: False}), cfg)
    assert not allowed


@pytest.mark.parametrize("flag,field", RELAXABLE.items())
def test_config_permission_allows_relaxing_only_that_flag(flag, field):
    cfg = config(**{field: True})
    source = task(constraints={flag: False})
    allowed, reasons = check_task(source, cfg)
    assert allowed, reasons
    assert transmitted(source, cfg)[flag] is False


@pytest.mark.parametrize("flag,field", RELAXABLE.items())
def test_caller_may_tighten_a_permitted_flag(flag, field):
    cfg = config(**{field: True})
    source = task(constraints={flag: True})
    allowed, reasons = check_task(source, cfg)
    assert allowed, reasons
    assert transmitted(source, cfg)[flag] is True


@pytest.mark.parametrize(
    "alias", ["No_Shell_Execution", " no_shell_execution", "no-shell-execution"]
)
def test_alias_spellings_of_reserved_flags_are_rejected_and_dropped(alias):
    cfg, source = config(), task(constraints={alias: False})
    allowed, reasons = check_task(source, cfg)
    assert not allowed
    assert any("canonical name" in reason for reason in reasons)
    assert alias not in transmitted(source, cfg)


def test_non_reserved_constraints_pass_through():
    cfg, source = (
        config(),
        task(constraints={"timeout_seconds": 10, "patch_only": True}),
    )
    assert check_task(source, cfg)[0]
    sent = transmitted(source, cfg)
    assert sent["timeout_seconds"] == 10 and sent["patch_only"] is True


@pytest.mark.parametrize("bad", ["no_shell_execution", ["x"], 5])
def test_constraints_must_be_an_object(bad):
    allowed, reasons = check_task(task(constraints=bad), config())
    assert not allowed
    assert any("object" in reason for reason in reasons)


@pytest.mark.parametrize(
    "constraints",
    [{}, {"no_file_write": True}, {"timeout_seconds": 5, "no_secret_access": True}],
)
def test_evaluated_constraints_equal_transmitted_constraints(constraints):
    cfg, source = config(), task(constraints=constraints)
    evaluated, violations = effective_constraints(source, cfg)
    assert not violations
    assert transmitted(source, cfg) == evaluated
    assert AssistantClient(cfg)._build_task_envelope(source) == serialize_task(
        source, cfg
    )


def test_rejected_task_never_reaches_transport(monkeypatch):
    client = AssistantClient(config())
    transport = MagicMock()
    monkeypatch.setattr(client, "_open", transport)
    with pytest.raises(PolicyViolation):
        client.delegate(task(constraints={"no_shell_execution": 0}))
    transport.assert_not_called()


@pytest.mark.parametrize(
    "alias",
    [
        "no\u200b_shell_execution",  # zero-width space
        "no_shell_execution\u200d",  # zero-width joiner
        "\uff4e\uff4f_shell_execution",  # fullwidth "no"
        "NO_SHELL_EXECUTION",
    ],
)
def test_lookalike_spellings_of_reserved_flags_are_rejected(alias):
    cfg, source = config(), task(constraints={alias: False})
    allowed, reasons = check_task(source, cfg)
    assert not allowed and any("canonical name" in r for r in reasons)
    assert alias not in transmitted(source, cfg)
