# Hermes Plugin Security Scanner — Documentation

> **Status:** MVP+ (Phase 1 + anti-FP tuning)
> **Version:** 0.2.0
> **Location:** `security/plugin_scanner/`

---

## Overview

The Plugin Security Scanner provides defense-in-depth security scanning for Hermes
plugins before they are executed. It detects hardcoded secrets, dangerous code patterns,
prompt injection, and policy violations using a combination of regex patterns, AST
analysis, and optional external tools (Semgrep, gitleaks, Bandit).

**Core principle:** Plugins with **medium or higher risk are disabled by default**
until explicitly approved by the operator. Critical findings **block execution**
entirely.

### What's New in 0.2.0

| Feature                       | What it does                                                                                                                                     |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Entropy check**             | Shannon entropy analysis distinguishes real keys (high entropy) from test fixtures (`abc123`)                                                    |
| **Sequential patterns**       | Detects dummy sequences (`abc123`, `def456`, `xyz789`) common in test fixtures                                                                   |
| **Doc mode**                  | Documentation files (`.md`, `SKILL.md`, `CHANGELOG.md`) get reduced severity — examples are expected                                             |
| **Test mode**                 | Findings in `tests/` directories with dummy patterns downgraded to INFO                                                                          |
| **Skill vs Code**             | Auto-detects text-heavy plugins (>60% `.md`) and applies softer severity — prompt injection in a red-teaming skill is its _topic_, not an attack |
| **Rule overrides**            | Fine-grained per-rule per-plugin control: `allow`, `skip`, `downgrade:warning`, `downgrade:info`                                                 |
| **Score cap**                 | No CRITICAL finding → max HIGH; no HIGH finding → max MEDIUM (prevents aggregation inflation)                                                    |
| **Approval enforcement**      | Approvals permit MEDIUM/HIGH plugins without hiding objective risk; CRITICAL remains blocked                                                     |
| **Skills directory scanning** | `~/.hermes/skills/` scanned alongside `~/.hermes/plugins/` with automatic skill-mode detection                                                   |

## Architecture

```text
security/plugin_scanner/
├── __init__.py              # Package init, version
├── findings.py              # Finding, RiskLevel, ScanResult, ScanProfile models
├── cache.py                 # SQLite SHA-256 cache
├── scanner.py               # Orchestrator + scan profiles + scoring engine
├── policy.py                # Approval policy + rule overrides (plugin_policy.json)
├── cli.py                   # CLI handler (scan, approve, override, revoke, policy, cache)
├── plugin_scanner.yaml      # Default configuration
├── categories/
│   ├── __init__.py
│   ├── secrets.py           # Secrets scan: regex + entropy + gitleaks (optional)
│   └── policy.py            # Policy scan: AST + regex + semgrep/bandit (optional)
└── rules/
    ├── hermes-critical.yaml # Semgrep custom rules (ERROR severity)
    └── hermes-warning.yaml  # Semgrep custom rules (WARNING severity)
```

## Quick Start

```bash
# Scan all plugins with default startup profile
hermes-ops-kit plugin scan

# Scan a specific plugin
hermes-ops-kit plugin scan --plugin ~/.hermes/plugins/my-plugin

# Full manual scan with JSON output
hermes-ops-kit plugin scan --profile manual --json --force

# View approval policy
hermes-ops-kit plugin policy

# Approve a plugin
hermes-ops-kit plugin approve my-plugin

# Whitelist a specific rule for a plugin (fine-grained)
hermes-ops-kit plugin override gotify-notify network-access allow

# Run integration test suite
bash scripts/test-scanner.sh
```

## Scan Categories

### secrets

Detects hardcoded credentials in plugin source files:

- API keys (OpenAI, Anthropic, Gemini, GitHub, NVIDIA)
- Bitwarden/Vaultwarden session keys
- Private keys (RSA, EC, OpenSSH)
- Connection strings with credentials
- Webhook URLs (Slack, Discord)
- Generic passwords, tokens, and secrets

