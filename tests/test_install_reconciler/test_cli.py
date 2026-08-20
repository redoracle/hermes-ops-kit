"""CLI tests: ``install doctor`` read-only surface (subprocess, black-box)."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "hermes_ops_kit.bridge",
            "install",
            "doctor",
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_doctor_runs_read_only():
    proc = _run()
    assert proc.returncode in (0, 1)
    assert "Installation:" in proc.stdout


def test_doctor_json_is_valid_schema():
    proc = _run("--json")
    assert proc.returncode in (0, 1)
    payload = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line) if line.endswith("}") else None
            if payload is None:
                # multi-line JSON: find the full document
                start = proc.stdout.index("{")
                payload = json.loads(proc.stdout[start:])
            break
    assert payload is not None
    assert payload["schema_version"] == 1
    assert payload["overall"] in ("HEALTHY", "REPAIRABLE", "DIAGNOSE_ONLY", "UNSAFE")
    assert set(payload) == {
        "schema_version",
        "overall",
        "runtime",
        "actual",
        "expected",
        "findings",
    }


def test_doctor_verbose_shows_details():
    proc = _run("--verbose")
    assert proc.returncode in (0, 1)
    # Verbose prints evidence detail lines when findings exist; never crashes
    assert "Runtime:" in proc.stdout
