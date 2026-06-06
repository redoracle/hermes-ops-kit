"""Hermes Ops Kit — Security Tests

Tests for redaction, fingerprinting, file permissions, and secret scanning.
Run with: python3 -m pytest tests/test_security.py -v
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security.redaction import redact, SECRET_PATTERNS  # pyright: ignore[reportMissingImports]
from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from security.file_permissions import (  # pyright: ignore[reportMissingImports]
    ensure_dir_700,
    ensure_file_600,
    verify_permissions,
    check_env_file,
)
from security.secret_scanner import (  # pyright: ignore[reportMissingImports]
    scan_for_secrets,
    scan_for_forbidden_blocks,
    assert_clean,
)
from security.secret_backend import UnsafeSecretWriteError  # pyright: ignore[reportMissingImports]


# ── Redaction Tests ────────────────────────────────────────────────────


def test_redacts_openai_key():
    assert "<OPENAI_KEY_REDACTED>" in redact(
        "sk-proj-abc123xyz789def456ghi012jkl345mno678pqr"
    )


def test_redacts_anthropic_key():
    assert "<ANTHROPIC_KEY_REDACTED>" in redact(
        "sk-ant-api03-abc123xyz789def456ghi012jkl345mno678pqr901stu234vwx"
    )


def test_redacts_gemini_key():
    assert "<GEMINI_KEY_REDACTED>" in redact("AIzaSyDabc123xyz789def456ghi012jkl345mno")


def test_redacts_github_token():
    assert "<GITHUB_TOKEN_REDACTED>" in redact(
        "ghp_abc123xyz789def456ghi012jkl345mno678"
    )


def test_redacts_nvidia_key():
    assert "<NVIDIA_KEY_REDACTED>" in redact(
        "nvapi-abc123xyz789def456ghi012jkl345mno678pqr901stu234vwx567"
    )


def test_redacts_bearer_token():
    result = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.xyz")
    assert "Bearer" not in result or "TOKEN_REDACTED" in result
    assert "eyJhbGciOiJIUzI1NiJ9" not in result


def test_redacts_bw_session():
    text = "BW_SESSION=abc123xyz789def456ghi012jkl345mno678pqr901stu234vwx"
    assert "BW_SESSION=<REDACTED>" in redact(text)


def test_redacts_private_key():
    pem = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7
-----END PRIVATE KEY-----"""
    assert "<PRIVATE_KEY_REDACTED>" in redact(pem)


def test_redact_preserves_safe_text():
    safe = "The quick brown fox jumps over the lazy dog."
    assert redact(safe) == safe


def test_redact_none_returns_none():
    assert redact("") == ""
    assert redact(None) is None  # type: ignore[arg-type]


def test_all_patterns_compile():
    """Every SECRET_PATTERNS tuple must have a compilable regex."""
    import re

    for pattern, _ in SECRET_PATTERNS:
        re.compile(pattern)


# ── Fingerprint Tests ──────────────────────────────────────────────────


def test_fingerprint_no_raw_secret_in_output():
    secret = "sk-test-secret-value-12345"
    fp, last4 = secret_fingerprint(secret)
    assert secret not in fp
    assert secret not in last4
    assert fp.startswith("sha256:")
    assert len(fp) == 19  # "sha256:" + 12 hex chars


def test_fingerprint_last4():
    _, last4 = secret_fingerprint("sk-abc123xyz789def456ghi012jkl345mno678Ab7Q")
    assert last4 == "Ab7Q"


def test_fingerprint_empty():
    fp, last4 = secret_fingerprint("")
    assert fp == "sha256:empty"
    assert last4 == ""


def test_fingerprint_short_value():
    _, last4 = secret_fingerprint("ab")
    assert last4 == "ab"


def test_fingerprint_deterministic():
    secret = "my-test-secret"
    _fp1, l1 = secret_fingerprint(secret)
    _fp2, l2 = secret_fingerprint(secret)
    assert _fp1 == _fp2
    assert l1 == l2


# ── File Permission Tests ──────────────────────────────────────────────


def test_ensure_dir_700():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "secret-dir")
        ensure_dir_700(path)
        actual = stat.S_IMODE(os.stat(path).st_mode)
        assert actual == 0o700


def test_ensure_file_600():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "secret.env")
        with open(path, "w") as f:
            f.write("KEY=value")
        os.chmod(path, 0o644)
        ensure_file_600(path)
        actual = stat.S_IMODE(os.stat(path).st_mode)
        assert actual == 0o600