**Anti-false-positive measures:**

- Shannon entropy check: keys with < 3.2 bits/char entropy are downgraded (real keys are random)
- Sequential pattern detection: `abc123`, `def456`, `xyz789` sequences → flagged as dummy
- Dummy word detection: `example`, `placeholder`, `xxx`, `leak`, `<KEY>`, `<TOKEN>` → flagged
- Doc mode: findings in `.md`/`SKILL.md` files get one severity level downgrade
- Test mode: findings in `tests/` with dummy patterns → INFO severity

Optionally integrates with **gitleaks** if installed.

### policy

Detects dangerous code patterns using AST analysis and regex:

- Shell execution (`os.system()`, `subprocess.run()`)
- Dynamic code execution (`eval()`, `exec()`, `compile()`)
- Dynamic imports (`__import__()`, `importlib.import_module()`)
- Network access (`socket`, `requests.post()` to external URLs)
- Environment variable access (BW*\*, VAULTWARDEN, HERMES*\*)
- Prompt injection in SKILL.md/AGENTS.md/CLAUDE.md
- Curl/wget piped to shell
- Hidden pip/npm install in setup scripts

**Anti-false-positive measures:**

- Skill mode: prompt-injection findings in skills → INFO (topic, not attack)
- Python file guard: `pip install` / `curl | bash` in `.py` files (docstrings) → skipped
- Scanner self-exclusion: `rules/` YAML files and `.github/workflows/` → skipped

Optionally integrates with **Semgrep** and **Bandit** if installed.

### Future Categories

- **code** (Phase 2): Entropy analysis, obfuscation detection
- **dependencies** (Phase 2): Supply chain vulnerability scanning
- **behavior** (Phase 3): Docker sandbox dry-run execution
- **reputation** (Phase 3): VirusTotal, OSSF Scorecard, Socket.dev

## Risk Levels

| Level    | Score | Default Action                        |
| -------- | ----- | ------------------------------------- |
| NONE     | 0     | Allow silently                        |
| LOW      | 1–9   | Allow with notification               |
| MEDIUM   | 10–24 | Disable (requires approval)           |
| HIGH     | 25–49 | Disable (requires approval + warning) |
| CRITICAL | ≥ 50  | BLOCK (manual config override only)   |

### Score Cap Rules

To prevent many low-severity findings from aggregating to an inflated overall risk:

1. **No CRITICAL finding → max HIGH** — if every individual finding is HIGH or below, the overall cannot be CRITICAL
2. **No HIGH finding → max MEDIUM** — if every individual finding is MEDIUM or below, the overall cannot be HIGH

A single CRITICAL finding always results in CRITICAL overall (no cap applies).

## Scan Profiles

| Profile | Categories                 | Timeout | Cache TTL | Blocks on      | Use Case                 |
| ------- | -------------------------- | ------- | --------- | -------------- | ------------------------ |
| startup | secrets, policy (built-in) | 12s     | 7 days    | critical, high | Every Hermes start       |
| install | secrets, policy            | 60s     | 0 (fresh) | critical, high | First plugin install     |
| update  | secrets, policy            | 60s     | 0 (fresh) | critical, high | Plugin git pull / update |
| manual  | secrets, policy            | 120s    | 0 (fresh) | critical       | On-demand full scan      |
| ci      | secrets, policy            | 60s     | 0 (fresh) | critical       | CI/CD pipeline           |

## SHA Cache

Results are cached in `~/.hermes/ops-kit/plugin_scanner_cache.db` (SQLite).
Cache keyed by both:

- **git_commit_hash** — detects new commits (plugin updates)
- **file_tree_sha** — detects uncommitted local file changes

**Cache invalidation triggers:**

- New git commit → automatic rescan
- Local file changes → automatic rescan
- Scanner version change → rescan all plugins
- TTL expiry (default: 7 days for startup profile)
- `--force` flag → skip cache

## Plugin Type Detection

