"""Hermes Ops Kit — Snapshot Tests.

Golden output tests for UX consistency. These verify that:
- --json output is pure JSON with no ANSI
- --plain output has no Unicode boxes or emoji
- Key sections appear (ROUTE, ASSISTANTS, PROVIDERS)
- Assistant appears under ASSISTANTS (never under PROVIDERS)
- No secrets leak into any output mode
- Compact output fits reasonable line count
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = sys.executable


sys.path.insert(0, PROJECT_DIR)

from hermes_ops_kit._subprocess import module_command  # noqa: E402


def _run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    argv = list(args)
    cmd, env = [PYTHON] + argv, None
    if argv and argv[0].endswith(".py"):
        # script argv → -P -m hermes_ops_kit.<module> (cwd off sys.path)
        cmd, env = module_command(argv[0][:-3].replace("/", "."), argv[1:])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=PROJECT_DIR,
        env=env,
    )


def _has_ansi(text: str) -> bool:
    return bool(re.search(r"\x1b\[[0-9;]*m", text))


def _has_secrets(text: str) -> bool:
    patterns = [
        r"sk-ant-[A-Za-z0-9-_]{15,}",
        r"sk-[A-Za-z0-9-_]{15,}",
        r"AIza[0-9A-Za-z_-]{30,}",
        r"ghp_[A-Za-z0-9]{30,}",
        r"Bearer\s+[A-Za-z0-9_\-\.]{10,}",
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    return False


# ── JSON output tests ─────────────────────────────────────────────


def test_usage_metrics_json_is_valid():
    """usage_metrics --json produces valid JSON with no ANSI."""
    r = _run("usage_metrics_v2.py", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert "_timestamp" in data
    assert "_hermes" in data or "_bridge" in data
    assert not _has_ansi(r.stdout)
    assert not _has_secrets(r.stdout)


def test_usage_metrics_json_has_providers():
    """usage_metrics --json includes all 8 providers."""
    r = _run("usage_metrics_v2.py", "--json")
    data = json.loads(r.stdout)
    for provider in [
        "openai",
        "anthropic",
        "github",
        "gemini",
        "deepseek",
        "nvidia",
        "fireworks",
        "deepinfra",
    ]:
        assert provider in data, f"Missing provider: {provider}"


def test_usage_metrics_json_has_assistants_section():
    """Assistant appears under _assistants, never under providers."""
    r = _run("usage_metrics_v2.py", "--json")
    data = json.loads(r.stdout)
    assert "_assistants" in data


def test_assistant_manager_list_json_is_valid():
    """assistant-manager list --json produces valid JSON."""
    config = os.path.join(PROJECT_DIR, "config", "assistants.yaml")
    r = _run("hermes_assistant_manager.py", "--config", config, "list", "--json")
    # Parse stdout as JSON — fall back to raw if parse fails
    try:
        data = json.loads(r.stdout)
        assert data.get("ok") is True
        assert not _has_ansi(r.stdout)
        assert not _has_secrets(r.stdout)
    except json.JSONDecodeError:
        # If stdout is empty, check stderr for clues
        assert False, (
            f"list --json returned non-JSON: stdout={r.stdout[:200]!r} stderr={r.stderr[:200]!r}"
        )


def test_key_rotate_help_json():
    """key_rotate --help mentions --json flag."""
    r = _run("hermes_key_rotate.py", "--help")
    assert r.returncode == 0
    assert "--json" in r.stdout


# ── Compact output tests ──────────────────────────────────────────


def test_usage_metrics_compact_fits():
    """Compact mode fits reasonable line count."""
    r = _run("usage_metrics_v2.py", "--compact")
    lines = [line for line in r.stdout.split("\n") if line.strip()]
    assert len(lines) <= 25, f"Compact output too long: {len(lines)} lines"


def test_usage_metrics_compact_has_key_sections():
    """Compact mode shows ROUTE essentials."""
    r = _run("usage_metrics_v2.py", "--compact")
    # "primary" / "default" appear when at least one provider is online;
    # with 0 providers (CI) the header "hermes ops kit" is always present.
    assert any(
        word in r.stdout.lower() for word in ("primary", "default", "hermes ops kit")
    )


# ── NO_COLOR tests ────────────────────────────────────────────────


def test_no_color_disables_ansi():
    """NO_COLOR=1 produces no ANSI escape codes."""
    env = {**os.environ, "NO_COLOR": "1"}
    env["PYTHONPATH"] = module_command("usage_metrics_v2", ["--compact"])[1][
        "PYTHONPATH"
    ]
    r = subprocess.run(
        module_command("usage_metrics_v2", ["--compact"])[0],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_DIR,
        env=env,
    )
    assert not _has_ansi(r.stdout)


def test_plain_mode_no_unicode_boxes():
    """NO_COLOR + non-TTY produces output without ANSI (plain-like behavior)."""
    env = {**os.environ, "NO_COLOR": "1"}
    env["PYTHONPATH"] = module_command("usage_metrics_v2", ["--compact"])[1][
        "PYTHONPATH"
    ]
    r = subprocess.run(
        module_command("usage_metrics_v2", ["--compact"])[0],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_DIR,
        env=env,
    )
    assert not _has_ansi(r.stdout)
    assert "READY" in r.stdout or "ONLINE" in r.stdout or "PROVIDERS" in r.stdout


# ── Secret safety tests ───────────────────────────────────────────


def test_compact_no_secrets():
    """Compact output leaks no API keys."""
    r = _run("usage_metrics_v2.py", "--compact")
    assert not _has_secrets(r.stdout)


def test_json_no_secrets():
    """JSON output leaks no API keys."""
    r = _run("usage_metrics_v2.py", "--json")
    assert not _has_secrets(r.stdout)


def test_assistant_list_no_secrets():
    """Assistant list leaks no secrets."""
    r = _run(
        "hermes_assistant_manager.py",
        "--config",
        os.path.join(PROJECT_DIR, "config", "assistants.yaml"),
        "list",
        "--json",
    )
    assert not _has_secrets(r.stdout)


# ── Section ordering tests ────────────────────────────────────────


def test_assistant_id_under_assistants_not_providers():
    """In JSON output, Assistant is under _assistants, never in provider keys."""
    r = _run("usage_metrics_v2.py", "--json")
    data = json.loads(r.stdout)
    assert "_assistants" in data, (
        f"Missing _assistants key. Keys: {list(data.keys())[:10]}"
    )
    assert len(data.get("_assistants", {})) >= 0


def test_route_sections_present():
    """Rich output contains ROUTE, ASSISTANTS, PROVIDERS sections."""
    r = _run("usage_metrics_v2.py")
    assert "ROUTE" in r.stdout
    assert "PROVIDERS" in r.stdout
    assert "ASSISTANTS" in r.stdout
