"""Drift guard: every command in a user-facing doc must exist.

Commands are never executed. Each documented command is checked against the tool's own
``--help`` output (argparse validates subcommands and options there), and for the
hand-dispatched ``hermes-ops-kit`` groups against the usage text or the small table below.
The design and roadmap documents describe planned commands and are excluded.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Describe commands that are planned or historical, not the CLI as shipped.
EXCLUDED_DOCS = {"plugin-security-scanner-design.md", "ROUND_BALANCING.md"}

TOOLS = {
    "hermes-ops-kit": "hermes_ops_kit.bridge",
    "hermes-key-rotate": "hermes_ops_kit.hermes_key_rotate",
    "hermes-usage": "hermes_ops_kit.usage_metrics_v2",
    "hermes-route-manager": "hermes_ops_kit.hermes_route_manager",
    "hermes-skill-factory": "hermes_ops_kit.hermes_skill_factory",
    "hermes-assistant-manager": "hermes_ops_kit.hermes_assistant_manager",
    "hermes-export": "hermes_ops_kit.hermes_export",
}

# Second-level commands of the hermes-ops-kit groups that dispatch by hand (their
# parser accepts anything after the group). Kept next to the handlers' usage text.
HAND_DISPATCHED = {
    "plugin": {
        "scan",
        "approve",
        "revoke",
        "override",
        "disable",
        "enable",
        "block",
        "policy",
        "cache",
        "rules",
        "config",
    },
    "headroom": {
        "status",
        "doctor",
        "up",
        "down",
        "enable",
        "disable",
        "reconcile",
        "stats",
        "export",
    },
    "install": {"doctor", "setup", "repair", "update"},
}
PLUGIN_SUBS = {
    "cache": {"show", "stats", "clear"},
    "rules": {"update", "list"},
    "policy": {"export", "import"},
    "config": {"show"},
}

_FENCE = re.compile(r"^\s*```")
_CANDIDATE = re.compile(r"^[a-z][a-z0-9-]*$")


def _doc_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md"]
    files += [
        p for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in EXCLUDED_DOCS
    ]
    files += sorted((ROOT / "hermes_ops_kit" / "skills").rglob("*.md"))
    return [p for p in files if p.exists()]


def _fenced_lines(path: Path):
    inside = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE.match(line):
            inside = not inside
        elif inside:
            yield number, line


def _segments(line: str):
    """Command segments of one shell line (pipes, &&, redirects and comments removed)."""
    line = re.sub(r"\s+#.*$", "", line.strip())
    line = re.sub(r"^\$\s+", "", line)
    for part in re.split(r"\|\||\||&&|;|>|<<", line):
        part = part.strip()
        if part:
            yield part


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def documented_commands():
    seen = set()
    for path in _doc_files():
        for number, line in _fenced_lines(path):
            for segment in _segments(line):
                tokens = _tokens(segment)
                if not tokens or tokens[0] not in TOOLS:
                    continue
                key = (path.name, tuple(tokens))
                if key not in seen:
                    seen.add(key)
                    yield f"{path.relative_to(ROOT)}:{number}", tokens


def _is_word(token: str) -> bool:
    return bool(_CANDIDATE.match(token))


@functools.lru_cache(maxsize=None)
def _help(tool: str, *path: str) -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HERMES_HOME": "/nonexistent-hermes-home",
    }
    done = subprocess.run(
        [sys.executable, "-P", "-m", TOOLS[tool], *path, "-h"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
        env={**env, "PYTHONPATH": str(ROOT)},
    )
    return done.stdout + done.stderr if done.returncode in (0, 2) else ""


def _choices(help_text: str) -> set[str]:
    found = set()
    for group in re.findall(r"\{([^}]+)\}", help_text):
        found.update(group.split(","))
    return found


def _flags(tokens: list[str]) -> list[str]:
    return [t.split("=")[0] for t in tokens if re.match(r"^--?[A-Za-z][\w-]*", t)]


def _problems(tokens: list[str]) -> list[str]:
    tool, rest = tokens[0], tokens[1:]
    first = rest[0] if rest and _is_word(rest[0]) else None
    problems: list[str] = []

    if tool == "hermes-ops-kit":
        top = _choices(_help(tool))
        if first is None:
            return problems
        if first not in top:
            return [f"unknown command '{first}'"]
        second = rest[1] if len(rest) > 1 and _is_word(rest[1]) else None
        if first in HAND_DISPATCHED:
            if second and second not in HAND_DISPATCHED[first]:
                problems.append(f"unknown {first} subcommand '{second}'")
            # the bridge parser must accept it too, or the handler is never reached
            bridge_choices = _choices(_help(tool, first))
            if second and bridge_choices and second not in bridge_choices:
                problems.append(f"the bridge rejects '{first} {second}'")
            if first == "plugin" and second in PLUGIN_SUBS:
                third = rest[2] if len(rest) > 2 and _is_word(rest[2]) else None
                if third and third not in PLUGIN_SUBS[second]:
                    problems.append(f"unknown plugin {second} subcommand '{third}'")
            if first == "plugin" and second == "scan":
                from hermes_ops_kit.security.plugin_scanner import cli

                accepted = {o.split()[0].split("|")[0] for o in cli._SCAN_OPTIONS}
                accepted |= {
                    "--no-semgrep",
                    "--no-bandit",
                    "--no-gitleaks",
                    "--use-semgrep",
                    "--use-bandit",
                    "--use-gitleaks",
                }
                problems += [
                    f"plugin scan has no option {f}"
                    for f in _flags(rest[2:])
                    if f not in accepted
                ]
        elif second:
            group_choices = _choices(_help(tool, first))
            if group_choices and second not in group_choices:
                problems.append(f"unknown {first} subcommand '{second}'")
        return problems

    # argparse-backed tools: --help validates subcommands, and lists every option.
    choices = _choices(_help(tool))
    if first and choices and first not in choices:
        return [f"unknown subcommand '{first}'"]
    text = _help(tool) + (_help(tool, first) if first and choices else "")
    return [
        f"no option {f}"
        for f in _flags(rest)
        if f not in text and f not in ("-h", "--help")
    ]


def test_documentation_contains_commands():
    assert len(list(documented_commands())) > 100  # the scan itself found the docs


@pytest.mark.parametrize(
    "where,tokens",
    list(documented_commands()),
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_documented_command_exists(where, tokens):
    assert _problems(tokens) == [], f"{where}: {' '.join(tokens)}"


def test_the_guard_catches_a_missing_command():
    assert _problems(["hermes-key-rotate", "rollback", "--provider", "openai"])
    assert _problems(["hermes-ops-kit", "auth", "pool", "list"])
    assert _problems(["hermes-ops-kit", "plugin", "audit", "--json"])
    assert _problems(["hermes-ops-kit", "plugin", "scan", "--no-behavior"])
    assert not _problems(["hermes-ops-kit", "plugin", "cache", "stats"])


def test_no_documented_command_has_a_comment_glued_to_it():
    """`reconcile [--dry-run]# note` pastes as a bogus argument; a comment needs a space."""
    glued = [
        f"{where}: {' '.join(tokens)}"
        for where, tokens in documented_commands()
        if any("#" in token for token in tokens)
    ]
    assert glued == []
