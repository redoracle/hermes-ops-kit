"""Embedded in Hermes, credentials are read per profile and os.environ is never written (S5.2)."""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path

import pytest

from hermes_ops_kit import provider_catalog
from hermes_ops_kit.env import env_loader, loader

PACKAGE = Path(__file__).resolve().parents[1] / "hermes_ops_kit"
LAUNCH_SECRET = "launch-profile-secret"


def _write_env(home: Path, body: str, name: str = ".env") -> None:
    (home / name).write_text(body)


@pytest.fixture
def launch_env(monkeypatch):
    """Credentials present only in the launch process environment."""
    for name in (
        "OPENAI_API_KEY",
        "FAL_KEY",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
    ):
        monkeypatch.setenv(name, LAUNCH_SECRET)


# --- the loaders never write the shared process environment when embedded ----------


def test_embedded_loaders_do_not_write_the_process_environment(fake_core, profiles):
    home = profiles.use("a")
    _write_env(home, "EMBEDDED_ONLY_KEY=1\n")
    _write_env(home, "EMBEDDED_GEN_KEY=2\n", ".env.generated")
    before = dict(os.environ)

    loader._ENV_LOADED = False
    try:
        loader.load_dotenv()
    finally:
        loader._ENV_LOADED = True
    loader.load_env_setdefault()
    env_loader.load_dotenv(str(home / ".env"), set_environ=True)
    merged = env_loader.load_hermes_env()

    assert dict(os.environ) == before
    assert merged["EMBEDDED_ONLY_KEY"] == "1"  # the dict is still returned


def test_standalone_loaders_still_populate_the_environment(tmp_path, monkeypatch):
    import sys

    from hermes_ops_kit import ops_config_io

    monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("STANDALONE_KEY", raising=False)
    _write_env(tmp_path, "STANDALONE_KEY=1\n")
    loader.load_env_setdefault()
    assert os.environ["STANDALONE_KEY"] == "1"
    monkeypatch.delenv("STANDALONE_KEY")


# --- shared credential lookups are per profile -------------------------------------


def test_provider_key_and_has_credential_follow_the_active_profile(
    fake_core, profiles, launch_env
):
    _write_env(profiles.use("a"), "OPENAI_API_KEY=key-a\n")
    profiles.use("b")
    assert provider_catalog.provider_key("openai") is None  # not the launch secret
    assert not provider_catalog.has_credential("openai")
    profiles.use("a")
    assert provider_catalog.provider_key("openai") == "key-a"
    assert provider_catalog.first_available_key("openai") == "OPENAI_API_KEY"


def test_explicit_env_vars_argument_still_wins_over_the_scoped_read(
    fake_core, profiles
):
    _write_env(profiles.use("a"), "OPENAI_API_KEY=key-a\n")
    assert (
        provider_catalog.provider_key("openai", {"OPENAI_API_KEY": "given"}) == "given"
    )
    assert provider_catalog.provider_key("openai", {}) is None


def test_an_unscoped_credential_read_under_multiplexing_raises(fake_core, profiles):
    profiles.use("a")
    fake_core.multiplex = True
    with pytest.raises(fake_core.UnscopedSecretError):
        provider_catalog.provider_key("openai")


# --- image adapters ------------------------------------------------------------------


def test_adapter_availability_follows_the_profile_not_the_launch_environment(
    fake_core, profiles, launch_env
):
    from hermes_ops_kit.image_routes.adapters.cloudflare_image import (
        CloudflareImageAdapter,
    )
    from hermes_ops_kit.image_routes.adapters.custom_image import CustomImageAdapter
    from hermes_ops_kit.image_routes.adapters.fal_image import FALImageAdapter

    _write_env(
        profiles.use("a"),
        "FAL_KEY=fal-a\nCLOUDFLARE_API_TOKEN=t\nCLOUDFLARE_ACCOUNT_ID=acc\nCUSTOM_KEY=c\n",
    )
    custom = CustomImageAdapter.__new__(CustomImageAdapter)
    custom._key_env, custom._base_url = "CUSTOM_KEY", "https://example.invalid"

    assert FALImageAdapter().is_available()
    assert CloudflareImageAdapter().is_available()
    assert custom.is_available()

    profiles.use("b")  # no files; launch environment holds FAL_KEY/CLOUDFLARE_* only
    assert not FALImageAdapter().is_available()
    assert not CloudflareImageAdapter().is_available()
    assert not custom.is_available()


