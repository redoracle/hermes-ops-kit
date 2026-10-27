import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "target,override",
    [
        ("lint", "RUFF=false"),
        ("format", "RUFF=false"),
        ("security-scan", "SECURITY_SCANNER=false"),
        ("security-scan", "SECURITY_SCANNER=/definitely/missing/scanner"),
    ],
)
def test_failed_checks_cannot_succeed(target, override):
    result = subprocess.run(
        ["make", target, override], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode != 0


def test_syntax_error_fails_compile(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n")
    result = subprocess.run(
        ["make", "compile", f"SRC={tmp_path}"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode != 0


def test_successful_scanner_remains_successful():
    result = subprocess.run(
        ["make", "security-scan", "SECURITY_SCANNER=true"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
