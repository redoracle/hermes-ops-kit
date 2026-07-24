"""Black-box subprocess tests for the OpenAI-compat provider adapters.

Covers the validate_model allowlist gate, the missing-API-key guard, and the
models operation — the contract the bridge relies on. No network calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADAPTERS = ("fireworks_adapter.py", "deepinfra_adapter.py", "deepseek_adapter.py")


def _run(adapter: str, *args: str, env: dict | None = None) -> tuple[int, str, str]:
    e = os.environ.copy()
    e.pop("FIREWORKS_API_KEY", None)
    e.pop("DEEPINFRA_API_KEY", None)
    e.pop("DEEPSEEK_API_KEY", None)
    if env:
        e.update(env)
    r = subprocess.run(
        [sys.executable, str(ROOT / "providers" / adapter), *args],
        capture_output=True,
        text=True,
        env=e,
        timeout=30,
    )
    return r.returncode, r.stdout, r.stderr


def test_validate_model_rejects_unknown() -> None:
    for adapter in ADAPTERS:
        rc, _out, err = _run(adapter, "--operation", "models", "--model", "bogus/unknown-model")
        assert rc != 0, adapter
        assert "not in allowlist" in err, adapter


def test_missing_api_key_exits_with_hint() -> None:
    for adapter in ADAPTERS:
        rc, out, _err = _run(adapter, "--operation", "chat", "--prompt", "hi")
        assert rc != 0, adapter
        d = json.loads(out)  # error envelope on stdout
        assert d["ok"] is False
        assert "API_KEY not set" in d["error"], adapter


def test_models_op_works_without_key() -> None:
    for adapter in ADAPTERS:
        rc, out, _err = _run(adapter, "--operation", "models")
        assert rc == 0, (adapter, _err)
        d = json.loads(out)
        assert d["ok"] is True
        assert len(d["result"]["structured"]["models"]) > 0, adapter
        assert d["provider"] in ("fireworks", "deepinfra", "deepseek")


def test_deepseek_reasoner_hooks() -> None:
    # deepseek-reasoner rejects temperature and lacks JSON mode → the hooks
    # redirect extraction onto deepseek-v4-flash and suppress temperature.
    from providers.deepseek_adapter import DeepSeekAdapter  # pyright: ignore[reportMissingImports]

    assert DeepSeekAdapter.supports_temperature("deepseek-reasoner") is False
    assert DeepSeekAdapter.supports_temperature("deepseek-v4-flash") is True
    assert DeepSeekAdapter.extract_model("deepseek-reasoner") == "deepseek-v4-flash"
    assert DeepSeekAdapter.extract_model("deepseek-v4-flash") == "deepseek-v4-flash"
    reasoner_warn = DeepSeekAdapter.extract_warning("deepseek-reasoner")
    assert reasoner_warn is not None
    assert "deepseek-v4-flash" in reasoner_warn  # derives from extract_model (no drift)
    assert DeepSeekAdapter.extract_warning("deepseek-v4-flash") is None


def test_extract_redacts_structured_and_text(monkeypatch, capsys) -> None:
    # Pin the run_cli redaction of result.text AND result.structured — the
    # structured field is the same model output as text, so op_extract echoing
    # {"api_key":"sk-..."} must be redacted in both.
    import types as _types

    secret = "sk-test-1234567890abcdef"
    content = '{"api_key": "' + secret + '"}'
    mod = _types.ModuleType("openai")
    for n in ("AuthenticationError", "RateLimitError", "APITimeoutError",
              "APIConnectionError", "InternalServerError", "PermissionDeniedError"):
        setattr(mod, n, type(n, (Exception,), {}))

    _msg = _types.SimpleNamespace(content=content)
    _choice = _types.SimpleNamespace(message=_msg)
    _resp = _types.SimpleNamespace(choices=[_choice], usage=None, id="req-1")

    class _Completions:
        @staticmethod
        def create(**kw):
            return _resp

    class _Client:
        def __init__(self, **kw):
            pass

        models = _types.SimpleNamespace(data=["m"])  # not used by extract
        chat = _types.SimpleNamespace(completions=_Completions())

    mod.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
    monkeypatch.setattr(
        sys, "argv",
        ["deepseek_adapter.py", "--operation", "extract", "--prompt", "x",
         "--model", "deepseek-v4-flash", "--schema", '{"type":"object"}'],
    )
    from providers._openai_compat_ops import run_cli  # pyright: ignore[reportMissingImports]
    from providers.deepseek_adapter import DeepSeekAdapter  # pyright: ignore[reportMissingImports]

    run_cli(DeepSeekAdapter)
    out = capsys.readouterr().out
    assert secret not in out  # raw secret must not leak
    assert "REDACTED" in out  # redaction marker present (text + structured)
    assert json.loads(out)["ok"] is True


def test_adapter_rotator_shared_attrs_in_sync() -> None:
    # Drift detection: the base_url/provider attrs are duplicated across each
    # adapter and its rotator — they must stay in sync or validation and runtime
    # would hit different endpoints.
    from providers.fireworks_adapter import FireworksAdapter  # pyright: ignore[reportMissingImports]
    from providers.fireworks_rotator import FireworksRotator  # pyright: ignore[reportMissingImports]
    from providers.deepinfra_adapter import DeepInfraAdapter  # pyright: ignore[reportMissingImports]
    from providers.deepinfra_rotator import DeepInfraRotator  # pyright: ignore[reportMissingImports]
    from providers.deepseek_adapter import DeepSeekAdapter  # pyright: ignore[reportMissingImports]
    from providers.deepseek_rotator import DeepSeekRotator  # pyright: ignore[reportMissingImports]

    for adapter, rotator in [
        (FireworksAdapter, FireworksRotator),
        (DeepInfraAdapter, DeepInfraRotator),
        (DeepSeekAdapter, DeepSeekRotator),
    ]:
        assert adapter.base_url_default == rotator.base_url_default
        assert adapter.base_url_env == rotator.base_url_env
        assert adapter.provider == rotator.provider
        assert adapter.provider_label == rotator.provider_label


def test_extract_handles_none_api_content(monkeypatch, capsys) -> None:
    # op_extract must not crash when the API returns null content —
    # json.loads(None) raises TypeError (not JSONDecodeError); catching it keeps
    # the successful response (ok=True) with a parse_error structured field.
    import types as _types

    mod = _types.ModuleType("openai")
    for n in ("AuthenticationError", "RateLimitError", "APITimeoutError",
              "APIConnectionError", "InternalServerError", "PermissionDeniedError"):
        setattr(mod, n, type(n, (Exception,), {}))

    _msg = _types.SimpleNamespace(content=None)
    _choice = _types.SimpleNamespace(message=_msg)
    _resp = _types.SimpleNamespace(choices=[_choice], usage=None, id="req-1")

    class _Completions:
        @staticmethod
        def create(**kw):
            return _resp

    class _Client:
        def __init__(self, **kw):
            pass

        models = _types.SimpleNamespace(data=["m"])
        chat = _types.SimpleNamespace(completions=_Completions())

    mod.OpenAI = _Client
    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")
    monkeypatch.setattr(
        sys, "argv",
        ["deepseek_adapter.py", "--operation", "extract", "--prompt", "x",
         "--model", "deepseek-v4-flash", "--schema", '{"type":"object"}'],
    )
    from providers._openai_compat_ops import run_cli  # pyright: ignore[reportMissingImports]
    from providers.deepseek_adapter import DeepSeekAdapter  # pyright: ignore[reportMissingImports]

    run_cli(DeepSeekAdapter)
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] is True  # API succeeded — must not crash on null content
    assert d["result"]["structured"]["parse_error"] is True