def test_openai_generate_reports_a_missing_key_instead_of_using_the_launch_key(
    fake_core, profiles, launch_env
):
    pytest.importorskip("openai")
    from hermes_ops_kit.image_routes.adapters.openai_image import OpenAIImageAdapter

    profiles.use("b")
    result = OpenAIImageAdapter().generate("a cat")
    assert result["ok"] is False and result["error_type"] == "MissingAPIKey"


def test_adapters_do_not_swallow_a_scope_refusal(fake_core, profiles):
    from hermes_ops_kit.image_routes.adapters.fal_image import FALImageAdapter

    profiles.use("a")
    fake_core.multiplex = True
    with pytest.raises(fake_core.UnscopedSecretError):
        FALImageAdapter().is_available()


def test_gemini_notice_filter_hides_the_dual_key_message_and_cleans_up(caplog):
    from hermes_ops_kit.image_routes.adapters.gemini_image import _quiet_dual_key_notice

    sdk_logger = logging.getLogger("google_genai")
    before = list(sdk_logger.filters) + list(logging.getLogger().filters)
    notice = "Both GOOGLE_API_KEY and GEMINI_API_KEY are set. Using GOOGLE_API_KEY."
    with caplog.at_level(logging.INFO):
        with _quiet_dual_key_notice():
            sdk_logger.info(notice)
            sdk_logger.info("a different message")
        sdk_logger.info(notice)
    messages = [r.getMessage() for r in caplog.records]
    assert messages.count(notice) == 1  # only the one logged outside the filter
    assert "a different message" in messages
    assert list(sdk_logger.filters) + list(logging.getLogger().filters) == before


# --- assistant client ------------------------------------------------------------------


def test_assistant_client_reads_its_settings_per_profile(
    fake_core, profiles, launch_env, monkeypatch
):
    from hermes_ops_kit.assistants.base import AssistantConfig
    from hermes_ops_kit.assistants.client import AssistantClient

    monkeypatch.setenv("ASSISTANT_KEY", LAUNCH_SECRET)
    config = AssistantConfig(
        id="t", display_name="T", type="remote_hermes", role="remote_worker",
        capabilities=[{"id": "review"}], base_url_env="ASSISTANT_URL",
        api_key_env="ASSISTANT_KEY", model_env="ASSISTANT_MODEL_NAME",
    )  # fmt: skip
    _write_env(
        profiles.use("a"), "ASSISTANT_URL=https://a.invalid\nASSISTANT_KEY=key-a\n"
    )
    client = AssistantClient(config)
    assert (client.base_url, client.api_key) == ("https://a.invalid", "key-a")

    profiles.use("b")
    client = AssistantClient(config)
    assert (client.base_url, client.api_key) == (
        "",
        "",
    )  # not the launch environment's key


# --- where the kit may write os.environ --------------------------------------------------

# Files that own a process environment on purpose; each is a no-op when embedded.
ENVIRON_WRITERS = {"env/loader.py", "env/env_loader.py", "usage_metrics_v2.py"}


def test_os_environ_is_only_written_by_the_documented_standalone_paths():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = str(path.relative_to(PACKAGE))
        if rel in ENVIRON_WRITERS:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
                targets = (
                    node.targets
                    if not isinstance(node, ast.AugAssign)
                    else [node.target]
                )
                if any(
                    isinstance(t, ast.Subscript)
                    and ast.unparse(t.value) == "os.environ"
                    for t in targets
                ):
                    offenders.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = ast.unparse(node.func.value)
                if (
                    target == "os.environ"
                    and node.func.attr in {"pop", "update", "setdefault", "clear"}
                ) or ast.unparse(node.func) in {"os.putenv", "os.unsetenv"}:
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []
