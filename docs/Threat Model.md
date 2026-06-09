---
title: Threat Model
tags: [hermes, ops-kit, security, threat-model]
created: 2026-06-04
modified: 2026-06-09
---

# Threat Model

Hermes Ops Kit operates on secrets, API keys, provider credentials, and
infrastructure configuration. This document catalogs threats and mitigations.

## Trust Boundaries

```text
┌─────────────────────────────────────────────────────────────┐
│ Local Host (macOS / Linux)                                  │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Hermes   │  │ Ops Kit      │  │ Bitwarden/Vaultwarden│   │
│  │ Agent    │◄─┤ Plugin       │──┤ (self-hosted)        │   │
│  │ Runtime  │  │              │  │                      │   │
│  └──────────┘  └──────┬───────┘  └──────────────────────┘   │
│                       │                                     │
│  ┌────────────────────▼───────────────────────────────────┐ │
│  │ ~/.hermes/                                             │ │
│  │  .env (0600)   .env.generated (0600)   audit/*.jsonl   │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │
         ▼  (TLS, API keys from env)
┌─────────────────────────────────────────────────────────────┐
│ External Provider APIs                                      │
│  api.openai.com  api.anthropic.com  generativelanguage...   │
└─────────────────────────────────────────────────────────────┘
```

### Trust Boundary 1: Bitwarden/Vaultwarden ↔ Ops Kit

**Threats:**

- Compromised Bitwarden/Vaultwarden server exposes all secrets
- `bw` CLI session token leaked from `~/.hermes/.env`
- Man-in-the-middle between ops-kit and Bitwarden/Vaultwarden

**Mitigations:**

- HTTPS required (TLS verification enforced)
- `bw` session token stored in chmod 600 `.env` file
- `bw serve` restricted to 127.0.0.1
- Session tokens expire periodically (Bitwarden default)
- Secrets never logged — only `sha256:xxx` fingerprints and `last4` chars

### Trust Boundary 2: Ops Kit → Provider APIs

**Threats:**

- API key leaked in logs, stdout, stderr, or audit files
- Key exfiltration via Obsidian notes
- Accidental key commit to git

**Mitigations:**

- 16-pattern `redact()` function catches all known key formats
- `assert_clean()` scanner gate before every Obsidian/audit write
- `.gitignore` excludes `.env*`, `*.jsonl`, `*.key`, `*.pem`
- Atomic env writes with chmod 600
- Test fixtures use obviously fake keys (`sk-abc123testsecretnotreal`)

### Trust Boundary 3: Assistant Delegation

**Threats:**

- Assistant API key leaked in delegation audit log
- Malicious assistant response exfiltration
- Task injection via crafted capabilities

**Mitigations:**

- Policy engine checks every delegation: blocks secrets, shell exec, file mutation by default
- Result sanitizer redacts secrets from assistant responses
- Audit log stores only request/result fingerprints, never raw content
- Read-only by default for remote assistants
- Capabilities are allowlisted per assistant in `assistants.yaml`

### Trust Boundary 4: Plugin Installation & Execution

**Threats:**

- Malicious plugin with hardcoded secrets (API keys, tokens, session keys)
- Plugin executing arbitrary shell commands (`os.system`, `subprocess`)
- Dynamic code injection via `eval()`, `exec()`, or `__import__()`
- Prompt injection in plugin SKILL.md / AGENTS.md / CLAUDE.md
- Plugin exfiltrating environment variables (BW*SESSION, VAULTWARDEN, HERMES*\*)
- Supply chain attack via malicious plugin update

**Mitigations:**

- **Plugin Security Scanner** (`security/plugin_scanner/`): scans all plugins before loading
  - Secrets detection: regex patterns for 10+ key formats + optional gitleaks (150+ detectors)
  - Policy detection: AST analysis + regex for dangerous patterns + optional semgrep (2,500+ rules)
  - Disable-by-default: medium+ risk plugins are disabled until explicitly approved
  - Critical findings block execution entirely (requires manual config override)
- **SHA-256 cache** (SQLite): rescan on git commit, file tree change, or scanner version change
- **Approval policy** (`plugin_policy.json` v2): atomic writes, JSONL audit trail, plus
  per-rule per-plugin overrides (`allow`, `downgrade:warning`, `downgrade:info`) for
  fine-grained whitelisting without blanket plugin approval
- **Policy engine integration** (`check_plugin_security()`): centralized risk evaluation with
  declarative rules in `policy/rules.yaml`
- **Scan profiles**: startup (12s fast scan on Hermes boot), install/update (60s deep scan),
  manual (120s adversarial scan), ci (60s CI/CD pipeline scan)
- **Anti-false-positive measures** (v0.2.0):
  - Shannon entropy analysis: keys with <3.2 bits/char entropy are downgraded (real keys are random)
  - Sequential pattern detection: `abc123`, `def456`, `xyz789` sequences flagged as dummy/test
  - Doc mode: findings in `.md`/`SKILL.md`/`CHANGELOG.md` downgraded one severity level (examples are expected)
  - Test mode: findings in `tests/` with dummy patterns downgraded to INFO
  - Skill vs Code auto-detection: text-heavy plugins (>60% `.md`) get softer severity (prompt injection in a red-teaming skill is its _topic_, not an attack)
  - Score cap: no CRITICAL individual finding → max HIGH; no HIGH → max MEDIUM
- **Optional external tools**: gitleaks (150+ secret types), Semgrep (2,500+ SAST rules),
  Bandit (Python security rules) — auto-detected at runtime, graceful degradation if absent