def test_verify_permissions_safe():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "safe.env")
        with open(path, "w") as f:
            f.write("KEY=value")
        os.chmod(path, 0o600)
        assert verify_permissions(path, 0o600)
        assert not verify_permissions(path, 0o644)


def test_check_env_file_safe():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, ".env")
        with open(path, "w") as f:
            f.write("KEY=value")
        os.chmod(path, 0o600)
        result = check_env_file(path)
        assert result["safe"]
        assert result["mode"] == "600"


def test_check_env_file_unsafe():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, ".env-unsafe")
        with open(path, "w") as f:
            f.write("KEY=value")
        os.chmod(path, 0o644)
        result = check_env_file(path)
        assert not result["safe"]
        issue: str = str(result.get("issue", ""))
        assert "600" in issue


# ── Secret Scanner Tests ───────────────────────────────────────────────


def test_scan_clean_content():
    clean, violations = scan_for_secrets("This is safe documentation text.")
    assert clean
    assert len(violations) == 0


def test_scan_detects_api_key():
    clean, violations = scan_for_secrets(
        "My key is sk-abc123xyz789def456ghi012jkl345mno678pqr"
    )
    assert not clean
    assert len(violations) > 0


def test_scan_detects_bearer():
    clean, _ = scan_for_secrets("Authorization: Bearer tok123")
    assert not clean


def test_forbidden_blocks_detect_env_assignment():
    clean, violations = scan_for_forbidden_blocks("OPENAI_API_KEY=sk-xxx")
    assert not clean
    assert any("env KEY assignment" in v for v in violations)


def test_forbidden_blocks_detect_private_key():
    clean, _ = scan_for_forbidden_blocks(
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    )
    assert not clean


def test_assert_clean_raises_on_secret():
    try:
        assert_clean("sk-abc123xyz789def456ghi012jkl345mno678pqr", sink="test")
        assert False, "should have raised"
    except UnsafeSecretWriteError:
        pass


def test_assert_clean_passes_safe():
    assert_clean("Safe content", sink="test")


# ── Atomic Write Tests ─────────────────────────────────────────────────


def test_atomic_write_creates_file():
    from env.atomic_write import atomic_write  # pyright: ignore[reportMissingImports]

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "generated.env")
        atomic_write(path, "KEY=value\n")
        assert os.path.exists(path)
        assert open(path).read() == "KEY=value\n"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_atomic_write_no_tmp_leftover():
    from env.atomic_write import atomic_write  # pyright: ignore[reportMissingImports]

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "generated.env")
        atomic_write(path, "KEY=value\n")
        assert not os.path.exists(path + ".tmp")


# ── Env Render Tests ───────────────────────────────────────────────────


def test_env_projection_mapping_complete():
    """Every env var in the projection YAML has a corresponding secret ref."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "env_projection.yaml",
    )
    with open(config_path) as f:
        content = f.read()

    # Parse manually (avoids PyYAML dependency)
    projection: dict[str, str] = {}
    in_projection = False
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "env_projection:":
            in_projection = True
            continue
        if in_projection:
            # Exit if we hit a non-indented top-level key
            if not line.startswith(" ") and not line.startswith("\t"):
                if ":" in stripped:
                    break  # next top-level section
            if ":" in stripped and not stripped.startswith("#"):
                key, _, val = stripped.partition(":")
                key = key.strip().strip('"')
                val = val.strip().strip('"').strip("'")
                if key and val and not val.startswith("#"):
                    projection[key] = val

    assert len(projection) >= 15  # 15 unique env var → ref mappings
    for env_var, ref in projection.items():
        assert ref.startswith("hermes/"), f"{env_var} → {ref} must start with hermes/"
        assert "/" in ref, f"{env_var} → {ref} must follow hermes/<provider>/<key>"


# ── Error Hierarchy Tests ──────────────────────────────────────────────


def test_error_hierarchy():
    from security.secret_backend import (  # pyright: ignore[reportMissingImports]
        HermesKeyRotateError,
        SecretBackendError,
        VaultwardenAuthError,
        UnsafeSecretWriteError,
    )

    assert issubclass(SecretBackendError, HermesKeyRotateError)
    assert issubclass(VaultwardenAuthError, SecretBackendError)
    assert issubclass(UnsafeSecretWriteError, HermesKeyRotateError)
    assert not issubclass(UnsafeSecretWriteError, SecretBackendError)
