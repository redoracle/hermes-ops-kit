"""Hermes Ops Kit — apply-profile reasoning_effort wiring.

Black-box subprocess tests: apply-profile must write ``agent.reasoning_effort``
into ~/.hermes/config.yaml, honour ``--effort`` overrides, fall back to the
profile default, and reject unknown tiers.

Run with: python3 -m pytest tests/cli/test_apply_profile_effort.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli_runner import run_cli  # pyright: ignore[reportMissingImports]


def _apply(tmp_path: Path, *extra: str):
    """Run apply-profile against an isolated HERMES_HOME."""
    env = {"HERMES_HOME": str(tmp_path), "NO_COLOR": "1", "HERMES_TEST_MODE": "1"}
    # Seed an empty config so _load_yaml returns {} regardless of missing-file
    # handling; apply-profile rebuilds it from the profile preset.
    (tmp_path / "config.yaml").write_text("{}\n")
    return run_cli(["hermes_route_manager.py", "apply-profile", *extra], env=env)


def test_apply_profile_writes_effort_override(tmp_path: Path) -> None:
    r = _apply(tmp_path, "cheap", "--effort", "high")
    assert r.returncode == 0, r.stderr
    cfg = (tmp_path / "config.yaml").read_text()
    assert "reasoning_effort" in cfg  # written to config, not just printed
    assert "high" in cfg
    assert "effort:" in r.stdout


def test_apply_profile_default_effort_per_profile(tmp_path: Path) -> None:
    # cheap=low, balanced=medium, max-quality=max (the v0.19.0 top tier).
    for profile, expected in (("cheap", "low"), ("balanced", "medium"), ("max-quality", "max")):
        r = _apply(tmp_path, profile)
        assert r.returncode == 0, r.stderr
        cfg = (tmp_path / "config.yaml").read_text()
        assert "reasoning_effort" in cfg, f"{profile} missing reasoning_effort"
        assert expected in cfg, f"{profile} expected effort {expected!r}"
        assert expected in r.stdout


def test_apply_profile_effort_none_disables_thinking(tmp_path: Path) -> None:
    r = _apply(tmp_path, "balanced", "--effort", "none")
    assert r.returncode == 0, r.stderr
    cfg = (tmp_path / "config.yaml").read_text()
    assert "none" in cfg
    assert "none" in r.stdout


def test_apply_profile_rejects_invalid_effort(tmp_path: Path) -> None:
    # argparse choices must reject an unknown tier (e.g. the pre-v0.19.0 "turbo").
    r = _apply(tmp_path, "cheap", "--effort", "turbo")
    assert r.returncode != 0
    assert "invalid choice" in r.stderr or "choices" in r.stderr
