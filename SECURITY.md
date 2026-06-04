# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Hermes Ops Kit, please report it
privately via GitHub's Security Advisory system:

1. Go to the **Security** tab on the repository
2. Click **Report a vulnerability**
3. Follow the instructions to create a private advisory

Alternatively, email the maintainers directly if you cannot use GitHub's system.

We aim to acknowledge reports within 48 hours and provide an initial assessment
within 5 business days.

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.2.x   | ✅ Yes              |
| < 0.2.0 | ❌ No (pre-release) |

## Security Design Principles

Hermes Ops Kit is built around these security guarantees:

- **No raw secrets in logs, stdout, stderr, or audit files.** All output paths
  go through a shared `redact()` function covering 16+ secret patterns.
- **Secret scanner gate before every disk write.** `assert_clean()` blocks any
  content containing secret-like patterns before it reaches Obsidian or audit logs.
- **Two-phase key rotation.** New keys are stored, smoke-tested, and only
  activated after the smoke test passes. Old keys are revoked last.
- **Atomic env writes.** All `.env.generated` writes use temp → chmod 600 →
  fsync → rename, preventing partial files or permission leaks.
- **Safe Bitwarden CLI wrapper.** The `bw` CLI is called with subprocess list
  arguments (no shell injection), secrets are passed via stdin/environment
  (never command-line arguments), and forbidden commands (export, import,
  share, send) are blocked.
- **HTTPS required** for the Vaultwarden secret backend. TLS verification is
  enforced unless explicitly overridden.

## What to Report

- Secret leakage through any output path (stdout, stderr, files, logs)
- Redaction bypasses
- Unsafe file permissions or atomic write failures
- Bitwarden CLI safety bypasses
- Policy engine bypass allowing unauthorized operations
- Remote assistant delegation policy bypasses

## What Not to Report

- Theoretical vulnerabilities without a concrete exploit path
- Issues in third-party tools (Vaultwarden, Bitwarden CLI) — report those upstream
- Issues already documented in `docs/threat-model.md` unless you have a bypass

## Disclosure Policy

- We follow coordinated disclosure: please give us reasonable time to fix
  the issue before public disclosure.
- We will credit reporters in release notes unless anonymity is requested.
- Critical vulnerabilities will trigger an out-of-band patch release.

## Security Contact

Maintainers: see the repository's code owners for current contact information.
