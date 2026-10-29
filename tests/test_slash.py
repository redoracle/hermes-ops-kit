"""The /ops-kit slash command: read-only, bounded, isolated replies (F5, S7)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

from hermes_ops_kit import slash


def _child(monkeypatch, code: str):
    """Make the slash child run ``python -c code <tokens...>`` instead of the dispatcher."""

    def build(module, args):
        return [sys.executable, "-c", code, *args], dict(os.environ)

    monkeypatch.setattr(slash, "module_command", build)


@pytest.fixture
def no_spawn(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a process was started")

    monkeypatch.setattr(slash, "module_command", forbidden)


# --- parsing and the allowlist (nothing is ever started) --------------------------------


def test_empty_arguments_show_help_listing_only_chat_safe_commands(no_spawn):
    reply = slash.run_slash("")
    assert "read-only from chat" in reply and "/ops-kit audit tail" in reply
    assert "terminal" in reply and "rotation" in reply  # named only as terminal-only


def test_malformed_quoting_gets_a_readable_error(no_spawn):
    reply = slash.run_slash('audit search --type "unterminated')
    assert "Could not parse" in reply and "quotes" in reply


@pytest.mark.parametrize(
    "raw",
    [
        "rotate --provider openai",
        "rotate --provider openai --dry-run",
        "plugin approve --all",
        "plugin revoke --all",
        "plugin scan",
        "mcp approve --all",
        "mcp revoke",
        "headroom enable",
        "headroom disable",
        "image test 'a cat'",
        "install repair",
        "install setup",
        "install update",
        "preflight",
        "preflight --force",
        "usage",
        "usage --compact",
        "status",
        "export report",
        "audit export",
        "assistant add",
        "routes apply-profile cheap",
        "doctor --repair",
        "install doctor --repair",
        "install doctor --python /bin/sh",  # the doctor would execute that interpreter
        "audit tail --output /tmp/x",
        "audit tail abc",
        "audit tail 99999",
        "audit tail 5 6",
        "doctor extra",
        "--json doctor",  # flags cannot come before the subcommand
        "mcp tools --server",  # value flag without a value
        "mcp tools --server --json",
        "nonsense",
    ],
)
def test_everything_else_is_refused_with_a_terminal_hint(no_spawn, raw):
    reply = slash.run_slash(raw)
    assert "not available from chat" in reply and "hermes-ops-kit" in reply


def test_quoted_arguments_reach_the_command_intact(monkeypatch):
    _child(monkeypatch, "import sys, json; print(json.dumps(sys.argv[1:]))")
    reply = slash.run_slash('mcp tools --server "two words"')
    assert json.loads(reply) == ["mcp", "tools", "--server", "two words"]


@pytest.mark.parametrize(
    "raw", ["audit tail 5", "audit tail 5 --json", "audit search --type key_rotated"]
)
def test_allowed_forms_are_started(monkeypatch, raw):
    _child(monkeypatch, "print('ran')")
    assert slash.run_slash(raw) == "ran"


# --- the child: isolation, stdin, exit status, redaction ----------------------------------


def test_the_command_runs_in_its_own_process_group(monkeypatch):
    _child(monkeypatch, "import os; print(os.getpgrp() == os.getpid())")
    assert slash.run_slash("doctor") == "True"


def test_stdin_is_closed_so_a_prompt_cannot_hang(monkeypatch):
    _child(monkeypatch, "import sys; print(repr(sys.stdin.read()))")
    assert slash.run_slash("doctor") == "''"


def test_the_real_dispatcher_answers_in_a_throwaway_home(tmp_path, monkeypatch):
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    assert slash.run_slash("audit tail") == "No audit events"


def test_nonzero_exit_is_reported(monkeypatch):
    _child(monkeypatch, "print('partial'); raise SystemExit(3)")
    reply = slash.run_slash("doctor")
    assert reply.startswith("partial") and "[exit code 3]" in reply


def test_output_is_redacted_before_it_is_returned(monkeypatch):
    secret = "abc123DEF456ghi789JKL012mno345PQR678stu"
    _child(
        monkeypatch,
        f"print('export BW_SESSION={secret}==');"
        "print('Authorization: Bearer zzz/yyy+xxx==');"
        f"print('failed: sk-{secret}')",
    )
    reply = slash.run_slash("doctor")
    assert secret not in reply and "zzz/yyy" not in reply
    assert "<REDACTED>" in reply


def test_ansi_escape_sequences_are_stripped(monkeypatch):
    _child(monkeypatch, "print('\\x1b[31mred\\x1b[0m')")
    assert slash.run_slash("doctor") == "red"


# --- limits ------------------------------------------------------------------------------


def test_a_slow_command_times_out_and_its_whole_process_group_is_killed(monkeypatch):
    monkeypatch.setattr(slash, "TIMEOUT_SECONDS", 1.0)
    _child(
        monkeypatch,
        "import subprocess, time\n"
        "p = subprocess.Popen(['sleep', '60'])\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)",
    )
    started = time.monotonic()
    reply = slash.run_slash("doctor")
    assert time.monotonic() - started < 10
    assert "timed out after 1s" in reply and "terminal" in reply
    grandchild = int(reply.splitlines()[0])
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild, 0)  # the grandchild did not outlive the timeout


def test_runaway_output_is_capped_and_the_command_stopped(monkeypatch):
    monkeypatch.setattr(slash, "MAX_CAPTURE_BYTES", 2000)
    _child(
        monkeypatch, "import sys\nwhile True:\n    sys.stdout.write('x' * 1000 + '\\n')"
    )
    started = time.monotonic()
    reply = slash.run_slash("doctor")
    assert time.monotonic() - started < 10
    assert "output limit reached" in reply
    assert len(reply) < slash.MAX_REPLY_CHARS + 200


def test_long_replies_are_truncated_for_chat(monkeypatch):
    monkeypatch.setattr(slash, "MAX_REPLY_CHARS", 100)
    _child(monkeypatch, "print('y' * 500)")
    reply = slash.run_slash("doctor")
    assert reply.endswith("… (output truncated)") and len(reply) < 140


# --- concurrency -------------------------------------------------------------------------


def test_simultaneous_replies_never_mix_output(monkeypatch):
    _child(
        monkeypatch,
        "import sys, time\n"
        "for _ in range(40):\n"
        "    print(sys.argv[1], flush=True)\n"
        "    time.sleep(0.002)",
    )
    markers = [f"AAAA-{i}" for i in range(6)]
    replies: dict[str, str] = {}

    def call(marker):
        replies[marker] = slash.run_slash(f"audit search --type {marker}")

    # the fake child prints argv[1] ("audit"); use argv[-1] (the --type value) instead
    _child(
        monkeypatch,
        "import sys, time\n"
        "for _ in range(40):\n"
        "    print(sys.argv[-1], flush=True)\n"
        "    time.sleep(0.002)",
    )
    threads = [threading.Thread(target=call, args=(m,)) for m in markers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    for marker, reply in replies.items():
        assert set(reply.split()) == {marker}
    assert len(replies) == len(markers)


# --- registration --------------------------------------------------------------------------


def test_the_registered_handler_returns_a_reply_string(monkeypatch):
    import hermes_ops_kit

    registered = {}

    class Context:
        def __getattr__(self, name):
            def record(*args, **kwargs):
                if name == "register_command":
                    registered[args[0]] = args[1]

            return record

    hermes_ops_kit.register(Context())
    handler = registered["ops-kit"]
    assert "read-only from chat" in handler("")
    assert "not available from chat" in handler("rotate --provider openai")
    assert "not available from chat" in handler(["rotate", "--provider", "openai"])


# --- read-only by evidence (review follow-ups) -----------------------------------------------


def test_every_allowed_command_leaves_the_profile_untouched(tmp_path, monkeypatch):
    """Run each chat-reachable command for real: no file may appear anywhere in the home."""
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    before = {p for p in tmp_path.rglob("*")}
    for entry in sorted(slash.ALLOWED_COMMANDS):
        reply = slash.run_slash(" ".join(entry))
        assert "Could not start" not in reply, (entry, reply)
        created = {p for p in tmp_path.rglob("*")} - before
        assert not created, f"{' '.join(entry)} created {sorted(map(str, created))}"


def _mcp_probe_home(home, marker):
    (home / "config.yaml").write_text(
        "mcp_servers:\n"
        "  probe:\n"
        "    command: sh\n"
        f"    args: ['-c', 'touch {marker}; sleep 0.2']\n"
    )


def test_chat_never_starts_a_configured_mcp_server(tmp_path, monkeypatch):
    from hermes_ops_kit import ops_config_io
    from hermes_ops_kit.mcp_auditor import auditor

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    marker = tmp_path / "server-was-started"
    _mcp_probe_home(tmp_path, marker)

    # control: an unrestricted audit does launch the configured server...
    auditor.run_audit()
    assert marker.exists()
    marker.unlink()

    # ...but none of the chat-reachable MCP commands may
    for command in ("mcp list", "mcp risks", "mcp tools", "mcp policy"):
        slash.run_slash(command)
        time.sleep(0.4)
        assert not marker.exists(), (
            f"/ops-kit {command} started a configured MCP server"
        )


def test_read_only_mode_stops_seeding_defaults(tmp_path, monkeypatch):
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    monkeypatch.setenv(ops_config_io.READ_ONLY_ENV, "1")
    path = ops_config_io.deployed_or_bundled("routes.yaml", seed=True)
    assert path == ops_config_io.bundled_path("routes.yaml")
    assert not (tmp_path / "ops-kit").exists()


def test_a_core_refusal_becomes_a_bounded_reply_not_an_exception(monkeypatch):
    def refuse(module, args):
        raise RuntimeError("no profile-scoped environment available")

    monkeypatch.setattr(slash, "module_command", refuse)
    reply = slash.run_slash("doctor")
    assert reply.startswith("Could not start the command")
