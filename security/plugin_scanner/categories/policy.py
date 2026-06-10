"""Hermes Ops Kit — Plugin Scanner: Policy Category.

Detects dangerous plugin behavior through:
- AST analysis for Python files (dangerous imports, dynamic execution)
- Regex patterns for shell/markdown/all files
- Optional Semgrep subprocess integration
- Prompt injection patterns in SKILL.md / AGENTS.md / CLAUDE.md

Gracefully degrades if Semgrep is not installed.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from typing import Any

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)

from security.plugin_scanner.findings import (  # pyright: ignore[reportMissingImports]
    Finding,
    RiskLevel,
    Severity,
    ScanCategory,
)


# ── Constants ────────────────────────────────────────────────────────

# Dangerous modules that indicate high-risk behavior
DANGEROUS_IMPORTS: dict[str, tuple[str, RiskLevel]] = {
    "subprocess": ("shell-execution-capability", RiskLevel.HIGH),
    "socket": ("network-access-capability", RiskLevel.HIGH),
    "ctypes": ("native-code-capability", RiskLevel.MEDIUM),
    "multiprocessing": ("process-spawning-capability", RiskLevel.MEDIUM),
    "pickle": ("deserialization-capability", RiskLevel.HIGH),
    "marshal": ("deserialization-capability", RiskLevel.HIGH),
    "code": ("dynamic-code-execution", RiskLevel.HIGH),
    "builtins": ("builtin-manipulation", RiskLevel.HIGH),
}

# Dangerous functions that when called indicate malicious intent
DANGEROUS_CALLS: dict[str, tuple[str, RiskLevel]] = {
    "eval": ("dynamic-code-execution", RiskLevel.HIGH),
    "exec": ("dynamic-code-execution", RiskLevel.HIGH),
    "compile": ("dynamic-code-execution", RiskLevel.MEDIUM),
    "__import__": ("dynamic-import", RiskLevel.HIGH),
}

# Dangerous attribute access chains
DANGEROUS_ATTR_CHAINS: list[tuple[list[str], str, RiskLevel]] = [
    (["os", "system"], "shell-execution", RiskLevel.CRITICAL),
    (["os", "popen"], "shell-execution", RiskLevel.CRITICAL),
    (["subprocess", "call"], "shell-execution", RiskLevel.CRITICAL),
    (["subprocess", "run"], "shell-execution", RiskLevel.HIGH),
    (["subprocess", "Popen"], "shell-execution", RiskLevel.CRITICAL),
    (["subprocess", "check_output"], "shell-execution", RiskLevel.HIGH),
    (["subprocess", "check_call"], "shell-execution", RiskLevel.HIGH),
    (["socket", "create_connection"], "network-access", RiskLevel.HIGH),
    (["socket", "socket"], "network-access", RiskLevel.HIGH),
    (["requests", "post"], "network-access", RiskLevel.MEDIUM),
    (["requests", "get"], "network-access", RiskLevel.LOW),
    (["requests", "put"], "network-access", RiskLevel.MEDIUM),
    (["urllib", "request"], "network-access", RiskLevel.MEDIUM),
    (["http", "client"], "network-access", RiskLevel.MEDIUM),
    (["importlib", "import_module"], "dynamic-import", RiskLevel.HIGH),
    (["shutil", "copy"], "file-system-write", RiskLevel.MEDIUM),
    (["shutil", "move"], "file-system-write", RiskLevel.MEDIUM),
    # Path mutating methods (constructor alone is harmless and not flagged)
    (["pathlib", "Path", "write_text"], "file-system-write", RiskLevel.MEDIUM),
    (["pathlib", "Path", "write_bytes"], "file-system-write", RiskLevel.MEDIUM),
    (["pathlib", "Path", "unlink"], "file-system-write", RiskLevel.MEDIUM),
    (["pathlib", "Path", "chmod"], "file-system-write", RiskLevel.MEDIUM),
    (["pathlib", "Path", "rename"], "file-system-write", RiskLevel.MEDIUM),
    # Note: pathlib.Path() constructor is NOT flagged — only mutating
    # operations. The AST visitor cannot resolve `from pathlib import Path`
    # imports, so the constructor would be unreliable to detect anyway.
    (["base64", "b64decode"], "obfuscation", RiskLevel.MEDIUM),
    (["zlib", "decompress"], "obfuscation", RiskLevel.MEDIUM),
    (["codecs", "decode"], "obfuscation", RiskLevel.MEDIUM),
]

# Regex patterns for dangerous operations in all file types
DANGEROUS_PATTERNS: list[tuple[str, str, str, RiskLevel, Severity]] = [
    # (regex, rule_name, message, risk_level, severity)
    (
        r"os\.system\s*\(",
        "shell-execution",
        "Shell command execution via os.system()",
        RiskLevel.CRITICAL,
        Severity.ERROR,
    ),
    (
        r"subprocess\.(?:call|run|Popen|check_output|check_call)\s*\(",
        "shell-execution",
        "Shell command execution via subprocess",
        RiskLevel.HIGH,
        Severity.ERROR,
    ),
    (
        r"\beval\s*\(.*\)",
        "dynamic-code-execution",
        "Dynamic code evaluation via eval()",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
    (
        r"\bexec\s*\(.*\)",
        "dynamic-code-execution",
        "Dynamic code execution via exec()",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
    (
        r"\bcompile\s*\(",
        "dynamic-code-execution",
        "Code compilation via compile()",
        RiskLevel.MEDIUM,
        Severity.WARNING,
    ),
    (
        r"__import__\s*\(",
        "dynamic-import",
        "Dynamic module import via __import__()",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
    (
        r"importlib\.import_module\s*\(",
        "dynamic-import",
        "Dynamic module import via importlib",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
    (
        r"socket\.socket\s*\(.*AF_INET",
        "network-access",
        "Network socket creation",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
    (
        r"socket\.create_connection\s*\(",
        "network-access",
        "Network connection creation",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
    (
        r"requests\.(?:post|put|delete|patch)\s*\(.*https?://",
        "network-exfiltration",
        "HTTP request to external URL",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
    (
        r"curl\s+.*\|.*(?:bash|sh|zsh)",
        "curl-pipe-shell",
        "curl piped to shell — remote code execution risk",
        RiskLevel.CRITICAL,
        Severity.ERROR,
    ),
    (
        r"wget\s+.*\|.*(?:bash|sh|zsh)",
        "wget-pipe-shell",
        "wget piped to shell — remote code execution risk",
        RiskLevel.CRITICAL,
        Severity.ERROR,
    ),
    (
        r"pip\s+install\s+(?!-r|--requirement)",
        "pip-install-hidden",
        "Hidden pip install in setup scripts",
        RiskLevel.CRITICAL,
        Severity.ERROR,
    ),
    (
        r"npm\s+install\s+-g",
        "npm-global-install",
        "Global npm install in setup scripts",
        RiskLevel.HIGH,
        Severity.WARNING,
    ),
]

# Environment variable access patterns
ENV_ACCESS_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, rule_name, message)
    (
        r"(?:os\.environ(?:\.get)?|os\.getenv)\s*\(\s*['\"]\s*(?:BW_SESSION|BW_CLIENTSECRET|BW_PASSWORD|BW_CLIENTID)",
        "env-bw-access",
        "Attempt to read Bitwarden/Vaultwarden session from environment",
    ),
    (
        r"(?:os\.environ(?:\.get)?|os\.getenv)\s*\(\s*['\"]\s*VAULTWARDEN",
        "env-vaultwarden-access",
        "Attempt to read Vaultwarden configuration from environment",
    ),
    (
        r"(?:os\.environ(?:\.get)?|os\.getenv)\s*\(\s*['\"]\s*HERMES_",
        "env-hermes-access",
        "Attempt to read Hermes environment variables",
    ),
    (
        r"open\s*\(\s*os\.path\.expanduser\s*\(\s*['\"]~/\.hermes",
        "hermes-dir-access",
        "Attempt to access ~/.hermes/ directory",
    ),
    (
        r"open\s*\(\s*.*\.env['\"]",
        "dotenv-access",
        "Attempt to read .env files",
    ),
]

# Prompt injection patterns for markdown files
PROMPT_INJECTION_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, rule_name, message)
    (
        r"ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|directives?|commands?|context)",
        "prompt-injection-ignore",
        "Prompt injection: 'ignore previous instructions' pattern",
    ),
    (
        r"(?:you\s+are\s+(?:now|no\s+longer)|forget\s+(?:everything|all)|disregard\s+(?:all|previous))",
        "prompt-injection-role",
        "Prompt injection: role reassignment pattern",
    ),
    (
        r"(?:system\s*(?:prompt|message|instruction)|override\s*(?:system|safety|guard))",
        "prompt-injection-system",
        "Prompt injection: system prompt override attempt",
    ),
    (
        r"\[INST\].*\[/INST\]",
        "prompt-injection-tags",
        "Prompt injection: special instruction tags",
    ),
    (
        r"<\|im_start\|>|<\|im_end\|>",
        "prompt-injection-tokens",
        "Prompt injection: chat template tokens",
    ),
    (
        r"DO\s+NOT\s+(?:FOLLOW|OBEY|LISTEN)",
        "prompt-injection-negation",
        "Prompt injection: negative instruction override",
    ),
]


# ── AST Analysis ─────────────────────────────────────────────────────


class _PolicyASTVisitor(ast.NodeVisitor):
    """Walk a Python AST looking for dangerous patterns."""

    def __init__(self, plugin_name: str, file_path: str) -> None:
        self.plugin_name = plugin_name
        self.file_path = file_path
        self.findings: list[Finding] = []

    def _make_finding(
        self,
        rule: str,
        message: str,
        risk_level: RiskLevel,
        severity: Severity,
        line: int = 0,
    ) -> Finding:
        return Finding(
            id="",
            plugin_name=self.plugin_name,
            category=ScanCategory.POLICY.value,
            rule=rule,
            severity=severity,
            risk_level=risk_level,
            file_path=self.file_path,
            line=line,
            message=message,
            remediation="Review whether this capability is required. If not, remove it.",
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base_module = alias.name.split(".")[0]
            if base_module in DANGEROUS_IMPORTS:
                rule, risk = DANGEROUS_IMPORTS[base_module]
                self.findings.append(
                    self._make_finding(
                        rule=rule,
                        message=f"Dangerous import: '{alias.name}' — {rule}",
                        risk_level=risk,
                        severity=(
                            Severity.ERROR
                            if risk == RiskLevel.CRITICAL
                            else Severity.WARNING
                        ),
                        line=node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            self.generic_visit(node)
            return
        base_module = node.module.split(".")[0]
        if base_module in DANGEROUS_IMPORTS:
            rule, risk = DANGEROUS_IMPORTS[base_module]
            self.findings.append(
                self._make_finding(
                    rule=rule,
                    message=f"Dangerous import from '{node.module}': {', '.join(a.name for a in node.names)}",
                    risk_level=risk,
                    severity=(
                        Severity.ERROR
                        if risk == RiskLevel.CRITICAL
                        else Severity.WARNING
                    ),
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check for direct dangerous function calls
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in DANGEROUS_CALLS:
                rule, risk = DANGEROUS_CALLS[func_name]
                self.findings.append(
                    self._make_finding(
                        rule=rule,
                        message=f"Dangerous function call: {func_name}()",
                        risk_level=risk,
                        severity=Severity.ERROR,
                        line=node.lineno,
                    )
                )

        # Check for dangerous attribute chains (e.g., os.system, subprocess.run)
        chain = _resolve_attr_chain(node.func)
        if chain:
            chain_key = ".".join(chain)
            for attr_chain, rule, risk in DANGEROUS_ATTR_CHAINS:
                attr_key = ".".join(attr_chain)
                if chain_key == attr_key or chain_key.startswith(attr_key + "."):
                    self.findings.append(
                        self._make_finding(
                            rule=rule,
                            message=f"Dangerous call: {chain_key}()",
                            risk_level=risk,
                            severity=Severity.ERROR,
                            line=node.lineno,
                        )
                    )
                    break

        self.generic_visit(node)


def _resolve_attr_chain(node: ast.expr) -> list[str] | None:
    """Resolve an attribute chain like os.path.join or subprocess.run."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return parts
    return None