The scanner auto-classifies plugins during scanning:

| Type      | Criteria         | Effect                                                               |
| --------- | ---------------- | -------------------------------------------------------------------- |
| **code**  | ≤60% `.md` files | Standard severity — executable code patterns are high risk           |
| **skill** | >60% `.md` files | Soft severity — prompt injection = topic, secrets in docs = examples |

Skills are text-heavy plugins loaded as AI context. Their `.md` files naturally
contain documentation, examples, and topic-relevant patterns (e.g., prompt injection
in a red-teaming skill). The scanner reflects this in its severity decisions.

## Approval Workflow

### Disable-by-Default

Plugins with MEDIUM or higher risk are **disabled by default**. They will not be
loaded by Hermes until explicitly approved.

Critical findings **block** execution. Override requires manual config edit.

### Approval Levels

| Level         | Command                                       | Effect                           |
| ------------- | --------------------------------------------- | -------------------------------- |
| Plugin        | `plugin approve <name>`                       | All findings, present and future |
| Finding       | `plugin approve --finding <id>`               | Single specific finding          |
| Category      | `plugin approve <name> --category <category>` | All findings of one category     |
| Global        | `plugin approve --all`                        | All installed plugins            |
| Rule Override | `plugin override <name> <rule> <action>`      | Per-rule per-plugin fine-grained |

### Rule Override Actions

| Action              | Effect                                           |
| ------------------- | ------------------------------------------------ |
| `allow` / `skip`    | Remove the finding entirely (whitelist the rule) |
| `downgrade:warning` | Reduce severity to WARNING, risk to MEDIUM       |
| `downgrade:info`    | Reduce severity to INFO, risk to LOW             |

```bash
# Examples
hermes-ops-kit plugin override gotify-notify network-access allow
hermes-ops-kit plugin override red-teaming prompt-injection-system downgrade:info
hermes-ops-kit plugin override --show                           # List all overrides
hermes-ops-kit plugin override my-plugin --remove --rule my-rule # Remove one
```

### Policy File

`~/.hermes/ops-kit/plugin_policy.json` — atomic writes with `tmp + os.replace`.

```json
{
  "version": 2,
  "approved_plugins": ["hermes-ops-kit", "apple"],
  "approved_findings": [],
  "approved_categories": [],
  "disabled_plugins": [],
  "blocked_plugins": ["bad-plugin"],
  "rule_overrides": {
    "gotify-notify": {
      "network-access": "allow"
    },
    "red-teaming": {
      "prompt-injection-system": "downgrade:info",
      "prompt-injection-role": "downgrade:info"
    }
  }
}
```

### Audit Trail

All approval decisions logged to `~/.hermes/ops-kit/plugin_policy_audit.jsonl`:

```jsonl
{"ts":"2026-06-09T15:30:00Z","action":"plugin_approved","plugin":"hermes-ops-kit"}
{"ts":"2026-06-09T15:31:12Z","action":"rule_override_set","plugin":"gotify-notify","rule":"network-access","override":"allow"}
```

## CLI Reference

