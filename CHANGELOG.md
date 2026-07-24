# Changelog

All notable changes to Hermes Ops Kit.

## [Unreleased]

### Hermes Agent v0.19.0 (Quicksilver) Compatibility — v2026.7.20

Adapted hermes-ops-kit to Hermes Agent v0.19.0 "Quicksilver" (release
`v2026.7.20`). Audit-driven — see `docs/compat-audit.md`, produced by the new
`hermes-compat-audit` skill. Changes stay within ops-kit's operational/security
lane; capabilities now owned by Hermes core (delivery ledger, sessions export,
smart approvals, profile routing, LM Studio JIT) are deliberately NOT
reimplemented (integrate, don't duplicate — `CLAUDE.md` scope boundary).

**Providers**
- **Fireworks AI** — first-class provider: `providers/fireworks_adapter.py` +
  `fireworks_rotator.py` (OpenAI-compatible, `https://api.fireworks.ai/inference/v1`),
  wired into bridge, usage_metrics, budget (`paid_low`), key rotation, env projection.
- **DeepInfra** — first-class provider: `providers/deepinfra_adapter.py` +
  `deepinfra_rotator.py` (`https://api.deepinfra.com/v1/openai`), full registry wiring.
- Upstage Solar: investigated but **not wired** — no adapter exists in the core
  checkout (v0.18.2); the `hermes-compat-audit` skill will catch it when core adds it.

**Security**
- `security/credential_read_guard.py` — shared credential-read guard mirroring
  core `agent/file_safety.raise_if_read_blocked` (#57698); gates model/user-supplied
  local-file reads in image-gen adapters (gemini reference images, comfyui workflow).
  Best-effort delegates to core; local denylist fallback.
- Redaction: added Fireworks (`fw-`/`fw_`/`fpk_`), xAI/Grok (`xai-`), Fal.ai (`fal_`)
  prefixes to `security/redaction.py` (mirror of core `agent/redact.py`).

**Cost governance**
- `cost_governor/plan_status.py` — Nous plan allowance reader integrating core
  `hermes_cli.nous_account.get_nous_portal_account_info` (integrate, not reimplement).
- `cost_governor/budget.py` — allowance-aware gating: blocks paid_premium/standard
  routes when the Nous plan is exhausted (recommends free tier); `evaluate_budget`
  exposes a `plan_allowance` summary.

**Route config**
- `hermes_route_manager.py` — `reasoning_effort` tiers (mirrors core
  `VALID_REASONING_EFFORTS`: none/minimal/low/medium/high/xhigh/max/ultra) in
  BUILTIN_PROFILES + `--effort` flag on `apply-profile` (writes
  `agent.reasoning_effort`).
- `route_verifier.py` — env_map now recognizes nvidia + zai (nvidia was missing —
  AUX routes to nvidia falsely reported "no credential"); `commands.py` documents
  the credential-check vs usage-registry layering (openrouter/zai are OpenAI-compat,
  credential-check only).

**Compatibility tooling**
- `skills/hermes-compat-audit/SKILL.md` — skill that monitors
  https://github.com/NousResearch/hermes-agent/releases vs the plugin, runs a 3-pass
  audit (coverage map → gap analysis → implementation plan) updating
  `docs/compat-audit.md`.
- `scripts/hermes_compat_audit.py` — grounded GitHub-release fetcher + manifest
  comparison (defensive on network/rate-limit).
- `config/compat.yaml` — compatibility manifest (target version + per-feature coverage).

**Tests**
- 257 tests passing (was 212). Added: redaction (6), reasoning_effort (4),
  route_verifier creds (3), credential-read guard (10), plan_status (7), budget
  allowance (8), compat-audit (4), provider adapters (3).

**Post-review hardening** — an 8-agent adversarial review (security /
correctness / enterprise-quality / scope-compliance) confirmed 19 findings
(0 critical/high); all security & correctness findings fixed:
- Adapters (fireworks/deepinfra/deepseek): redact the `structured` field too
  (was a redaction bypass for `op_extract`).
- Rotators (fireworks/deepinfra/deepseek): `redact()` exception detail and
  smoke-test strings at the source.
- `hermes_key_rotate.py` legacy `--provider` uses `PROVIDER_CHOICES` (was
  hardcoded — rejected nvidia/fireworks/deepinfra).
- `budget.py`: read nested `budgets.daily_usd`/`monthly_usd` (silent
  config-ignored bug — operator limits were ignored).
- `credential_read_guard.py`: dual-root check (profile mode) + narrow
  `skills/.hub` (core parity).
- `local_comfyui.py`: dropped the guard (operator-configured path, outside the
  guard's model-supplied-path contract).
- Redaction: JSON secret-field pattern (opaque DeepInfra keys).
- Base-URL env override: fireworks + deepseek now honor `*_BASE_URL` in adapter
  + health-check (was split-brain vs the rotator).
- `commands.py` doctor fallback derives from `usage_metrics_v2.PROVIDERS`;
  `scripts/hermes_compat_audit.py` handles empty/non-list releases + reads URL
  from `config/compat.yaml`.
- Deferred (follow-up): factor OpenAI-compat commonality onto `BaseRotator` /
  shared ops module (~600 duplicated lines); read `block_classes` from budget.yaml.
  → Both completed in the Tier 3 section below.

### Tier 3 — Architectural (v0.19.0 compatibility, enterprise-grade)

- **SecretSource bridge** (`security/secret_source_bridge.py`) — ops-kit now
  queries Hermes core's SecretSource provenance
  (`hermes_cli.env_loader._SECRET_SOURCES`) instead of duplicating the read path.
  Core's SecretSource is read-only/bulk (no per-secret `get`), so the bridge
  queries provenance and `env/render_env.py` annotates `# source=vaultwarden:<ref>`
  + surfaces `also-provided-by=core:<label>` conflicts (core wins at runtime).
  WRITES (rotation) stay on Vaultwarden — core has no write API.
- **`env/render_env.py`** — fixed a latent bug (the `env_projection:` section of
  `config/env_projection.yaml` was never loaded — only `deny_render` was parsed,
  so FIREWORKS/DEEPINFRA additions were silently ignored). Now loads via
  `_load_env_projection()` + provenance/conflict annotation.
- **OpenAI-compat dedup** (`providers/_openai_compat_ops.py`) — factored the
  ~95% identical adapter/rotator triplet (deepseek/fireworks/deepinfra) into a
  shared `OpenAICompatAdapter` + `run_cli` + `OpenAICompatRotator` (8-branch
  validate ladder + 9-step rotate). The 6 per-provider files are now thin
  subclasses (~-1000 lines). DeepSeek's reasoner divergence via
  `supports_temperature`/`extract_model` hooks (`extract_warning` derives from
  `extract_model` — no drift). Subprocess boundary + bridge contract preserved.
- **`cost_governor/budget.py`** — `block_classes` read from
  `config/budget.yaml` `actions.block.block_classes` (single source of truth;
  was hardcoded). Used by both allowance-gating and spend-block branches.
- **Adversarial review (8-agent)** — 6 findings, all fixed: structured-field
  redaction test, rotate/smoke rollback branch tests (smoke-fail, render-fail,
  QUOTA_OR_BILLING), `extract_warning` DRY (derive from `extract_model`), removed
  dead `core_provides`, adapter/rotator drift-detection test.
- **Tests** — 280 passing (was 257). Added: render_env (env_projection + provenance),
  secret_source_bridge, rotator validate-ladder + rotate branches, adapter
  structured-redaction + drift, deepseek reasoner hooks, block_classes-from-config,
  cross-registry provider drift detection.

### Code-review hardening (post-commit `/code-review max ultracode`)

A `/code-review max ultracode` pass found 9 issues in the committed work; 7 fixed
(2 nits skipped: mutable class-attr is theoretical + all subclasses override; `fal_`
threshold mirrors core's `{10,}` — diverging would break alignment):
- `op_extract`: catch `TypeError` (null API content) alongside `JSONDecodeError` — a
  successful response with null content now returns `ok=True` + `parse_error` instead
  of crashing (affected all 3 OpenAI-compat providers).
- `rotate`: wrap `restore_secret` in the smoke-fail rollback (was unwrapped — a
  restore failure could propagate, skip audit, + leave the bad key); capture
  `rollback_error` + still audit `smoke_test_failed`.
- `check_fireworks/deepinfra/deepseek`: guard `data.get` on a null `/models` response.
- `credential_read_guard`: treat `HERMES_HOME=""` as unset (don't check paths vs CWD).
- `budget`: honor an explicit empty `block_classes` (`[]` was silently overridden by
  the default via `or`); `is None` checks in evaluate_budget + check_route_allowed.
- `render_env`: `_KEY_VAL_RE` tolerates inline comments on env_projection entries.
- `credential_read_guard`: `Optional[callable]` → `Optional[Callable[..., Any]]`.
- +3 regression tests (null content, empty block_classes, restore-failure rollback).
- **Tests** — 283 passing.

### Security — Hermes Session Scan Integration

- Register the cached plugin security scan on Hermes's supported
  `on_session_start` lifecycle event.
- Keep session scans report-only and document `hermes-ops-kit preflight` as the
  required mechanism for excluding unsafe plugins before Hermes loads them.
- Remove the invalid `config.yaml` plugin-hook installation guidance; Hermes
  reserves the `hooks:` configuration block for executable shell hooks.
- Install and verify the project `dev` extra from `install.sh`, ensuring Pillow
  and `ruff` are available after an approved plugin installation.
- Harden `install.sh` for Linux/macOS/WSL with platform, Python, and pip checks,
  PEP 668-safe user installs, virtual-environment support, and portable
  `/usr/bin/env bash` wrappers pinned to the installation interpreter.
- Add Ubuntu and macOS CI validation for installer Bash syntax, plus ShellCheck
  and an idempotent end-to-end installation test on Linux.
- Resolve the fresh-install self-scan trust loop: complete scans of the official
  repository may proceed despite expected privileged findings, while custom
  repository overrides remain fail-closed unless explicitly trusted.
- Install and verify Semgrep and Bandit automatically, and install Gitleaks
  through Homebrew/Linuxbrew or pinned Go module version `v8.30.1`.
- Add `install-wsl.sh` to bootstrap Ubuntu/Debian WSL prerequisites before
  delegating to the cross-platform installer.
- Make installer and quickstart output explicit that a normal gateway restart
  does not run preflight, and show the safe restart command.

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

[0.2.0]: https://github.com/redoracle/hermes-ops-kit/releases/tag/v0.2.0
