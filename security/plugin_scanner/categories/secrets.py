"""Hermes Ops Kit — Plugin Scanner: Secrets Category.

Detects hardcoded credentials, API keys, tokens, and session keys
in plugin files. Reuses security/redaction.py SECRET_PATTERNS and
optionally integrates gitleaks as a subprocess tool.

Gracefully degrades if gitleaks is not installed.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from typing import Any

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)

from security.redaction import SECRET_PATTERNS, redact  # pyright: ignore[reportMissingImports]
from security.plugin_scanner.findings import (  # pyright: ignore[reportMissingImports]
    Finding,
    RiskLevel,
    Severity,
    ScanCategory,
)


# ── File Classification & Entropy Utilities ───────────────────────────

# Documentation file extensions and names — findings in these files
# are downgraded one severity level because example patterns are expected.
_DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".txt", ".adoc"})
_DOC_NAMES: frozenset[str] = frozenset(
    {
        "SKILL.md",
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "GEMINI.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
    }
)
_TEST_DIR_MARKERS: tuple[str, ...] = ("tests/", "test/", "spec/")

# Sequential/dummy pattern indicators that suggest a fake/test secret
_DUMMY_SEQUENCES: tuple[str, ...] = (
    "abc",
    "123",
    "xyz",
    "def",
    "ghi",
    "jkl",
    "mno",
    "pqr",
    "stu",
    "vwx",
    "abc123",
    "xyz789",
    "def456",
    "ghi012",
    "jkl345",
    "mno678",
    "pqr901",
)
_DUMMY_WORDS: tuple[str, ...] = (
    "example",
    "dummy",
    "placeholder",
    "xxx",
    "your-",
    "test-key",
    "sk-...",
    "REDACTED",
    "YOUR_",
    "changeme",
    "replace",
    "<KEY>",
    "<TOKEN>",
    "leak",
)

# Entropy threshold: secrets with Shannon entropy < this are likely
# test fixtures or predictable patterns, not real credentials.
_LOW_ENTROPY_THRESHOLD = 3.2  # bits per character


def _is_doc_file(file_path: str) -> bool:
    """Check if a file is documentation (markdown, readme, changelog, etc.)."""
    base = os.path.basename(file_path)
    if base in _DOC_NAMES:
        return True
    ext = os.path.splitext(file_path)[1].lower()
    return ext in _DOC_EXTENSIONS


def _is_test_file(file_path: str) -> bool:
    """Check if a file is in or under a test directory anywhere in its path.

    Matches both top-level (``tests/test_foo.py``) and nested
    (``src/tests/foo.py``, ``lib/test/bar.py``) test directories.
    """
    normalized = file_path.replace("\\", "/")
    segments = normalized.split("/")
    # Check any path segment (except the filename) is a test dir
    for segment in segments[:-1]:
        if segment in ("test", "tests", "spec"):
            return True
    return False


def _file_class(file_path: str) -> str:
    """Classify a file: 'test', 'doc', or 'code'."""
    if _is_test_file(file_path):
        return "test"
    if _is_doc_file(file_path):
        return "doc"
    return "code"


def _shannon_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string in bits per character.

    Returns 0.0 for empty strings.
    """
    if not text:
        return 0.0
    n = len(text)
    counts = Counter(text)
    entropy = 0.0
    for count in counts.values():
        p = count / n
        entropy -= p * math.log2(p)
    return entropy


def _has_sequential_patterns(text: str) -> bool:
    """Check if text contains sequential/dummy patterns suggesting a fake secret.

    Returns True if the text looks like a test fixture or placeholder,
    False if it could be a real secret.
    """
    lower = text.lower()
    # Check for dummy words
    for word in _DUMMY_WORDS:
        if word.lower() in lower:
            return True
    # Check for sequential alphanumeric patterns (e.g., abc123, def456)
    seq_count = sum(1 for s in _DUMMY_SEQUENCES if s in lower)
    return seq_count >= 2


