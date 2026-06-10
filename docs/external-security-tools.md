# External Security Tools — Installation Guide

`install.sh` installs and verifies Semgrep and Bandit automatically in an
isolated scanner environment under `~/.local/share/hermes-ops-kit` (or
`$XDG_DATA_HOME/hermes-ops-kit`). This is required because Semgrep releases can
have Python dependencies that conflict with Hermes Ops Kit or Conda. It also
installs Gitleaks when Homebrew/Linuxbrew or Go is available. Go installations
are pinned to `v8.30.1`; Homebrew/Linuxbrew installations use the
package-managed version. The Plugin Security Scanner still works with built-in
detectors when an external tool is unavailable. When upgrading from an older
installer, Semgrep versions with a conflicting `ruamel.yaml<0.18` requirement
are migrated out of the active Python environment. Compatible existing tools
are preserved and reused.

Hermes Ops Kit integrates with three industry-standard external security tools.
They are not bundled in the repository. The automated installer provisions
them; use the commands below for manual installations.

---

## Quick Summary

| Tool     | Category        | Detects                                           | Install time | Runtime overhead |
| -------- | --------------- | ------------------------------------------------- | ------------ | ---------------- |
| gitleaks | secrets         | 150+ secret types (API keys, tokens, credentials) | ~30s         | +5-15s per scan  |
| Semgrep  | policy          | 2,500+ community SAST rules (OWASP, CWE, etc.)    | ~60s         | +10-30s per scan |
| Bandit   | policy (Python) | Python-specific security issues (B1xx-B8xx)       | ~15s         | +5-10s per scan  |

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
# gitleaks — pinned Go build
GOBIN="$HOME/.local/bin" go install github.com/zricethezav/gitleaks/v8@v8.30.1

# Semgrep + Bandit in separate isolated environments
python3 -m venv "$HOME/.local/share/hermes-ops-kit/scanners/semgrep"
python3 -m venv "$HOME/.local/share/hermes-ops-kit/scanners/bandit"
"$HOME/.local/share/hermes-ops-kit/scanners/semgrep/bin/python" -m pip install semgrep
"$HOME/.local/share/hermes-ops-kit/scanners/bandit/bin/python" -m pip install bandit
ln -sfn "$HOME/.local/share/hermes-ops-kit/scanners/semgrep/bin/semgrep" "$HOME/.local/bin/semgrep"
ln -sfn "$HOME/.local/share/hermes-ops-kit/scanners/bandit/bin/bandit" "$HOME/.local/bin/bandit"
```

### Linux (RHEL / Fedora / CentOS)

```bash
# gitleaks — pinned Go build
GOBIN="$HOME/.local/bin" go install github.com/zricethezav/gitleaks/v8@v8.30.1

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

### Windows / WSL

Run from Ubuntu/Debian WSL:

```bash
bash install-wsl.sh
```

The WSL bootstrap installs Go, Python, pip, and Git through `apt-get`, then
delegates to `install.sh`, which provisions all three scanners.

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

| Symptom                         | Likely Cause                               | Fix                                                                             |
| ------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------- |
| `semgrep: command not found`    | `~/.local/bin` is not on PATH              | Add `export PATH="$HOME/.local/bin:$PATH"` to the shell profile                 |
| `Failed to find semgrep-core`   | No compatible Semgrep wheel                | Rerun `install.sh`; it retries with the older-Linux-compatible Semgrep `1.96.0` |
| `gitleaks: command not found`   | Go/Homebrew unavailable                    | Install Go, then rerun `install.sh`                                             |
| Semgrep scan timeout (60s)      | Too many files / large repo                | Scanner auto-caps at 5MB per file                                               |
| gitleaks returns empty on macOS | No git repo (--no-git used)                | Normal — scanner passes `--no-git` flag                                         |
| Bandit import error             | Isolated scanner environment is incomplete | Remove `~/.local/share/hermes-ops-kit/scanners`, then rerun `install.sh`        |
