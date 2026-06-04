# Changelog

All notable changes to Hermes Ops Kit.

## [Unreleased]

### Vault Management

- **`backup-vault`** — exports all Bitwarden/Vaultwarden refs to a fingerprint-only JSON
  backup file (chmod 600). Raw secrets are never exported.
- **`restore-vault`** — verifies backup integrity against the current vault.
  Reports missing refs that need re-seeding.
- **`diff`** — compares Bitwarden/Vaultwarden state vs `.env` vs `.env.generated`,
  showing which keys are in sync and which need migration.
- **`migrate`** — interactive one-shot wizard: scans `.env`, seeds all runtime
  keys into Bitwarden/Vaultwarden, renders `.env.generated`, and reports next steps.
- **`render-env --verify`** — runs `usage_metrics_v2` against the generated
  `.env.generated` to confirm all rendered keys are healthy.

### New Commands

- **`seed-from-env`** — bulk-migrates all provider runtime API keys from
  `~/.hermes/.env` into Bitwarden/Vaultwarden. Reads `env_projection.yaml` to discover
  the env-var → secret-ref mapping, validates each key against the provider
  API, skips already-stored keys. Supports `--dry-run` for preview.
- **GitHub `curl` fallback** — `validate_new_key()` and `smoke_test()` now
  fall back to `curl` when `gh` CLI is not installed. Uses
  `GET https://api.github.com/rate_limit` with `Authorization: Bearer` header.
- **Key age in `--status`** — each runtime key now shows `age_days` (days
  since last rotation) and a `stale` warning for keys older than 90 days.
  Secrets are classified as `runtime`, `admin`, or `config`.

### Security — Secret Classification & Rendering Safety

- **SecretClass enum** (`RUNTIME`, `ADMIN`, `CONFIG`) in `security/secret_backend.py`.
  Every Bitwarden/Vaultwarden secret now carries classification metadata in custom fields
  (`secret_class`, `renderable_to_env`). Admin-class secrets are NEVER renderable
  into `.env.generated`.
- **Three-layer denylist gate** in `env/render_env.py`:
  1. Hard `deny_render` list in `config/env_projection.yaml` blocks named admin refs
  2. Path-segment classification — any ref containing `admin_key`/`admin_secret`/etc.
     is auto-blocked
  3. Bitwarden/Vaultwarden metadata check — skips secrets with `renderable_to_env=False`
- **Admin key validation** — `cmd_seed_admin()` probes the provider API before
  storing admin credentials, preventing silent storage of invalid admin keys.
- **`SecretMetadata`** extended with `secret_class`, `renderable_to_env`,
  `rotation_supported` (`auto`/`hybrid`/`manual`), and `revocation_supported`.

### Security — Structured Validation

- **`ValidationResult`** frozen dataclass replaces bare `bool` from all
  `validate_new_key()` methods. Fields: `valid`, `reason_class`, `detail`,
  `http_status`, `retry_recommended`, `retry_after_seconds`.
- **`ValidationReason`** enum with 10 values: `OK`, `NETWORK_ERROR`, `TIMEOUT`,
  `AUTH_DENIED`, `RATE_LIMITED`, `FORBIDDEN`, `SERVER_ERROR`, `INVALID_FORMAT`,
  `SDK_UNAVAILABLE`, `UNKNOWN`. Transient reasons are retryable; permanent
  ones are returned immediately.
- All 5 provider rotators (OpenAI, Anthropic, Google, DeepSeek, GitHub) parse
  provider-specific HTTP/SDK exceptions into typed `ValidationReason` values.
  Error messages now say "candidate key unusable" instead of claiming "expired".

### Reliability — Retry, Rollback, Locking, Orphan Cleanup

- **`validate_with_retry()`** in `BaseRotator` — exponential backoff (max 3
  retries, 30s cap) on `NETWORK_ERROR`, `TIMEOUT`, `RATE_LIMITED`, `SERVER_ERROR`.
  Non-retryable failures (`AUTH_DENIED`, `FORBIDDEN`, `INVALID_FORMAT`) return
  immediately.
- **Rollback** — all 5 rotators now call `backup_secret()` before storing a
  candidate and `restore_secret()` on smoke-test or env-render failure, reverting
  to the previous key.
- **Per-provider locking** (`security/lockfile.py`) — `fcntl.flock` advisory
  locks prevent concurrent rotations of the same provider. Two terminals running
  `hermes-key-rotate --provider openai` simultaneously will serialize.
- **Orphaned key cleanup** — `cleanup_orphaned_key()` on `BaseRotator`. When an
  auto-created key (OpenAI Admin API, Anthropic Admin API, Google API Keys API)
  fails validation, the orphan is deleted from the provider to avoid credential
  leaks.
- **`revoke_key()`** on all 5 rotators. OpenAI/Anthropic delegate to existing
  delete/archive methods. Google/DeepSeek/GitHub return `False` (manual action).

### Rotation State Machine

- **`RotationPhase`** enum — 14 active states (`STARTED` → `LOCK_ACQUIRED` →
  `PREFLIGHT_OK` → … → `COMPLETED`) + 12 failure states (`FAILED_VALIDATE`,
  `FAILED_SMOKE_TEST`, `ROLLED_BACK`, `MANUAL_ACTION_REQUIRED`, etc.).
