"""Tests for the OpenAI-compat rotators — class attrs + shared validate ladder.

The validate_new_key exception → ValidationReason ladder is the highest-bug-surface
code (exception ordering matters); these tests exercise it via a fake openai SDK
so no network is needed.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.fireworks_rotator import FireworksRotator  # pyright: ignore[reportMissingImports]
from providers.deepinfra_rotator import DeepInfraRotator  # pyright: ignore[reportMissingImports]
from providers.deepseek_rotator import DeepSeekRotator  # pyright: ignore[reportMissingImports]
from security.secret_backend import ValidationReason, ValidationResult  # pyright: ignore[reportMissingImports]

_ERR_NAMES = (
    "AuthenticationError",
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "PermissionDeniedError",
)


def _make_fake_openai(raise_exc_cls: str | None = None):
    mod = types.ModuleType("openai")
    errs = {n: type(n, (Exception,), {}) for n in _ERR_NAMES}
    for n, cls in errs.items():
        setattr(mod, n, cls)

    class _Client:
        def __init__(self, **kw):
            pass

        class models:
            @staticmethod
            def list():
                if raise_exc_cls:
                    raise errs[raise_exc_cls]()
                return types.SimpleNamespace(data=["m1"])

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    if raise_exc_cls:
                        raise errs[raise_exc_cls]()
                    return types.SimpleNamespace(choices=[object()])

    mod.OpenAI = _Client
    return mod


def test_rotator_class_attrs() -> None:
    assert FireworksRotator.provider == "fireworks"
    assert FireworksRotator.api_ref == "hermes/fireworks/api_key"
    assert FireworksRotator.env_key == "FIREWORKS_API_KEY"
    assert FireworksRotator.chat_model == "accounts/fireworks/models/glm-5p2"
    assert DeepInfraRotator.env_key == "DEEPINFRA_API_KEY"
    assert DeepSeekRotator.env_key == "DEEPSEEK_API_KEY"
    assert DeepSeekRotator.chat_model == "deepseek-v4-flash"


def test_validate_new_key_success(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", _make_fake_openai())
    r = FireworksRotator(object())  # backend unused by validate_new_key
    vr = r.validate_new_key("any-key")
    assert vr.valid is True


def test_validate_new_key_auth_denied(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", _make_fake_openai("AuthenticationError"))
    r = FireworksRotator(object())
    vr = r.validate_new_key("bad-key")
    assert vr.valid is False
    assert vr.reason_class == ValidationReason.AUTH_DENIED
    assert vr.http_status == 401


def test_validate_new_key_rate_limited(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", _make_fake_openai("RateLimitError"))
    r = DeepInfraRotator(object())
    vr = r.validate_new_key("k")
    assert vr.reason_class == ValidationReason.RATE_LIMITED
    assert vr.retry_recommended is True


def test_validate_new_key_quota_or_billing(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", _make_fake_openai("PermissionDeniedError"))
    r = DeepSeekRotator(object())
    vr = r.validate_new_key("k")
    assert vr.reason_class == ValidationReason.QUOTA_OR_BILLING
    assert vr.retry_recommended is False


def test_validate_new_key_sdk_unavailable(monkeypatch) -> None:
    # No fake openai installed → import fails → SDK_UNAVAILABLE (no retry).
    monkeypatch.setitem(sys.modules, "openai", None)
    r = FireworksRotator(object())
    vr = r.validate_new_key("k")
    assert vr.valid is False
    assert vr.reason_class == ValidationReason.SDK_UNAVAILABLE


# ── rotate() branch tests (fake backend, mocked deps) ─────────────────


class _FakeBackend:
    """Records set/backup/restore calls for rotate() tests."""

    def __init__(self, secret: str = "old-key"):
        self.secret = secret
        self.calls: list[tuple] = []

    def get_secret(self, name):
        self.calls.append(("get", name))
        return types.SimpleNamespace(value=self.secret)

    def get_metadata(self, name):
        return types.SimpleNamespace(renderable_to_env=True, secret_class="runtime")

    def backup_secret(self, name):
        self.calls.append(("backup", name))
        return types.SimpleNamespace(value=self.secret)

    def set_secret(self, name, value, metadata=None):
        self.calls.append(("set", name))
        self.secret = value
        return types.SimpleNamespace()

    def restore_secret(self, name, previous):
        self.calls.append(("restore", name))
        return types.SimpleNamespace()


def _silence_audit(monkeypatch) -> None:
    import audit.audit_log as al  # pyright: ignore[reportMissingImports]

    monkeypatch.setattr(al, "audit_rotation_attempt", lambda **kw: None)


def test_rotate_smoke_failure_rolls_back(monkeypatch) -> None:
    backend = _FakeBackend()
    r = FireworksRotator(backend)
    _silence_audit(monkeypatch)
    monkeypatch.setattr(r, "validate_with_retry", lambda key: ValidationResult(valid=True))
    monkeypatch.setattr(r, "smoke_test", lambda: (False, "smoke failed"))
    result = r.rotate("new-key")
    assert result["ok"] is False
    assert ("set", r.api_ref) in backend.calls  # stored before smoke
    assert ("restore", r.api_ref) in backend.calls  # rolled back after smoke fail


def test_rotate_render_failure_rolls_back(monkeypatch) -> None:
    backend = _FakeBackend()
    r = DeepInfraRotator(backend)
    _silence_audit(monkeypatch)
    monkeypatch.setattr(r, "validate_with_retry", lambda key: ValidationResult(valid=True))
    monkeypatch.setattr(r, "smoke_test", lambda: (True, "ok"))

    def _render_fail(backend):
        raise RuntimeError("render boom")

    import env.render_env as re_mod  # pyright: ignore[reportMissingImports]
    monkeypatch.setattr(re_mod, "render_env", _render_fail)
    result = r.rotate("new-key")
    assert result["ok"] is False
    assert ("restore", r.api_ref) in backend.calls  # rolled back after render fail


def test_rotate_quota_or_billing_stores_with_warning(monkeypatch) -> None:
    backend = _FakeBackend()
    r = DeepSeekRotator(backend)
    _silence_audit(monkeypatch)
    monkeypatch.setattr(
        r, "validate_with_retry",
        lambda key: ValidationResult(valid=False, reason_class=ValidationReason.QUOTA_OR_BILLING, detail="no credits"),
    )
    monkeypatch.setattr(r, "smoke_test", lambda: (True, "ok"))
    import env.render_env as re_mod  # pyright: ignore[reportMissingImports]
    monkeypatch.setattr(re_mod, "render_env", lambda backend: "/fake/env-path")
    result = r.rotate("new-key")
    assert result["ok"] is True  # stored despite QUOTA_OR_BILLING
    assert result["warnings"]  # billing warning present
    assert ("set", r.api_ref) in backend.calls
