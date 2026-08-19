"""Hermes Ops Kit — CLI Test Runner.

Black-box subprocess-based CLI testing. Never imports command internals.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

sys.path.insert(0, str(PROJECT_DIR))

from hermes_ops_kit._subprocess import module_command  # noqa: E402


@dataclass
class CliResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    env: dict[str, str] = field(default_factory=dict)


def run_cli(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int = 60,
    test_mode: bool = True,
) -> CliResult:
    """Run a CLI command as a subprocess and capture output."""
    merged_env = os.environ.copy()
    if test_mode:
        merged_env["HERMES_TEST_MODE"] = "1"
        merged_env["NO_COLOR"] = "1"
    if env:
        merged_env.update(env)

    cmd = [sys.executable] + args
    if args and args[0].endswith(".py"):
        # script argv → -P -m hermes_ops_kit.<module> (cwd off sys.path);
        # accepts a bare filename or an absolute path under PROJECT_DIR
        _rel = Path(args[0])
        try:
            _rel = _rel.relative_to(PROJECT_DIR)
        except ValueError:
            pass
        module = ".".join(_rel.with_suffix("").parts)
        cmd, mod_env = module_command(module, args[1:])
        merged_env["PYTHONPATH"] = os.pathsep.join(
            e for e in (mod_env.get("PYTHONPATH"), merged_env.get("PYTHONPATH")) if e
        )
    start = time.time()
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd or PROJECT_DIR),
            env=merged_env,
        )
    except subprocess.TimeoutExpired:
        duration = int((time.time() - start) * 1000)
        return CliResult(
            args, -1, "", f"TIMEOUT after {timeout}s", duration, merged_env
        )

    duration = int((time.time() - start) * 1000)
    return CliResult(args, r.returncode, r.stdout, r.stderr, duration, merged_env)


def normalize_output(text: str) -> str:
    """Normalize dynamic values for snapshot comparison."""
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?", "<TIMESTAMP>", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?", "<TIMESTAMP>", text)
    text = re.sub(r"\b\d{3,5}ms\b", "<LATENCY_MS>", text)
    text = re.sub(r"/var/folders/[^\s]+", "<TMP>", text)
    text = re.sub(r"/tmp/[^\s]+", "<TMP>", text)
    text = re.sub(r"/Users/[^/\s]+/GIT/INFRA", "<PROJECT>", text)
    text = re.sub(r"/Users/[^/\s]+", "<HOME>", text)
    return text


def try_json(stdout: str) -> dict[str, Any] | None:
    """Try to parse stdout as JSON."""
    try:
        return json.loads(stdout)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


SECRET_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9-_]{15,}", "Anthropic key"),
    (r"sk-[A-Za-z0-9-_]{15,}", "OpenAI/DeepSeek key"),
    (r"AIza[0-9A-Za-z_-]{30,}", "Gemini key"),
    (r"ghp_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"github_pat_[A-Za-z0-9_]{30,}", "GitHub PAT"),
    (r"Bearer\s+[A-Za-z0-9_\-\.]{10,}", "Bearer token"),
    (r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----", "Private key"),
    (r"BW_SESSION=[A-Za-z0-9+/=]{20,}", "Bitwarden session"),
]


def find_secrets(text: str) -> list[str]:
    """Find any secret-like patterns in text. Returns list of violation descriptions."""
    violations = []
    for pattern, label in SECRET_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            violations.append(
                f"{label}: {re.search(pattern, text).group(0)[:20] if re.search(pattern, text) else '?'}..."
            )
    return violations


def has_ansi(text: str) -> bool:
    """Check for ANSI escape sequences."""
    return bool(re.search(r"\x1b\[[0-9;]*m", text))
