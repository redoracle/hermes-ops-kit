"""Hermes Ops Kit — CLI Test Fixtures.

Isolated temporary Hermes homes for false positive/negative testing.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write simple YAML without PyYAML dependency."""

    def _fmt(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return f'"{v}"'
        return str(v)

    lines = []
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for sk, sv in v.items():
                if isinstance(sv, dict):
                    lines.append(f"  {sk}:")
                    for ssk, ssv in sv.items():
                        if isinstance(ssv, list):
                            lines.append(f"    {ssk}:")
                            for item in ssv:
                                if isinstance(item, dict):
                                    cid = item.get("id", "")
                                    lines.append(f"      - id: {_fmt(cid)}")
                                    for ik, iv in item.items():
                                        if ik != "id":
                                            lines.append(f"        {ik}: {_fmt(iv)}")
                                else:
                                    lines.append(f"      - {item}")
                        elif isinstance(ssv, dict):
                            # Nested mapping (endpoint:, security:, policy:) — emit
                            # indented key/values, not a one-line Python repr.
                            lines.append(f"    {ssk}:")
                            for dk, dv in ssv.items():
                                lines.append(f"      {dk}: {_fmt(dv)}")
                        else:
                            lines.append(f"    {ssk}: {_fmt(ssv)}")
                elif isinstance(sv, list):
                    lines.append(f"  {sk}:")
                    for item in sv:
                        lines.append(f"    - {item}")
                else:
                    lines.append(f"  {sk}: {_fmt(sv)}")
        elif isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {_fmt(v)}")
    path.write_text("\n".join(lines) + "\n")


class HermesFixture:
    """Isolated Hermes test home with controlled config."""

    def __init__(self, name: str = "test") -> None:
        self.home = Path(tempfile.mkdtemp(prefix=f"hermes-test-{name}-"))
        self.ops_kit = self.home / "ops-kit"
        self.env_file = self.home / ".env"
        self.config_yaml = self.home / "config.yaml"

    def setup_dirs(self) -> "HermesFixture":
        self.home.mkdir(parents=True, exist_ok=True)
        self.ops_kit.mkdir(parents=True, exist_ok=True)
        self.home.chmod(0o700)
        return self

    def setup_safe_env(self) -> "HermesFixture":
        self.env_file.write_text(
            "HERMES_SECRET_BACKEND=vaultwarden\nVAULTWARDEN_SERVER_URL=https://vault.test:80\n"
        )
        self.env_file.chmod(0o600)
        return self

    def setup_unsafe_env(self) -> "HermesFixture":
        self.env_file.write_text("HERMES_SECRET_BACKEND=vaultwarden\n")
        self.env_file.chmod(0o644)
        return self

    def setup_assistants_yaml(self, data: dict[str, Any]) -> "HermesFixture":
        path = self.ops_kit / "assistants.yaml"
        _write_yaml(path, data)
        path.chmod(0o600)
        return self

    def setup_secret_assistants_yaml(self) -> "HermesFixture":
        data = {
            "version": 1,
            "assistants": {
                "test": {
                    "enabled": True,
                    "display_name": "Test",
                    "type": "remote_hermes",
                    "role": "remote_worker",
                    "transport": "openai_chat_completions",
                    "endpoint": {
                        "api_key": "sk-abc123testsecretnotreal",
                        "base_url_env": "TEST_URL",
                    },
                    "security": {},
                    "policy": {},
                    "capabilities": [],
                }
            },
        }
        return self.setup_assistants_yaml(data)

    def setup_valid_assistants_yaml(self) -> "HermesFixture":
        data = {
            "version": 1,
            "assistants": {
                "test-assistant": {
                    "enabled": True,
                    "display_name": "Assistant Test",
                    "type": "remote_hermes",
                    "role": "security_profiler",
                    "transport": "openai_chat_completions",
                    "endpoint": {
                        "base_url_env": "ASSISTANT_API_BASE",
                        "api_key_env": "ASSISTANT_API_KEY",
                        "model_env": "ASSISTANT_MODEL",
                        "default_model": "hermes-agent",
                    },
                    "capabilities": [
                        {
                            "id": "test_cap",
                            "description": "Test",
                            "safe_by_default": True,
                        }
                    ],
                    "security": {"network_zone": "test", "require_token": True},
                    "policy": {"max_timeout_seconds": 120},
                }
            },
        }
        return self.setup_assistants_yaml(data)

    def env(self) -> dict[str, str]:
        return {"HERMES_HOME": str(self.home), "NO_COLOR": "1", "CI": "1"}

    def cleanup(self) -> None:
        if self.home.exists():
            shutil.rmtree(self.home)

    def __enter__(self) -> "HermesFixture":
        return self.setup_dirs()

    def __exit__(self, *_: Any) -> None:
        self.cleanup()