def _is_likely_fake_secret(matched_text: str) -> tuple[bool, str]:
    """Determine if a matched string is likely a fake/test secret.

    Returns:
        (is_fake: bool, reason: str)
    """
    # Check 1: Dummy words/patterns
    if _has_sequential_patterns(matched_text):
        return True, "sequential_dummy_patterns"

    # Check 2: Low entropy (real secrets are random)
    entropy = _shannon_entropy(matched_text)
    if entropy < _LOW_ENTROPY_THRESHOLD:
        return True, f"low_entropy_{entropy:.1f}_bits"

    return False, ""


def _downgrade_for_file_class(
    severity: Severity,
    file_path: str,
) -> Severity:
    """Downgrade severity for findings in documentation files.

    Documentation files naturally contain example code with API keys,
    Bearer tokens, and environment variable examples. These should be
    surfaced at lower severity to reduce noise while still being visible.

    Downgrade mapping:
        ERROR   → WARNING  (doc files)
        WARNING → INFO     (doc files)
        INFO    → INFO     (unchanged)

    Test files are NOT downgraded here — that's handled separately
    based on the matched content (entropy + sequential patterns).
    """
    if not _is_doc_file(file_path):
        return severity

    if severity == Severity.ERROR:
        return Severity.WARNING
    if severity == Severity.WARNING:
        return Severity.INFO
    return severity


# ── Plugin Type Detection ──────────────────────────────────────────────


def detect_plugin_type(plugin_path: str) -> str:
    """Detect whether a plugin is a skill (text-heavy) or a code plugin.

    Skills are loaded as AI context — they are primarily markdown files.
    Dangerous patterns in skills are qualitatively different from
    dangerous patterns in executable code:
      - A "hardcoded API key" in a SKILL.md is documentation/example
      - A "prompt injection" pattern in a red-teaming skill is its purpose
      - A "shell execution" pattern in a skill is informational

    Returns:
        'skill' if the plugin is primarily text/markdown (>60% .md files)
        'code'  if the plugin is primarily executable code
    """
    plugin_path = os.path.expanduser(plugin_path)
    if not os.path.isdir(plugin_path):
        return "code"

    total_files = 0
    md_files = 0

    try:
        for root, dirs, files in os.walk(plugin_path):
            # Skip hidden and cache dirs
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and d not in {"__pycache__", "node_modules", ".venv", "venv"}
            ]
            for fname in files:
                if fname.startswith("."):
                    continue
                total_files += 1
                if fname.endswith(".md"):
                    md_files += 1
                # Stop after a reasonable sample
                if total_files > 500:
                    break
            if total_files > 500:
                break
    except OSError:
        return "code"

    if total_files == 0:
        return "code"

    ratio = md_files / total_files
    return "skill" if ratio > 0.6 else "code"


def _downgrade_for_skill(
    severity: Severity,
    risk_level: RiskLevel,
    file_path: str,
) -> tuple[Severity, RiskLevel]:
    """Apply skill-mode downgrades for text-heavy plugins.

    Skills are AI context, not executable code. Patterns that would
    be CRITICAL in a .py file are often documentation/educational in
    a .md skill file.

    Downgrade for skill files:
        ERROR   → WARNING (for .md files), unchanged for others
        WARNING → INFO    (for .md files)
        CRITICAL risk → HIGH or MEDIUM
    """
    if not _is_doc_file(file_path):
        return severity, risk_level

    # In skill .md files, downgrade severity
    if severity == Severity.ERROR:
        return Severity.WARNING, RiskLevel.MEDIUM
    if severity == Severity.WARNING:
        return Severity.INFO, RiskLevel.LOW

    return severity, risk_level


# ── Plugin-Specific Secret Patterns ──────────────────────────────────
#
# Extends the base SECRET_PATTERNS from security/redaction.py with
# additional patterns relevant to plugin security scanning.

