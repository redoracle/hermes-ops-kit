"""The ``/ops-kit`` slash command: a small, read-only, bounded window onto the CLI.

Core calls a plugin slash handler with the raw argument string and shows the
returned string to whoever sent the command, which may be a chat surface. So:

* only an allowlist of read-only, local, fast subcommands is reachable; every
  mutating, paid or slow command (rotate, approvals, enable/disable, image
  generation, scans, usage probes) answers with "run it in a terminal";
* the command runs in its own process and process group (never by redirecting the
  shared ``sys.stdout``), with no stdin, a finite timeout and a capture limit, so
  simultaneous calls cannot mix output and a runaway command cannot hang the host
  or exhaust memory;
* the reply is redacted and capped before it leaves this module.

The active Hermes profile reaches the child through ``child_env()``/``home()``,
which already follow core's context-local profile.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import threading
from dataclasses import dataclass

from . import ops_config_io
from ._cli_args import PRESENTATION_FLAGS
from ._subprocess import module_command
from .security.redaction import redact

TIMEOUT_SECONDS = 20.0  # core's CLI blocks the terminal for a sync handler: stay short
MAX_CAPTURE_BYTES = 64 * 1024  # child output read before the child is stopped
MAX_REPLY_CHARS = 3500  # what a chat message can reasonably carry


@dataclass(frozen=True)
class _Spec:
    """What a chat user may add after a subcommand's leading tokens."""

    positional: re.Pattern[str] | None = None  # e.g. a count; None = no positionals
    value_flags: frozenset[str] = frozenset()  # flags that take one value


# Flags that only change presentation. Anything else starting with "-" is refused:
# notably --repair, --force, --apply, --yes, --write, --output, and --python (the
# doctor would execute the interpreter it is pointed at).
_PRESENTATION_FLAGS = PRESENTATION_FLAGS

# Subcommands reachable from chat, matched on their exact leading tokens. Each is
# read-only, uses local configuration only and finishes well inside the timeout.
ALLOWED_COMMANDS: dict[tuple[str, ...], _Spec] = {
    ("doctor",): _Spec(),
    ("audit", "tail"): _Spec(positional=re.compile(r"\d{1,3}")),
    ("audit", "search"): _Spec(value_flags=frozenset({"--type", "--assistant", "--provider"})),
    ("mcp", "list"): _Spec(),
    ("mcp", "risks"): _Spec(),
    ("mcp", "tools"): _Spec(value_flags=frozenset({"--server"})),
    ("mcp", "policy"): _Spec(),
    ("budget", "status"): _Spec(),
    ("maintenance", "profiles"): _Spec(),
    ("image", "routes"): _Spec(),
    ("routes", "show"): _Spec(),
    ("headroom", "status"): _Spec(),
    ("install", "doctor"): _Spec(),
    ("plugin", "policy"): _Spec(),
    ("assistant", "list"): _Spec(),
}  # fmt: skip

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TERMINAL_HINT = "run it in a terminal: `hermes-ops-kit {command}`"


def _help() -> str:
    names = sorted(" ".join(parts) for parts in ALLOWED_COMMANDS)
    return (
        "Hermes Ops Kit (read-only from chat)\n"
        + "\n".join(f"  /ops-kit {name}" for name in names)
        + "\nEverything else (rotation, approvals, scans, usage probes, image generation, "
        "repairs) must be run in a terminal with `hermes-ops-kit`."
    )


def _refusal(tokens: list[str]) -> str:
    shown = " ".join(tokens)
    return f"'/ops-kit {shown}' is not available from chat; " + _TERMINAL_HINT.format(
        command=shown
    )


def _is_allowed(tokens: list[str]) -> bool:
    for entry, spec in ALLOWED_COMMANDS.items():
        if tuple(tokens[: len(entry)]) != entry:
            continue
        rest = tokens[len(entry) :]
        i = 0
        positionals = 0
        while i < len(rest):
            token = rest[i]
            if token in _PRESENTATION_FLAGS:
                i += 1
            elif (
                token in spec.value_flags
                and i + 1 < len(rest)
                and not rest[i + 1].startswith("-")
            ):
                i += 2
            elif (
                spec.positional is not None
                and positionals == 0  # at most one positional argument
                and not token.startswith("-")
                and spec.positional.fullmatch(token)
            ):
                positionals += 1
                i += 1
            else:
                return False
        return True
    return False


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _run(tokens: list[str]) -> tuple[bytes, int | None, str]:
    """Run the dispatcher in a child; returns (output, exit code, outcome note)."""
    cmd, env = module_command("commands", tokens)
    env["NO_COLOR"] = "1"
    env[ops_config_io.READ_ONLY_ENV] = "1"  # nothing seeded, no MCP server started
    proc = subprocess.Popen(  # noqa: S603 - argv list, allowlisted tokens, no shell
        cmd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # its own process group, so cleanup reaches grandchildren
    )
    captured = bytearray()
    overflow = threading.Event()

    def pump() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                return
            room = MAX_CAPTURE_BYTES - len(captured)
            captured.extend(chunk[:room])
            if len(chunk) > room:
                overflow.set()
                _kill_group(proc)
                return

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    note = ""
    try:
        proc.wait(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.wait()
        note = f"timed out after {TIMEOUT_SECONDS:g}s"
    reader.join(timeout=2)
    if proc.stdout is not None:
        proc.stdout.close()
    if overflow.is_set():
        note = note or "output limit reached"
    return bytes(captured), proc.returncode, note


def run_slash(raw_args: str) -> str:
    """The reply for ``/ops-kit <raw_args>``; never raises for user input."""
    try:
        tokens = shlex.split(raw_args or "")
    except ValueError as exc:
        return f"Could not parse the arguments ({exc}). Check quotes and try again."
    if not tokens:
        return _help()
    if not _is_allowed(tokens):
        return _refusal(tokens)

    try:
        output, code, note = _run(tokens)
    except (
        OSError,
        RuntimeError,
    ) as exc:  # RuntimeError: core refused to scope the environment
        return redact(f"Could not start the command: {exc}")

    text = redact(_ANSI.sub("", output.decode("utf-8", errors="replace"))).strip()
    if len(text) > MAX_REPLY_CHARS:
        text = text[:MAX_REPLY_CHARS].rstrip() + "\n… (output truncated)"
    status = []
    if note:
        status.append(note + "; " + _TERMINAL_HINT.format(command=" ".join(tokens)))
    elif code not in (0, None):
        status.append(f"exit code {code}")
    return "\n".join(
        part for part in (text or "(no output)", *(f"[{s}]" for s in status)) if part
    )
