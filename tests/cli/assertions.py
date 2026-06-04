"""Hermes Ops Kit — CLI Test Assertions.

Reusable assertions for CLI test output validation.
"""

from __future__ import annotations

from tests.cli.cli_runner import CliResult, find_secrets, has_ansi, try_json  # pyright: ignore[reportMissingImports]


def assert_exit_ok(r: CliResult) -> None:
    assert r.returncode == 0, (
        f"Expected exit 0, got {r.returncode}. stderr: {r.stderr[:200]}"
    )


def assert_exit_fail(r: CliResult) -> None:
    assert r.returncode != 0, f"Expected non-zero exit, got {r.returncode}"


def assert_json_valid(r: CliResult) -> dict:
    """Assert stdout is valid JSON and return parsed dict."""
    data = try_json(r.stdout)
    assert data is not None, f"Invalid JSON: {r.stdout[:200]}"
    return data


def assert_no_ansi(r: CliResult) -> None:
    assert not has_ansi(r.stdout), f"ANSI found in stdout: {r.stdout[:100]}"
    assert not has_ansi(r.stderr), f"ANSI found in stderr: {r.stderr[:100]}"


def assert_no_secrets(r: CliResult) -> None:
    secrets = find_secrets(r.stdout)
    assert not secrets, f"Secrets in stdout: {secrets}"
    secrets = find_secrets(r.stderr)
    assert not secrets, f"Secrets in stderr: {secrets}"


def assert_json_has_keys(r: CliResult, keys: list[str]) -> dict:
    data = assert_json_valid(r)
    for k in keys:
        assert k in data, (
            f"Missing key '{k}' in JSON output. Keys: {list(data.keys())[:10]}"
        )
    return data


def assert_section_present(r: CliResult, section: str) -> None:
    assert section in r.stdout, f"Section '{section}' not found in output"


def assert_stderr_empty(r: CliResult) -> None:
    assert not r.stderr.strip(), f"Expected empty stderr, got: {r.stderr[:200]}"


def assert_json_no_secrets(r: CliResult) -> None:
    data = assert_json_valid(r)
    raw = str(data)
    secrets = find_secrets(raw)
    assert not secrets, f"Secrets in JSON output: {secrets}"


def assert_human_table_has(r: CliResult, text: str) -> None:
    assert text in r.stdout, f"'{text}' not found in human output"