PLUGIN_SECRET_PATTERNS: list[tuple[str, str, str, RiskLevel]] = [
    # (regex, name, description, risk_level)
    # ── Generic API Keys / Tokens ──
    (
        r"(?:api[_-]?key|apikey|API_KEY)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
        "hardcoded-api-key",
        "Hardcoded API key in source code",
        RiskLevel.CRITICAL,
    ),
    (
        r"(?:token|TOKEN)\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{20,}['\"]",
        "hardcoded-token",
        "Hardcoded access token in source code",
        RiskLevel.CRITICAL,
    ),
    (
        r"(?:password|passwd|PASSWORD)\s*[:=]\s*['\"][^'\"]{4,}['\"]",
        "hardcoded-password",
        "Hardcoded password in source code",
        RiskLevel.CRITICAL,
    ),
    # ── Bitwarden / Vaultwarden Access ──
    (
        r"BW_SESSION\s*[:=]\s*['\"][A-Za-z0-9+/=]{20,}['\"]",
        "bw-session-exposure",
        "Bitwarden session key in source code",
        RiskLevel.CRITICAL,
    ),
    (
        r"VAULTWARDEN_PASSWORD\s*[:=]\s*['\"][^'\"]{4,}['\"]",
        "vaultwarden-password-exposure",
        "Vaultwarden master password in source code",
        RiskLevel.CRITICAL,
    ),
    # ── Private Keys ──
    (
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY",
        "private-key-exposure",
        "Private key material in plugin files",
        RiskLevel.CRITICAL,
    ),
    # ── Environment File Secrets ──
    (
        r"HERMES_[A-Z_]+(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL)\s*=\s*[^\n]{4,}",
        "hermes-env-secret",
        "Hermes secret-bearing environment variable (KEY/TOKEN/SECRET/PASSWORD/AUTH/CREDENTIAL)",
        RiskLevel.HIGH,
    ),
    # Catch-all for any other HERMES_* env var with a value that looks like a secret
    # (20+ alphanumeric/hyphen/underscore chars — avoids flagging path assignments)
    (
        r"HERMES_[A-Z_]+\s*=\s*['\"]?[A-Za-z0-9+/=_-]{20,}['\"]?",
        "hermes-env-secret-generic",
        "Hermes environment variable with potential secret value (generic match)",
        RiskLevel.MEDIUM,
    ),
    (
        r"(?:SECRET|secret)\s*[:=]\s*['\"][A-Za-z0-9+/=]{16,}['\"]",
        "generic-secret",
        "Generic secret value in source code",
        RiskLevel.HIGH,
    ),
    # ── Connection Strings ──
    (
        r"(?:mongodb|postgresql|mysql|redis)://[^/\s]+:[^/\s]+@",
        "connection-string-credentials",
        "Database connection string with embedded credentials",
        RiskLevel.HIGH,
    ),
    # ── Webhook URLs with Secrets ──
    (
        r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+",
        "slack-webhook-exposure",
        "Slack webhook URL in source code",
        RiskLevel.HIGH,
    ),
    (
        r"https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+",
        "discord-webhook-exposure",
        "Discord webhook URL in source code",
        RiskLevel.HIGH,
    ),
]


