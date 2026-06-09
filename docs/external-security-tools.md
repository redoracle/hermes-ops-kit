# External Security Tools — Installation Guide

> **These tools are optional.** The Plugin Security Scanner works fully without them.
> Installing them enhances detection coverage but is never required for operation.

Hermes Ops Kit integrates with three industry-standard external security tools.
They are **not bundled** and must be installed separately if you want the
additional detection rules they provide.

---

## Quick Summary

| Tool     | Category        | Detects                                          | Install time | Runtime overhead |
| -------- | --------------- | ------------------------------------------------ | ------------ | ---------------- |
| gitleaks | secrets         | 150+ secret types (API keys, tokens, credentials) | ~30s         | +5-15s per scan  |
| Semgrep  | policy          | 2,500+ community SAST rules (OWASP, CWE, etc.)   | ~60s         | +10-30s per scan |
| Bandit   | policy (Python) | Python-specific security issues (B1xx-B8xx)      | ~15s         | +5-10s per scan  |

**Without these tools**, the scanner still provides:
- 16-pattern regex secret detection (API keys, tokens, private keys, webhooks)
- AST-based dangerous code analysis (shell exec, dynamic imports, network access)
- Shannon entropy analysis for distinguishing real keys from test fixtures
- Prompt injection detection in markdown skill files

---

## Platform-Specific Instructions

### macOS

```bash
# gitleaks
brew install gitleaks

# Semgrep — choose one:
brew install semgrep                    # Homebrew (recommended)
pip install semgrep                     # pip (if using venv)

# Bandit
pip install bandit
```

Verify:
```bash
gitleaks version
semgrep --version
bandit --version
```

### Linux (Debian / Ubuntu)

```bash
# gitleaks — download the binary (no apt package)
GITLEAKS_VERSION=8.24.0
curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_amd64.tar.gz" | \
  sudo tar -xz -C /usr/local/bin gitleaks
sudo chmod +x /usr/local/bin/gitleaks

# Semgrep
python3 -m pip install semgrep

# Bandit
python3 -m pip install bandit
```

### Linux (RHEL / Fedora / CentOS)

```bash
# gitleaks
GITLEAKS_VERSION=8.24.0
curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_amd64.tar.gz" | \
  sudo tar -xz -C /usr/local/bin gitleaks
sudo chmod +x /usr/local/bin/gitleaks

# Semgrep
python3 -m pip install semgrep

# Bandit
python3 -m pip install bandit
```

### Linux (Arch)

```bash
# gitleaks
yay -S gitleaks

# Semgrep
python3 -m pip install semgrep

# Bandit
python3 -m pip install bandit
```

### Windows

```powershell
# gitleaks — download from GitHub Releases
# https://github.com/gitleaks/gitleaks/releases/latest
# Download gitleaks_*_windows_amd64.zip, extract, add to PATH

# Semgrep
pip install semgrep

# Bandit
pip install bandit
```

### Docker (any platform)

```bash
# gitleaks
docker run --rm -v "$(pwd):/src" zricethezav/gitleaks detect --source /src --no-git

# Semgrep
docker run --rm -v "$(pwd):/src" semgrep/semgrep semgrep scan --config auto /src
```

---

## What Each Tool Adds

### gitleaks

Adds detection for **150+ secret types** beyond the scanner's built-in patterns,
including: AWS keys, GCP service accounts, Azure connection strings, GitLab tokens,
JWT secrets, private key material, and generic high-entropy strings.

The scanner runs gitleaks as a subprocess (`gitleaks detect --no-git --source <path>`)
and converts its JSON output into standard `Finding` objects, applying the same
redaction pipeline as built-in findings.

### Semgrep

Adds **2,500+ community SAST rules** from the Semgrep Registry, covering the
OWASP Top 10, CWE Top 25, and language-specific security anti-patterns for
Python, JavaScript, Go, Ruby, Java, and more.

The scanner bundles two custom Hermes-specific rule files
(`hermes-critical.yaml`, `hermes-warning.yaml`) and invokes Semgrep as:
`semgrep scan --json --config <custom-rules> <plugin-path>`.

Semgrep findings are mapped into the scanner's severity/risk model:
Semgrep `ERROR` → scanner `ERROR` (HIGH risk), Semgrep `WARNING` → scanner
`WARNING` (MEDIUM risk).

### Bandit

Adds **Python-specific security rules** (B1xx through B8xx series) including
hardcoded passwords, SQL injection, XSS, request without timeout, and
insecure hash algorithms.

Bandit runs as: `bandit -r <plugin-path> -f json -ll --quiet` and its
results are mapped: Bandit `HIGH` → scanner ERROR (HIGH risk),
Bandit `MEDIUM` → scanner WARNING (MEDIUM risk), Bandit `LOW` → scanner INFO (LOW risk).

---

## Verifying Installation

After installing, verify the scanner detects the tools:

```bash
hermes-ops-kit plugin rules update
```

Output shows which tools are available:
```
Plugin Scanner Rules
  Semgrep:  available
  gitleaks: available
  Custom rules: 2 files
    - hermes-critical.yaml
    - hermes-warning.yaml
```

Or programmatically:
```python
from security.plugin_scanner.categories.policy import _semgrep_available
from security.plugin_scanner.categories.secrets import _gitleaks_available
from security.plugin_scanner.categories.policy import _bandit_available

print(f"Semgrep:  {_semgrep_available()}")
print(f"gitleaks: {_gitleaks_available()}")
print(f"Bandit:   {_bandit_available()}")
```

---

## Troubleshooting

| Symptom                          | Likely Cause                | Fix                                       |
| -------------------------------- | --------------------------- | ----------------------------------------- |
| `semgrep: command not found`     | Not on PATH                 | `pip install semgrep` or use full path    |
| `gitleaks: command not found`    | Not on PATH                 | `brew install gitleaks` or download binary |
| Semgrep scan timeout (60s)       | Too many files / large repo | Scanner auto-caps at 5MB per file         |
| gitleaks returns empty on macOS  | No git repo (--no-git used) | Normal — scanner passes `--no-git` flag   |
| Bandit import error              | Not installed               | `pip install bandit`                      |
