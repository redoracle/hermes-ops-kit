from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hermes_ops_kit import hermes_key_rotate as cli
from hermes_ops_kit.env.render_env import render_env
from hermes_ops_kit.providers.fireworks_rotator import FireworksRotator
from hermes_ops_kit.providers.deepinfra_rotator import DeepInfraRotator
from hermes_ops_kit.security.secret_backend import SecretValue, ValidationResult


@pytest.mark.parametrize("smoke_passes", [True, False])
def test_candidate_is_not_published_by_another_operation(
    tmp_path, monkeypatch, smoke_passes
):
    values = {"hermes/fireworks/api_key": "old-a", "hermes/deepinfra/api_key": "old-b"}
    backend = Mock()
    backend.get_secret.side_effect = lambda ref: (
        SecretValue(ref, values[ref]) if ref in values else None
    )
    backend.get_metadata.return_value = SimpleNamespace(renderable_to_env=True)
    backend.backup_secret.side_effect = backend.get_secret.side_effect
    backend.set_secret.side_effect = lambda ref, value, **kwargs: values.__setitem__(
        ref, value
    )
    backend.restore_secret.side_effect = lambda ref, secret: values.__setitem__(
        ref, secret.value
    )
    monkeypatch.setattr(
        "hermes_ops_kit.audit.audit_log.audit_rotation_attempt", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "hermes_ops_kit.security.lockfile.LOCK_DIR", str(tmp_path / "locks")
    )
    monkeypatch.setattr("hermes_ops_kit.ops_config_io.HERMES_HOME", str(tmp_path))
    staged, release, validated_b = Event(), Event(), Event()
    a, b = FireworksRotator(backend), DeepInfraRotator(backend)
    monkeypatch.setattr(a, "validate_with_retry", lambda key: ValidationResult(True))

    def validate_b(key):
        validated_b.set()
        return ValidationResult(True)

    monkeypatch.setattr(b, "validate_with_retry", validate_b)

    def smoke_a():
        assert values[a.api_ref] == "candidate-a"
        staged.set()
        assert release.wait(3)
        return smoke_passes, "synthetic"

    monkeypatch.setattr(a, "smoke_test", smoke_a)
    monkeypatch.setattr(b, "smoke_test", lambda: (True, "synthetic"))
    published = Event()

    def snapshot():
        result = render_env(backend)
        published.set()
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        fa = pool.submit(a.rotate, "candidate-a")
        try:
            assert staged.wait(3)
            fb = pool.submit(b.rotate, "candidate-b")
            fr = pool.submit(snapshot)
            assert validated_b.wait(3), "validation should remain concurrent"
            assert not published.wait(0.05), "render crossed an untested candidate"
        finally:
            release.set()
        assert fa.result()["ok"] is smoke_passes
        assert fb.result()["ok"]
        fr.result()
    content = (tmp_path / ".env.generated").read_text()
    assert 'DEEPINFRA_API_KEY="candidate-b"' in content
    assert ('FIREWORKS_API_KEY="candidate-a"' in content) is smoke_passes
    if not smoke_passes:
        assert 'FIREWORKS_API_KEY="old-a"' in content


def test_parallel_cli_does_not_reacquire_provider_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_ops_kit.security.lockfile.LOCK_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "PROVIDER_ROTATORS", {"a": object(), "b": object()})
    rotator = Mock()
    rotator.rotate.return_value = {"ok": True}
    monkeypatch.setattr(cli, "_get_rotator", lambda *args: rotator)
    assert cli.cmd_rotate_all_parallel(Mock(), max_parallel=2)["ok"]
    assert rotator.rotate.call_count == 2
    assert not cli.cmd_rotate_all_parallel(Mock(), max_parallel=0)["ok"]


def test_rollback_preserves_intervening_administrative_commit(tmp_path, monkeypatch):
    from hermes_ops_kit.security.lockfile import credential_commit_lock

    state = {"value": "initial"}
    backend = Mock()
    backend.get_secret.side_effect = lambda ref: SecretValue(ref, state["value"])
    backend.backup_secret.side_effect = backend.get_secret.side_effect
    backend.set_secret.side_effect = lambda ref, value, **kwargs: state.update(
        value=value
    )
    backend.restore_secret.side_effect = lambda ref, previous: state.update(
        value=previous.value
    )
    rotator = FireworksRotator(backend)
    monkeypatch.setattr("hermes_ops_kit.security.lockfile.LOCK_DIR", str(tmp_path))
    monkeypatch.setattr(
        "hermes_ops_kit.audit.audit_log.audit_rotation_attempt", lambda **kwargs: None
    )

    def validate(key):
        with credential_commit_lock():
            state["value"] = "administrative-commit"
        return ValidationResult(True)

    monkeypatch.setattr(rotator, "validate_with_retry", validate)
    monkeypatch.setattr(rotator, "smoke_test", lambda: (False, "synthetic failure"))
    assert not rotator.rotate("candidate")["ok"]
    assert state["value"] == "administrative-commit"


def test_cli_forwards_max_parallel(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "hermes-key-rotate",
            "rotate",
            "--provider",
            "all",
            "--parallel",
            "--max-parallel",
            "2",
        ],
    )
    monkeypatch.setattr(cli, "load_hermes_env", lambda: {})
    monkeypatch.setattr(cli, "VaultwardenSecretBackend", lambda **kwargs: Mock())
    rotate = Mock(return_value={"ok": True})
    monkeypatch.setattr(cli, "cmd_rotate_all_parallel", rotate)
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 0
    assert rotate.call_args.kwargs["max_parallel"] == 2


def test_resume_refuses_before_bootstrap_or_vault_access(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["hermes-key-rotate", "resume", "--provider", "openai"]
    )
    load = Mock(side_effect=AssertionError("bootstrap must not run"))
    monkeypatch.setattr(cli, "load_hermes_env", load)
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 1
    assert "UnsupportedOperation" in capsys.readouterr().out
    load.assert_not_called()