def _gitleaks_available() -> bool:
    """Check if gitleaks is installed and executable."""
    try:
        result = subprocess.run(
            ["gitleaks", "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _run_gitleaks(plugin_path: str) -> list[dict[str, Any]]:
    """Run gitleaks detect on a plugin path. Returns list of finding dicts."""
    # Validate path before passing to subprocess
    if not plugin_path or not os.path.isdir(os.path.expanduser(plugin_path)):
        return []
    plugin_path = os.path.abspath(os.path.realpath(os.path.expanduser(plugin_path)))

    try:
        result = subprocess.run(
            [
                "gitleaks",
                "detect",
                "--source",
                plugin_path,
                "--no-git",
                "--format=json",
                "--verbose",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return []
        # gitleaks exits 1 when leaks found
        try:
            findings = json.loads(result.stdout)
            return findings if isinstance(findings, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


def _scan_file_regex(file_path: str, rel_path: str, plugin_name: str) -> list[Finding]:
    """Scan a single file with regex patterns.

    Uses both base SECRET_PATTERNS (redaction) and PLUGIN_SECRET_PATTERNS.
    """
    findings: list[Finding] = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, PermissionError):
        return findings

    lines = content.split("\n")

    fclass = _file_class(rel_path)

    # Check base SECRET_PATTERNS
    for pattern, replacement in SECRET_PATTERNS:
        for i, line in enumerate(lines, start=1):
            match = re.search(pattern, line)
            if match:
                matched_text = match.group(0)
                evidence = redact(matched_text)
                if evidence == matched_text:
                    evidence = replacement

                # ── Entropy & dummy-pattern checks ──────────────
                is_fake, fake_reason = _is_likely_fake_secret(matched_text)

                # Determine severity and risk with downgrades
                if is_fake:
                    # Fake/dummy secret → downgrade to INFO
                    severity = Severity.INFO
                    risk = RiskLevel.LOW
                    downgrade_note = f" [downgraded: {fake_reason}]"
                elif fclass == "test":
                    # Test file without dummy indicators → still downgrade
                    severity = Severity.WARNING
                    risk = RiskLevel.MEDIUM
                    downgrade_note = " [downgraded: test_file]"
                else:
                    severity = _downgrade_for_file_class(Severity.ERROR, rel_path)
                    if severity != Severity.ERROR:
                        risk = RiskLevel.HIGH
                        downgrade_note = " [downgraded: doc_file]"
                    else:
                        risk = RiskLevel.CRITICAL
                        downgrade_note = ""

                findings.append(
                    Finding(
                        id="",
                        plugin_name=plugin_name,
                        category=ScanCategory.SECRETS.value,
                        rule="base-secret-pattern",
                        severity=severity,
                        risk_level=risk,
                        file_path=rel_path,
                        line=i,
                        message=f"Secret matched by base redaction pattern{downgrade_note}",
                        evidence=evidence,
                        remediation="Remove the hardcoded secret and use the Bitwarden/Vaultwarden secret backend instead.",
                        metadata={
                            "file_class": fclass,
                            "entropy": round(_shannon_entropy(matched_text), 2),
                            "fake_reason": fake_reason if is_fake else "",
                        },
                    )
                )
                break  # One finding per base pattern per file (dedup via pattern-level)

    # Check plugin-specific patterns
    for pattern, rule_name, description, risk_level in PLUGIN_SECRET_PATTERNS:
        for i, line in enumerate(lines, start=1):
            match = re.search(pattern, line)
            if match:
                matched_text = match.group(0)
                evidence = redact(matched_text)

                # Apply doc-mode and test-file downgrades for
                # hardcoded-api-key / hardcoded-token rules
                is_doc = _is_doc_file(rel_path)
                is_test = fclass == "test"
                is_fake, fake_reason = _is_likely_fake_secret(matched_text)

                if is_fake and is_test:
                    # Test file with dummy patterns → INFO
                    sev = Severity.INFO
                    rl = RiskLevel.LOW
                    downgrade_note = f" [downgraded: test_dummy_{fake_reason}]"
                elif is_test and rule_name in ("hardcoded-api-key", "hardcoded-token"):
                    # Test file, key/token pattern → INFO
                    sev = Severity.INFO
                    rl = RiskLevel.LOW
                    downgrade_note = " [downgraded: test_file]"
                elif is_doc:
                    # Doc file → downgrade one level
                    base_sev = (
                        Severity.ERROR
                        if risk_level == RiskLevel.CRITICAL
                        else Severity.WARNING
                    )
                    sev = _downgrade_for_file_class(base_sev, rel_path)
                    rl = RiskLevel.MEDIUM if sev == Severity.WARNING else RiskLevel.LOW
                    downgrade_note = " [downgraded: doc_file]"
                else:
                    sev = (
                        Severity.ERROR
                        if risk_level == RiskLevel.CRITICAL
                        else Severity.WARNING
                    )
                    rl = risk_level
                    downgrade_note = ""

                findings.append(
                    Finding(
                        id="",
                        plugin_name=plugin_name,
                        category=ScanCategory.SECRETS.value,
                        rule=rule_name,
                        severity=sev,
                        risk_level=rl,
                        file_path=rel_path,
                        line=i,
                        message=description + downgrade_note,
                        evidence=evidence,
                        remediation="Remove the hardcoded credential from plugin source code.",
                        metadata={
                            "file_class": fclass,
                            "fake_reason": fake_reason if is_fake else "",
                        },
                    )
                )
                break  # One finding per plugin-specific pattern per file

    return findings


def run(
    plugin_name: str,
    plugin_path: str,
    *,
    use_gitleaks: bool = True,
    skip_patterns: list[str] | None = None,
    plugin_type: str = "code",
) -> list[Finding]:
    """Run the secrets scan category on a plugin.

    Args:
        plugin_name: Name of the plugin.
        plugin_path: Path to the plugin directory. Must be an existing
            directory (absolute or relative). Caller should pre-validate
            that the path exists; this function will silently return empty
            results for nonexistent paths (no crash).
        use_gitleaks: Whether to attempt gitleaks integration.
        skip_patterns: Optional list of rule names to skip.
        plugin_type: 'skill' for text-heavy AI context plugins,
                     'code' for executable plugins. Skills get
                     softer severity because patterns in markdown
                     are documentation, not executable code.

    Returns:
        List of Finding objects. Returns empty list if plugin_path does
        not exist or is not a directory.
    """
    findings: list[Finding] = []
    skip = set(skip_patterns or [])
    skip_dirs = {
        ".git",
        ".github",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
    }
    # Paths to skip entirely (e.g., scanner's own rule definitions)
    _skip_prefixes = ("security/plugin_scanner/rules/",)
    _is_skill = plugin_type == "skill"
    text_extensions = {
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".sh",
        ".bash",
        ".zsh",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".cfg",
        ".ini",
        ".env",
        ".md",
        ".txt",
        ".rst",
        ".Dockerfile",
        ".dockerfile",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".java",
        ".c",
        ".h",
        ".cpp",
    }

    # ── Regex scanning ──────────────────────────────────────────
    for root, dirs, files in os.walk(plugin_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            # Also check full filename for Dockerfile etc.
            if ext not in text_extensions and fname not in (
                "Dockerfile",
                "Makefile",
                "Gemfile",
            ):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, plugin_path)
            # Skip scanner's own rule definitions (they contain example patterns)
            if any(rel.startswith(p) for p in _skip_prefixes):
                continue
            file_findings = _scan_file_regex(fpath, rel, plugin_name)
            for f in file_findings:
                if f.rule not in skip:
                    findings.append(f)

    # ── Optional gitleaks ───────────────────────────────────────
    if use_gitleaks and _gitleaks_available():
        gl_findings = _run_gitleaks(plugin_path)
        for gl in gl_findings:
            findings.append(
                Finding(
                    id="",
                    plugin_name=plugin_name,
                    category=ScanCategory.SECRETS.value,
                    rule="gitleaks:" + gl.get("rule", gl.get("RuleID", "unknown")),
                    severity=Severity.ERROR,
                    risk_level=RiskLevel.CRITICAL,
                    file_path=gl.get("file", gl.get("File", "")),
                    line=int(gl.get("line", gl.get("StartLine", 0)) or 0),
                    message=gl.get(
                        "description",
                        gl.get("Description", "Secret detected by gitleaks"),
                    ),
                    evidence=redact(gl.get("match", gl.get("Secret", ""))),
                    remediation="Remove the detected secret and use the secret backend.",
                )
            )

    # ── Skill-mode downgrade ────────────────────────────────────
    if _is_skill:
        for f in findings:
            sev, risk = _downgrade_for_skill(f.severity, f.risk_level, f.file_path)
            if sev != f.severity:
                f.severity = sev
                f.risk_level = risk
                f.message += " [skill_mode]"

    # ── Deduplicate ─────────────────────────────────────────────
    seen: set[str] = set()
    unique: list[Finding] = []
    for f in findings:
        key = f"{f.rule}:{f.file_path}:{f.line}"
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique
