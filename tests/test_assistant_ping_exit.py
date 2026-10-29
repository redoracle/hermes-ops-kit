"""`hermes-assistant-manager ping` must exit non-zero when the ping fails."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def cfg(tmp_path):
    path = tmp_path / "assistants.yaml"
    path.write_text(
        "assistants:\n  a1:\n    endpoint:\n      base_url_env: A1_BASE\n      api_key_env: A1_KEY\n"
    )
    return path


def _ping(tmp_path, cfg, assistant, env_extra=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HERMES_HOME": str(tmp_path),
        "PYTHONPATH": str(ROOT),
        **(env_extra or {}),
    }
    return subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "hermes_ops_kit.hermes_assistant_manager",
            "--config",
            str(cfg),
            "ping",
            assistant,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


def test_unknown_assistant_exits_not_found(tmp_path, cfg):
    done = _ping(tmp_path, cfg, "nobody")
    assert done.returncode == 6 and "not found" in done.stdout + done.stderr


def test_unreachable_assistant_exits_with_a_network_failure(tmp_path, cfg):
    done = _ping(tmp_path, cfg, "a1", {"A1_BASE": "http://127.0.0.1:9", "A1_KEY": "k"})
    assert done.returncode == 8