- **`RotationState`** checkpoint-able dataclass — persisted to
  `~/.hermes/rotation_checkpoints/<provider>.json` after each phase transition.
  NEVER stores raw keys — only SHA-256 fingerprints.
- **`RotationRunner`** wraps a provider rotator and executes phases sequentially
  with checkpoints. Enables crash recovery via `hermes-key-rotate resume`.
- **Phase-tracked audit** — `write_rotation_phase_event()` writes structured
  JSONL events with `operation=rotation.<phase>` for filtering.

### CLI — Subcommands, Parallel Rotation, Emergency Mode

- **6 new subcommands** (backward-compatible — all existing flat flags unchanged):
  - `hermes-key-rotate rotate --provider <p> [--manual-new-key-stdin] [--parallel]`
  - `hermes-key-rotate seed-admin --provider <p> --project-id/workspace-id/project-number`
  - `hermes-key-rotate emergency --provider <p> [--revoke-only]`
  - `hermes-key-rotate resume --provider <p>`
  - `hermes-key-rotate validate --provider <p>` (reads key from stdin, validates only)
  - `hermes-key-rotate render-env [--dry-run]`
- **Parallel rotation** — `rotate --provider all --parallel` uses
  `ThreadPoolExecutor` with per-provider locks for concurrent rotation.
- **Emergency-compromise mode** — `emergency --provider <p>` revokes the
  compromised key immediately, accepts a replacement via stdin, performs
  minimal smoke test, and renders env. Requires `--yes-i-understand-downtime-risk`.
  `--revoke-only` revokes without replacing (service will be down).
- **`--admin-key-stdin`** stores admin credentials (`hermes/<p>/admin_key` +
  project/workspace ID) in Bitwarden/Vaultwarden with `secret_class=admin`.
- **`hermes-key-rotate --provider all`** now collects per-provider errors without
  aborting the entire batch.

### Fixed

- **Ping command hardcoded assistant ID** — `hermes-assistant-manager ping`
  ignored the CLI's `assistant_id` argument and always looked up `"assistant-id"`.
- **Ping error messages swallowed** — the `{ok, command, result}` envelope hid
  nested errors under `.result.error`, showing `ERROR: unknown` for all failures.
- **Provider rotator class name typos** — `GithubRotator` → `GitHubRotator`,
  `DeepseekRotator` → `DeepSeekRotator`. Batch `--provider all` no longer
  crashes on the 4th provider.
- **`bw create item` base64 encoding** — Bitwarden CLI >= 2026.5.0 requires
  base64-encoded JSON as a positional argument instead of raw JSON on stdin.
  Fixed in `bitwarden_cli_client.py` `create_item()` and `edit_item()`.
- **`--provider` rotator wiring** — the `--provider` flag was parsed but never
  connected to any rotator logic. Now wired to `cmd_rotate()` with provider
  discovery via `importlib`.

### Added

- **Env auto-loading in ping** — `hermes-assistant-manager ping` loads
  `~/.hermes/.env` before checking endpoint env vars.
- **Gitignore `**/CLAUDE.md`\*\* — CLAUDE.md files excluded from git sync.
- **`security/lockfile.py`** — per-provider advisory file locking.
- **`providers/rotation_state_machine.py`** — typed state machine with
  checkpointing and crash recovery.

## [0.2.0] — 2026-06-04

### Initial Public Release

**Provider Adapters** — 5 LLM providers (OpenAI, Anthropic, Google Gemini,
GitHub, DeepSeek) with shared redaction pipeline.

**Key Rotation** — Two-phase rotation (store → smoke → activate → revoke)
backed by self-hosted Vaultwarden/Bitwarden. Provider rotators for OpenAI
(full-auto), Google Gemini (full-auto), Anthropic (partial), DeepSeek
(semi-manual), and GitHub (token minting).

**Usage Metrics** — Concurrent provider health checks, rate limits, cost
telemetry, CLI version probes, with rich terminal and JSON output.

**Route Manager** — CLI for managing Hermes routing configuration (primary,
utility, auxiliary, fallback chains) with profile presets (cheap, balanced,
max-quality).

**Image Routes** — Separate image generation routing layer (ComfyUI local,
Gemini Image, OpenAI DALL-E/gpt-image, FAL.ai) with priority-based fallback.

**Assistant Manager** — Config-driven remote Hermes agent delegation with
capability-based policy, result sanitization, and JSONL audit trail.

**MCP Auditor** — Security audit for MCP servers and tools with risk
classification and atomic whitelisting.

**Security** — Bitwarden/Vaultwarden secret backend (3 auth modes), 16-pattern redaction,
secret scanner gate, atomic env writes (chmod 600 + fsync), safe Bitwarden CLI
wrapper, SHA-256 fingerprinting.

**Environment Projection** — Bootstrap `.env` → Bitwarden/Vaultwarden → `.env.generated`
pipeline with 23 env var → secret ref mappings.

**Tests** — 92 tests across security, snapshots, CLI integration, and
simulator scenarios.

[0.2.0]: https://github.com/your-org/hermes-ops-kit/releases/tag/v0.2.0