def _scan_python_ast(file_path: str, rel_path: str, plugin_name: str) -> list[Finding]:
    """AST-analyze a single Python file."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, OSError, PermissionError):
        return []

    visitor = _PolicyASTVisitor(plugin_name, rel_path)
    visitor.visit(tree)
    return visitor.findings


# ── Regex File Scanning ──────────────────────────────────────────────


def _scan_file_regex_policy(
    file_path: str, rel_path: str, plugin_name: str
) -> list[Finding]:
    """Scan a single file with policy regex patterns."""
    findings: list[Finding] = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, PermissionError):
        return findings

    lines = content.split("\n")

    # Check dangerous patterns
    is_python_file = file_path.endswith(".py")
    for pattern, rule, message, risk, severity in DANGEROUS_PATTERNS:
        # Skip pip-install-hidden in Python files — "pip install" in a .py
        # file is almost always a docstring or comment instructing the user,
        # not an executable shell command.
        if rule == "pip-install-hidden" and is_python_file:
            continue
        # Skip curl-pipe-shell and wget-pipe-shell in Python files — same
        # rationale: these are informational examples in docstrings/comments.
        if rule in ("curl-pipe-shell", "wget-pipe-shell") and is_python_file:
            continue
        for i, line in enumerate(lines, start=1):
            if re.search(pattern, line):
                findings.append(
                    Finding(
                        id="",
                        plugin_name=plugin_name,
                        category=ScanCategory.POLICY.value,
                        rule=rule,
                        severity=severity,
                        risk_level=risk,
                        file_path=rel_path,
                        line=i,
                        message=message,
                        evidence=line.strip()[:200],
                        remediation="Remove or justify this dangerous pattern.",
                    )
                )
                break  # One finding per dangerous pattern per file
                # Rationale: these are broad pattern classes; reporting once
                # per file keeps output actionable. Caller deduplicates.

    # Check env access patterns
    for pattern, rule, message in ENV_ACCESS_PATTERNS:
        for i, line in enumerate(lines, start=1):
            if re.search(pattern, line):
                findings.append(
                    Finding(
                        id="",
                        plugin_name=plugin_name,
                        category=ScanCategory.POLICY.value,
                        rule=rule,
                        severity=Severity.ERROR,
                        risk_level=RiskLevel.HIGH,
                        file_path=rel_path,
                        line=i,
                        message=message,
                        evidence=line.strip()[:200],
                        remediation="Plugins should not access Hermes/Bitwarden environment variables directly.",
                    )
                )
                break  # One finding per env-access pattern per file

    return findings


def _scan_markdown_injection(
    file_path: str, rel_path: str, plugin_name: str
) -> list[Finding]:
    """Scan markdown files for prompt injection patterns."""
    findings: list[Finding] = []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except (OSError, PermissionError):
        return findings

    lines = content.split("\n")

    for pattern, rule, message in PROMPT_INJECTION_PATTERNS:
        for i, line in enumerate(lines, start=1):
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                findings.append(
                    Finding(
                        id="",
                        plugin_name=plugin_name,
                        category=ScanCategory.POLICY.value,
                        rule=rule,
                        severity=Severity.WARNING,
                        risk_level=RiskLevel.MEDIUM,
                        file_path=rel_path,
                        line=i,
                        message=message,
                        evidence=line.strip()[:200],
                        remediation="Review this instruction for prompt injection risk.",
                    )
                )
                break  # One finding per prompt-injection pattern per file
                # Rationale: these are broad text patterns; reporting once per
                # file prevents flooding when a markdown file repeats phrases.

    return findings


# ── Path Safety ───────────────────────────────────────────────────────

_SHELL_META_CHARS = re.compile(r"[;&|`$><\n]")


def _validate_plugin_path(plugin_path: str) -> str:
    """Validate and normalize a plugin path for safe subprocess use.

    Returns the resolved absolute path, or raises ValueError.
    """
    if not plugin_path or not isinstance(plugin_path, str):
        raise ValueError("Plugin path must be a non-empty string")
    if "\x00" in plugin_path:
        raise ValueError("Plugin path contains null bytes")
    if _SHELL_META_CHARS.search(plugin_path):
        raise ValueError("Plugin path contains shell metacharacters")
    resolved = os.path.abspath(os.path.realpath(os.path.expanduser(plugin_path)))
    if not os.path.exists(resolved):
        raise ValueError(f"Plugin path does not exist: {resolved}")
    if not os.path.isdir(resolved):
        raise ValueError(f"Plugin path is not a directory: {resolved}")
    return resolved


# ── Semgrep Integration ──────────────────────────────────────────────


def _semgrep_available() -> bool:
    """Check if semgrep is installed."""
    try:
        result = subprocess.run(
            ["semgrep", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _run_semgrep(plugin_path: str, rules_paths: list[str]) -> list[dict[str, Any]]:
    """Run semgrep with given rule paths, return findings."""
    if not rules_paths:
        return []

    try:
        plugin_path = _validate_plugin_path(plugin_path)
    except ValueError:
        return []

    cmd = [
        "semgrep",
        "scan",
        "--json",
        "--no-git-ignore",
        "--max-target-bytes",
        "5000000",
        "--exclude", ".venv",
        "--exclude", "venv",
        "--exclude", "__pycache__",
        "--exclude", "node_modules",
        "--exclude", ".git",
        "--exclude", ".tox",
    ]
    for rp in rules_paths:
        if os.path.exists(rp):
            cmd.extend(["--config", rp])
    cmd.append(plugin_path)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode not in (0, 1):
            return []
        try:
            data = json.loads(result.stdout)
            return data.get("results", []) if isinstance(data, dict) else []
        except (json.JSONDecodeError, ValueError):
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


def _semgrep_findings_to_our_model(
    semgrep_results: list[dict[str, Any]],
    plugin_name: str,
) -> list[Finding]:
    """Convert semgrep JSON results to our Finding model."""
    findings: list[Finding] = []
    for result in semgrep_results:
        check_id = result.get("check_id", "unknown")
        severity_str = result.get("extra", {}).get("severity", "WARNING")
        message = result.get("extra", {}).get("message", "")
        path = result.get("path", "")
        line = int(result.get("start", {}).get("line", 0) or 0)

        # Map semgrep severity to our model
        sev_map = {
            "ERROR": Severity.ERROR,
            "WARNING": Severity.WARNING,
            "INFO": Severity.INFO,
        }
        severity = sev_map.get(severity_str.upper(), Severity.WARNING)

        # Map to risk level
        if severity == Severity.ERROR:
            risk = RiskLevel.HIGH
        elif severity == Severity.WARNING:
            risk = RiskLevel.MEDIUM
        else:
            risk = RiskLevel.LOW

        findings.append(
            Finding(
                id="",
                plugin_name=plugin_name,
                category=ScanCategory.POLICY.value,
                rule=f"semgrep:{check_id}",
                severity=severity,
                risk_level=risk,
                file_path=path,
                line=line,
                message=message or f"Semgrep finding: {check_id}",
                remediation="Review and address this Semgrep finding.",
            )
        )
    return findings


# ── Bandit Integration (Optional) ─────────────────────────────────────


def _bandit_available() -> bool:
    """Check if bandit is installed."""
    try:
        result = subprocess.run(
            ["bandit", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _run_bandit(plugin_path: str) -> list[dict[str, Any]]:
    """Run bandit on a plugin path. Returns list of result dicts."""
    try:
        plugin_path = _validate_plugin_path(plugin_path)
    except ValueError:
        return []

    try:
        result = subprocess.run(
            [
                "bandit",
                "-r",
                plugin_path,
                "-f",
                "json",
                "-ll",  # Low severity threshold
                "--quiet",
                "--exclude", ".venv,venv,__pycache__,node_modules,.git,.tox",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode not in (0, 1):
            return []
        try:
            data = json.loads(result.stdout)
            return data.get("results", []) if isinstance(data, dict) else []
        except (json.JSONDecodeError, ValueError):
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


def _bandit_findings_to_our_model(
    bandit_results: list[dict[str, Any]],
    plugin_name: str,
) -> list[Finding]:
    """Convert bandit JSON results to our Finding model."""
    findings: list[Finding] = []
    severity_map = {
        "HIGH": (Severity.ERROR, RiskLevel.HIGH),
        "MEDIUM": (Severity.WARNING, RiskLevel.MEDIUM),
        "LOW": (Severity.INFO, RiskLevel.LOW),
    }
    for result in bandit_results:
        test_id = result.get("test_id", "unknown")
        test_name = result.get("test_name", "")
        sev_str = result.get("issue_severity", "LOW")
        sev, risk = severity_map.get(sev_str, (Severity.INFO, RiskLevel.LOW))
        fname = result.get("filename", "")
        line = result.get("line_number", 0) or 0
        text = result.get("issue_text", "")

        findings.append(
            Finding(
                id="",
                plugin_name=plugin_name,
                category=ScanCategory.POLICY.value,
                rule=f"bandit:{test_id}",
                severity=sev,
                risk_level=risk,
                file_path=fname,
                line=line,
                message=f"{test_name}: {text}" if test_name else text,
                remediation="Review and address this Bandit security finding.",
            )
        )
    return findings


# ── Main Entry Point ─────────────────────────────────────────────────


def run(
    plugin_name: str,
    plugin_path: str,
    *,
    use_semgrep: bool = True,
    use_bandit: bool = True,
    semgrep_rules: list[str] | None = None,
    skip_patterns: list[str] | None = None,
    plugin_type: str = "code",
) -> list[Finding]:
    """Run the policy scan category on a plugin.

    Args:
        plugin_name: Name of the plugin.
        plugin_path: Absolute path to the plugin directory.
        use_semgrep: Whether to attempt Semgrep integration.
        use_bandit: Whether to attempt Bandit integration.
        semgrep_rules: Optional list of semgrep rule file paths.
        skip_patterns: Optional list of rule names to skip.
        plugin_type: ``"code"`` (default) for executable plugins,
                     ``"skill"`` for text-heavy AI context plugins.
                     Skills get softer severity for prompt-injection
                     and doc-example patterns.

    Returns:
        List of Finding objects.
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
    # Scanner's own rule definitions contain example patterns — skip
    _skip_prefixes = ("security/plugin_scanner/rules/",)
    markdown_names = {"SKILL.md", "AGENTS.md", "CLAUDE.md", "README.md", "GEMINI.md"}

    # ── File-by-file scanning ───────────────────────────────────
    for root, dirs, files in os.walk(plugin_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, plugin_path)

            # Skip scanner's own rule definitions
            if any(rel.startswith(p) for p in _skip_prefixes):
                continue

            # Python AST analysis
            if fname.endswith(".py"):
                ast_findings = _scan_python_ast(fpath, rel, plugin_name)
                for f in ast_findings:
                    if f.rule not in skip:
                        findings.append(f)

            # Regex policy scanning (all text files)
            ext = os.path.splitext(fname)[1].lower()
            text_exts = {
                ".py",
                ".js",
                ".ts",
                ".sh",
                ".bash",
                ".yaml",
                ".yml",
                ".json",
                ".toml",
                ".cfg",
                ".ini",
            }
            if ext in text_exts or fname in ("Dockerfile", "Makefile", "Gemfile"):
                regex_findings = _scan_file_regex_policy(fpath, rel, plugin_name)
                for f in regex_findings:
                    if f.rule not in skip:
                        findings.append(f)

            # Markdown prompt injection scanning
            if fname.endswith(".md") or fname in markdown_names:
                md_findings = _scan_markdown_injection(fpath, rel, plugin_name)
                for f in md_findings:
                    if f.rule not in skip:
                        findings.append(f)

    # ── Optional Semgrep ─────────────────────────────────────────
    if use_semgrep and _semgrep_available():
        rules = semgrep_rules or _default_semgrep_rules()
        sg_results = _run_semgrep(plugin_path, rules)
        sg_findings = _semgrep_findings_to_our_model(sg_results, plugin_name)
        for f in sg_findings:
            if f.rule not in skip:
                findings.append(f)

    # ── Optional Bandit ──────────────────────────────────────────
    if use_bandit and _bandit_available():
        b_results = _run_bandit(plugin_path)
        b_findings = _bandit_findings_to_our_model(b_results, plugin_name)
        for f in b_findings:
            if f.rule not in skip:
                findings.append(f)

    # ── Skill-mode downgrade ──────────────────────────────────────
    if plugin_type == "skill":
        _prompt_rules = {
            "prompt-injection-system",
            "prompt-injection-role",
            "prompt-injection-ignore",
            "prompt-injection-negation",
            "prompt-injection-tags",
            "prompt-injection-tokens",
        }
        for f in findings:
            # Prompt injection in a skill is the skill's TOPIC,
            # not an attack on the system prompt
            if f.rule in _prompt_rules:
                f.severity = Severity.INFO
                f.risk_level = RiskLevel.LOW
                f.message += " [skill_mode: topic not attack]"
            # Network/shell patterns in skill docs are examples
            elif f.rule in (
                "network-access",
                "network-exfiltration",
                "shell-execution",
                "curl-pipe-shell",
                "wget-pipe-shell",
                "pip-install-hidden",
            ):
                f.severity = Severity.INFO
                f.risk_level = RiskLevel.LOW
                f.message += " [skill_mode: doc example]"

    # ── Deduplicate ──────────────────────────────────────────────
    seen: set[str] = set()
    unique: list[Finding] = []
    for f in findings:
        key = f"{f.rule}:{f.file_path}:{f.line}"
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique


def _default_semgrep_rules() -> list[str]:
    """Return paths to bundled semgrep custom rules."""
    rules_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "rules",
    )
    candidates = [
        os.path.join(rules_dir, "hermes-critical.yaml"),
        os.path.join(rules_dir, "hermes-warning.yaml"),
    ]
    return [c for c in candidates if os.path.exists(c)]