```bash
# Scanning
hermes-ops-kit plugin scan                                    # Default startup scan
hermes-ops-kit plugin scan --profile manual                   # Full manual scan
hermes-ops-kit plugin scan --plugin <name-or-path>            # Single plugin
hermes-ops-kit plugin scan --category secrets,policy          # Specific categories
hermes-ops-kit plugin scan --force                            # Skip cache
hermes-ops-kit plugin scan --json                             # Machine-readable output

# Approval
hermes-ops-kit plugin approve <plugin>                        # Approve entire plugin
hermes-ops-kit plugin approve <plugin> --category <category>  # Approve category
hermes-ops-kit plugin approve --finding <finding-id>          # Approve single finding
hermes-ops-kit plugin approve --all                           # Approve all plugins

# Rule Overrides (fine-grained)
hermes-ops-kit plugin override <plugin> <rule> allow          # Whitelist a rule
hermes-ops-kit plugin override <plugin> <rule> downgrade:info # Downgrade severity
hermes-ops-kit plugin override <plugin> <rule> downgrade:warning
hermes-ops-kit plugin override --show [<plugin>]              # List all overrides
hermes-ops-kit plugin override <plugin> --remove              # Remove all for plugin
hermes-ops-kit plugin override <plugin> --remove --rule <r>   # Remove single rule

# Revocation
hermes-ops-kit plugin revoke <plugin>                         # Revoke plugin approval
hermes-ops-kit plugin revoke --finding <finding-id>           # Revoke finding
hermes-ops-kit plugin revoke --all                            # Revoke all approvals

# Plugin state
hermes-ops-kit plugin disable <plugin>                        # Explicitly disable
hermes-ops-kit plugin enable <plugin>                         # Re-enable
hermes-ops-kit plugin block <plugin>                          # Permanently block

# Policy & cache
hermes-ops-kit plugin policy                                  # Show policy + overrides
hermes-ops-kit plugin policy --json                           # JSON policy
hermes-ops-kit plugin cache show                              # Cache contents
hermes-ops-kit plugin cache clear                             # Clear cache
hermes-ops-kit plugin rules update                            # Check external tool status

# First-install bootstrap
hermes-ops-kit install setup                                  # Create scanner config + reports
hermes-ops-kit install setup --json                           # Machine-readable bootstrap report
hermes-ops-kit install repair                                 # Restore ~/.hermes/config.yaml from backup
```

The first-install bootstrap creates `~/.hermes/ops-kit/plugin_scanner.yaml`
if it does not already exist, runs the install-profile scan, writes a
human-readable report and a JSON report under
`~/.hermes/ops-kit/reports/`, and applies safe preflight enforcement to
`~/.hermes/config.yaml`.

The generated scanner configuration is currently an operator-readable snapshot
of the shipped defaults. Enforcement remains code-defined so an edited local
file cannot silently weaken install/startup gates. Future configurable policy
must validate and constrain any security-reducing option before it is consumed.

It also prints a clear disclaimer:

> Security scanning reduces risk but does not guarantee that a plugin is safe.
> It performs static analysis and optional external-tool checks before execution,
> but it is not a runtime antivirus, EDR, or guarantee against zero-days,
> time bombs, logic bombs, or sophisticated obfuscation. Review findings before
> approving plugins.

For the full integration design, see:

- `docs/plugin-security-scanner-design.md`
- `docs/hermes-hook-integration.md`

## Optional External Tools

The scanner works fully without any external tools. When available, these
enhance detection:

| Tool     | Category | Install                 | Effect                         |
| -------- | -------- | ----------------------- | ------------------------------ |
| Semgrep  | policy   | `pip install semgrep`   | 2,500+ community SAST rules    |
| gitleaks | secrets  | `brew install gitleaks` | 150+ secret type detectors     |
| Bandit   | policy   | `pip install bandit`    | Python-specific security rules |

If a tool is not installed, the scanner degrades gracefully and uses only
built-in detection methods.

## Testing

```bash
# Integration test suite (10 tests: imports, entropy, classification,
# overrides, gotify-notify, self-scan, skills, full-scan, caching, policy)
bash scripts/test-scanner.sh

# Run all scanner unit tests (110 tests as of v0.2.0)
python3 -m pytest tests/test_plugin_scanner.py -v

# Run the complete pytest suite (194 collected tests as of v0.2.0;
# excludes the 10 shell integration checks and simulator scenarios)
python3 -m pytest tests/ -v

# Simulator (8 failure scenarios, no real API calls)
python3 tests/test_simulator.py --all
```

## Integration with Hermes

The scanner is designed to run as a hook during Hermes startup:

```yaml
# ~/.hermes/config.yaml
hooks:
  on_startup:
    - plugin: hermes-ops-kit
      hook: plugin_security_scan
      config:
        profile: startup
        block_on: [critical, high]
```

## Limitations