- Defense-in-depth: scanner runs as a separate module, not inline with plugin loading

### Trust Boundary 5: MCP Token Storage

**Threat:** Path traversal attack where a malformed MCP server ID
(e.g. `../../.ssh/id_rsa`) could read or overwrite arbitrary files
outside `~/.hermes/mcp-tokens/`.

**Mitigation:** `mcp/auditor.py:_load_oauth_token()` constrains the
server ID to a single path component via `os.path.realpath()` and
verifies the resolved path stays within the canonical token directory.
If the resolved path escapes, the function returns `None` — silently
refusing the read.

### Trust Boundary 6: MCP Tool Loading

**Threat:** MCP audit findings and approvals are advisory only unless Hermes'
`mcp_servers.<id>.enabled` configuration is updated before boot.

**Mitigation:** `hermes-ops-kit preflight` runs the MCP auditor before Hermes
starts. Servers with incomplete discovery or unapproved HIGH tools are disabled.
Any CRITICAL tool disables its server and blocks boot, even if broadly approved.
Preflight uses static declarations only, avoiding execution or network contact
with an unaudited MCP server during the gate itself.

## Key Rotation Threat Analysis

### Threat: Old key revoked before new key proven working

**Mitigation:** Two-phase rotation — store candidate → smoke test (live API call) → activate only if smoke passes → revoke old. Never revoke before smoke.

### Threat: Rotation interrupted mid-process (power loss, crash)

**Mitigation:** Each phase writes its own audit event. The `--status` command reports current state. Recovery: check status, resume from last checkpoint.

### Threat: Manual key paste captured by clipboard monitor

**Mitigation:** `--manual-new-key-stdin` reads from stdin (not clipboard). The key is never echoed to terminal. Prompt printed to stderr only.

### Threat: Admin credentials leak into runtime env file

**Mitigation:** 3-layer denylist gate in `render_env_content()`:

1. Hard `deny_render` list in `config/env_projection.yaml` — named admin refs are hard-blocked
2. Path-segment classification — any ref containing `admin_key`/`admin_secret`/`admin_token` is auto-blocked
3. Bitwarden/Vaultwarden metadata flag — secrets stored with `secret_class=admin` have `renderable_to_env=False`

Admin keys can NEVER reach `.env.generated`. Even if someone adds
`hermes/openai/admin_key` to the projection mapping, it is blocked
with a `<DENIED>` comment marker.

### Threat: Secret zero compromise (.env stolen)

The `.env` file contains Bitwarden/Vaultwarden bootstrap credentials — if stolen,
an attacker can unlock the entire secret store.

**Mitigation:**

- `.env` is chmod 600 (enforced by `--doctor-secrets`)
- `BW_SESSION` is short-lived (daily expiry), revocable from Bitwarden/Vaultwarden server
- `.env` contains only 4-6 bootstrap vars, not 10+ provider keys
- `.env` is gitignored and excluded from backups/sync
- Provider keys can be rotated without touching `.env` — limiting window of exposure
- Bitwarden/Vaultwarden server can be configured with IP allowlisting and MFA

This is the standard "secret zero" trade-off shared by HashiCorp Vault,
AWS Secrets Manager, and every encrypted secret store.

### Threat: Concurrent rotations cause race conditions

**Mitigation:** Per-provider `fcntl.flock` advisory locks
(`security/lockfile.py`). Two terminals running `--provider openai`
simultaneously will serialize. Concurrent `--all --parallel` locks
each provider independently.

### Threat: Rotation crash leaves inconsistent state

**Mitigation:** 14-phase state machine with checkpointing to
`~/.hermes/rotation_checkpoints/<provider>.json`. Each phase transition
writes a fingerprint-only checkpoint. Interrupted rotations resume with
`hermes-key-rotate resume --provider <provider>`.

## Redaction Pipeline

```text
User Output ← redact() ← tool handler (tools.py)
                          │
                          ├── bridge.py subprocess stdout
                          ├── usage_metrics_v2.py output
                          ├── hermes_key_rotate.py output
                          └── assistant results

Disk Write ← assert_clean() ← audit/*.py
                              env/atomic_write.py
```

- `redact()`: replaces detected patterns with `<PROVIDER_KEY_REDACTED>`
- `assert_clean()`: raises `SecretLeakError` if any pattern detected — blocks the write entirely

## Atomic Write Guarantees

All `.env.generated` writes:

1. Write content to temp file (same directory)
2. `os.fchmod(600)` on the file descriptor
3. `os.fsync()` to flush to disk
4. `os.rename()` (atomic on same filesystem)

Result: at no point does a readable `.env.generated` exist with wrong permissions
or partial content. Power loss during write leaves only the temp file.

## Related

- [[Architecture]] — module architecture
- [[Hermes Compatibility]] — Hermes integration guide
- [[Route Profile Design]] — route architecture
- [[Plugin Security Scanner]] — plugin scanner documentation
- [[Hermes Hook Integration]] — scanner hook wiring for Hermes
- `security/redaction.py` — 16-pattern secret redaction
- `security/lockfile.py` — per-provider fcntl.flock advisory locks
- `env/atomic_write.py` — temp→chmod 600→fsync→rename atomic writes
- `security/plugin_scanner/` — plugin security scanner source
- [[Architecture Decisions]] — key architectural decisions (ADRs)
- [[Key Management Lifecycle]] — full secret lifecycle, rotation modes, revocation matrix
- [[Operations Runbook]] — incident response procedures
- [[Quickstart]] — getting started guide
