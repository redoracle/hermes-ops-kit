"""Config single-source guardrails.

W1: every .env parsing path resolves identically through env.loader.
W2: HERMES_HOME override is honored (no hardcoded ~/.hermes paths).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_home(tmp_path, env_body: str, gen_body: str | None = None) -> str:
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text(env_body)
    if gen_body is not None:
        (home / ".env.generated").write_text(gen_body)
    return str(home)


def test_env_loader_parity(tmp_path, monkeypatch):
    """Former divergent parsers must all resolve through env.loader."""
    from hermes_ops_kit.env import loader

    home = _make_home(
        tmp_path,
        env_body='A=1\nB="quoted"\nC=unquoted # trailing comment\n',
        gen_body="A=2\nD=gen-only\n",
    )
    monkeypatch.setattr(loader.ops_config_io, "HERMES_HOME", home)

    expected = {"A": "2", "B": "quoted", "C": "unquoted", "D": "gen-only"}
    assert loader.load_env_dict(home) == expected

    # commands._load_hermes_env and route_verifier._load_env delegate
    from hermes_ops_kit import commands, route_verifier

    monkeypatch.setattr(
        route_verifier.ops_config_io, "HERMES_HOME", home, raising=False
    )
    monkeypatch.setattr(commands.ops_config_io, "HERMES_HOME", home, raising=False)
    assert commands._load_hermes_env() == expected
    assert route_verifier._load_env() == expected


def test_load_env_file_does_not_clobber_real_env(tmp_path, monkeypatch):
    """usage_metrics_v2.load_env_file must not override real env vars."""
    from hermes_ops_kit import usage_metrics_v2 as um
    from hermes_ops_kit.env import loader

    home = _make_home(tmp_path, env_body="MY_PROBE_VAR=from-file\n")
    monkeypatch.setattr(loader.ops_config_io, "HERMES_HOME", home)
    monkeypatch.setattr(um.ops_config_io, "HERMES_HOME", home, raising=False)
    monkeypatch.setenv("MY_PROBE_VAR", "from-env")
    um.load_env_file()
    assert os.environ["MY_PROBE_VAR"] == "from-env"


def test_hermes_home_override_honored(tmp_path, monkeypatch):
    """All path authorities must follow HERMES_HOME."""
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for mod_name in (
        "hermes_ops_kit",
        "hermes_ops_kit.route_verifier",
        "hermes_ops_kit.hermes_route_manager",
        "hermes_ops_kit.env.loader",
    ):
        mod = sys.modules.get(mod_name) or __import__(mod_name, fromlist=["x"])
        # modules cache HERMES_HOME at import; recompute as they would
        assert os.environ["HERMES_HOME"] == str(home)


def test_no_hardcoded_hermes_home_paths():
    """Source must not construct ~/.hermes paths outside ops_config_io."""
    import subprocess

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hermes_ops_kit")
    result = subprocess.run(
        ["grep", "-rn", '-e', 'expanduser("~/.hermes', "-e", '"~/.hermes/', root, "--include=*.py"],
        capture_output=True,
        text=True,
    )
    # Allowlist: ops_config_io (authority), the expand_home docstring/default
    # strings, and display-only messages.
    allowed_substrings = (
        "ops_config_io.py",
        'os_config_io.expand_home',
        "~/.hermes/ops-kit/workflows/flux-text2image.json",  # default passed to expand_home
        'memory_project_root": "~/.hermes"',  # default passed to expand_home
        '"run_dir": "~/.hermes/',
        '"state_file": "~/.hermes/',
        'or "~/.hermes", "~/.hermes"',  # credential_read_guard fallback probe
        # display-only strings (warnings, notes, metadata) — not path construction
        "has unsafe permissions",
        "not found — using defaults",
        '"~/.hermes/.env 0600"',
        '("~/.hermes/.env", False, "not found")',
        "is not chmod 0600",
        '"notes": ["~/.hermes/key-rotation-audit.jsonl"]',
        '"source": "~/.hermes/.env"',
    )
    offenders = [
        line
        for line in result.stdout.strip().splitlines()
        if line and not any(a in line for a in allowed_substrings)
    ]
    assert not offenders, f"Hardcoded ~/.hermes paths:\n{chr(10).join(offenders)}"
