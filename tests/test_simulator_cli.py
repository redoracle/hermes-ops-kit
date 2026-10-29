"""The documented `python3 tests/test_simulator.py --all` must not depend on the operator's config."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOSTILE_CONFIG = """\
model:
  provider: custom:my-local-llm
  default: whatever
fallback_providers:
- provider: custom:another
  model: x
"""


def test_the_simulator_ignores_and_never_modifies_the_operators_config(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text(HOSTILE_CONFIG)
    done = subprocess.run(
        [sys.executable, "-P", str(ROOT / "tests" / "test_simulator.py"), "--all"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "HERMES_HOME": str(home),
            "PYTHONPATH": str(ROOT),
        },
    )
    assert done.returncode == 0, done.stdout[-600:] + done.stderr[-600:]
    assert "RESULTS: 8/8 passed" in done.stdout
    assert (
        config.read_text() == HOSTILE_CONFIG
    )  # the simulator used its own temporary home
