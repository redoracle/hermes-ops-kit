# Threat Model

Hermes Ops Kit operates on secrets, API keys, provider credentials, and
infrastructure configuration. This document catalogs threats and mitigations.

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│ Local Host (macOS / Linux)                                   │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Hermes   │  │ Ops Kit      │  │ Vaultwarden (self-   │  │
│  │ Agent    │◄─┤ Plugin       │──┤ hosted, LAN/VPN)     │  │
│  │ Runtime  │  │              │  │                      │  │
│  └──────────┘  └──────┬───────┘  └──────────────────────┘  │
│                       │                                       │
│  ┌────────────────────▼───────────────────────────────────┐ │
│  │ ~/.hermes/                                              │ │
│  │  .env (0600)   .env.generated (0600)   audit/*.jsonl   │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │
         ▼  (TLS, API keys from env)
┌─────────────────────────────────────────────────────────────┐
│ External Provider APIs                                       │
│  api.openai.com  api.anthropic.com  generativelanguage...   │
└─────────────────────────────────────────────────────────────┘
```

### Trust Boundary 1: Vaultwarden ↔ Ops Kit

**Threats:**
- Compromised Vaultwarden server exposes all secrets
- `bw` CLI session token leaked from `~/.hermes/.env`
- Man-in-the-middle between ops-kit and Vaultwarden

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

## Key Rotation Threat Analysis

### Threat: Old key revoked before new key proven working
**Mitigation:** Two-phase rotation — store candidate → smoke test (live API call) → activate only if smoke passes → revoke old. Never revoke before smoke.

### Threat: Rotation interrupted mid-process (power loss, crash)
**Mitigation:** Each phase writes its own audit event. The `--status` command reports current state. Recovery: check status, resume from last checkpoint.

### Threat: Manual key paste captured by clipboard monitor
**Mitigation:** `--manual-new-key-stdin` reads from stdin (not clipboard). The key is never echoed to terminal. Prompt printed to stderr only.

## Redaction Pipeline

```
User Output ← redact() ← tool handler (tools.py)
                          │
                          ├── bridge.py subprocess stdout
                          ├── usage_metrics_v2.py output
                          ├── hermes_key_rotate.py output
                          └── assistant results

Disk Write ← assert_clean() ← obsidian_sink.py
                              audit/*.py
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