- **No runtime monitoring:** Scanner analyzes source code, does not monitor
  running processes. Time bombs, logic bombs, and polymorphic malware may
  evade detection.
- **No guarantee of completeness:** Novel attack patterns, zero-day
  exploits, and sophisticated obfuscation may not be detected.
- **False positives reduced but possible:** Entropy check, doc/skill/test
  downgrades, and rule overrides significantly reduce noise, but some
  legitimate plugins may still trigger findings (e.g., plugins that
  legitimately need network access). Use `plugin override` for fine-grained
  whitelisting.
- **Semgrep/gitleaks optional:** Without these tools, detection is limited
  to built-in regex, AST, and entropy patterns.
- **No sandbox execution in MVP:** Behavior analysis (Docker sandbox) is
  a Phase 3 feature and not yet implemented.

## Files Changed

```text
NEW (0.1.0):
  security/plugin_scanner/__init__.py
  security/plugin_scanner/findings.py
  security/plugin_scanner/cache.py
  security/plugin_scanner/scanner.py
  security/plugin_scanner/policy.py
  security/plugin_scanner/cli.py
  security/plugin_scanner/plugin_scanner.yaml
  security/plugin_scanner/categories/__init__.py
  security/plugin_scanner/categories/secrets.py
  security/plugin_scanner/categories/policy.py
  security/plugin_scanner/rules/hermes-critical.yaml
  security/plugin_scanner/rules/hermes-warning.yaml
  tests/test_plugin_scanner.py
  docs/plugin-security-scanner.md
  docs/plugin-security-scanner-design.md
  docs/hermes-hook-integration.md

MODIFIED (0.1.0):
  commands.py                    # Added 'plugin' subcommand dispatcher
  policy/engine.py               # Added check_plugin_security() function
  policy/rules.yaml              # Added plugin_security rule set
  mcp/auditor.py                 # Path traversal defense in OAuth token loading
  docs/Threat Model.md           # Added plugin scanner + MCP path traversal boundaries
  docs/architecture.md           # Added plugin_scanner module + updated policy/docs sections

NEW/MODIFIED (0.2.0):
  categories/secrets.py          # +entropy check, +dummy detection, +doc/test/skill downgrades
  categories/policy.py           # +skill-mode downgrades, +pip/curl guard for .py files
  scanner.py                     # +score cap, +objective risk, +scan_context hash, +plugin_type
  enforce.py                     # Preflight enforcement: scan → decisions → config sync
  bootstrap.py                   # First-install setup wizard
  policy.py                      # +rule_overrides (allow/downgrade), v2 policy format
  cli.py                         # +override subcommand, +policy display with overrides
  scripts/test-scanner.sh        # 10-test integration suite
  install.sh                     # Pre-install static security gate
  bridge.py                      # +install +preflight subcommands
  policy/decisions.py            # +preflight_decision() wrapper
  policy/engine.py               # +check_plugin_security()
  policy/rules.yaml              # +plugin_security rule set
  docs/external-security-tools.md# Platform-specific gitleaks/Semgrep/Bandit install guide
  docs/Threat Model.md           # +plugin scanner + MCP boundaries
  docs/architecture.md           # +plugin_scanner module + preflight data flow
  CLAUDE.md                      # Updated scanner section + rule overrides
  README.md                      # Updated v0.2.0 features + preflight + docs
  docs/plugin-security-scanner.md # This file — complete 0.2.0 refresh
```

## Follow-Up Recommendations

1. **Phase 2:** Implement `code` and `dependencies` scan categories
2. **Phase 3:** Add Docker sandbox (`behavior`) and external threat intel (`reputation`)
3. **CI Integration:** The `ci` scan profile is defined; add a GitHub Actions workflow
4. **TUI Dashboard:** Interactive terminal UI for reviewing and approving scan results
5. **Semgrep Registry:** Contribute custom Hermes rules upstream to the Semgrep community
6. **Auto-approval learning:** ML-based pattern recognition for known-safe plugin behaviors
