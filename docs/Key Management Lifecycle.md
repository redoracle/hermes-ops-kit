---
title: Key Management Lifecycle
tags:
  [hermes, ops-kit, security, key-management, vaultwarden, bitwarden, rotation]
created: 2026-06-04
modified: 2026-06-04
---

# Key Management Lifecycle

Complete lifecycle of secrets managed by Hermes Ops Kit through Bitwarden/Vaultwarden — from initial seeding to rotation to revocation.

## Secret Classification

Every secret stored in Vaultwarden has a classification that determines its lifecycle behavior.

| Class     | Examples                                                           | Renderable to `.env.generated`? | Rotation                | Revocation             |
| --------- | ------------------------------------------------------------------ | ------------------------------- | ----------------------- | ---------------------- |
| `admin`   | `admin_key`, `admin_secret`, `admin_token`, `service_account_json` | **Never** (3-layer denylist)    | Via `--admin` flag only | Auto (where supported) |
| `runtime` | `api_key`, `token`, `copilot_token`, `app_private_key`             | **Yes**                         | Primary rotation target | Auto or manual         |
| `config`  | `project_id`, `workspace_id`, `base_url`, `model`, `api_key_id`    | **Yes**                         | Rarely needed           | Manual                 |

### Runtime vs Admin Separation

Admin credentials are stored in Vaultwarden with `secret_class=admin` and `renderable_to_env=false`. Three independent gates prevent them from ever reaching `.env.generated`:

1. **Hard `deny_render` list** in `config/env_projection.yaml` — named admin refs hard-blocked
2. **Path-segment classification** — any ref containing `admin_key`/`admin_secret`/`admin_token`/`service_account_json` auto-blocked
3. **Vaultwarden metadata flag** — `renderable_to_env: false` on admin-class secrets

See: [[Architecture]] (Data Flow: Env Rendering Safety section), [[Threat Model]] (3-layer denylist)

---

## Secret Inventory by Provider

### OpenAI

| Secret Ref                         | Class   | Vaultwarden Item                   | Env Var             | Rotatable | Revocable |
| ---------------------------------- | ------- | ---------------------------------- | ------------------- | --------- | --------- |
| `hermes/openai/api_key`            | runtime | `Hermes/OpenAI/API_KEY`            | `OPENAI_API_KEY`    | ✅        | ✅ Auto   |
| `hermes/openai/admin_key`          | admin   | `Hermes/OpenAI/ADMIN_KEY`          | `<DENIED>`          | ✅        | ✅ Auto   |
| `hermes/openai/project_id`         | config  | `Hermes/OpenAI/PROJECT_ID`         | `OPENAI_PROJECT_ID` | —         | Manual    |
| `hermes/openai/service_account_id` | config  | `Hermes/OpenAI/SERVICE_ACCOUNT_ID` | —                   | —         | Manual    |

**Rotation:** `admin-hybrid` — auto-creates key via Admin API (`POST /v1/api_keys`), names it `hermes-<env>-openai-<timestamp>`.
**Revocation:** `DELETE /v1/api_keys/{id}` via admin credential.

### Anthropic

| Secret Ref                      | Class   | Vaultwarden Item                | Env Var             | Rotatable | Revocable |
| ------------------------------- | ------- | ------------------------------- | ------------------- | --------- | --------- |
| `hermes/anthropic/api_key`      | runtime | `Hermes/Anthropic/API_KEY`      | `ANTHROPIC_API_KEY` | ✅        | ✅ Auto   |
| `hermes/anthropic/admin_key`    | admin   | `Hermes/Anthropic/ADMIN_KEY`    | `<DENIED>`          | ✅        | ✅ Auto   |
| `hermes/anthropic/workspace_id` | config  | `Hermes/Anthropic/WORKSPACE_ID` | —                   | —         | Manual    |
| `hermes/anthropic/api_key_id`   | config  | `Hermes/Anthropic/API_KEY_ID`   | —                   | —         | Manual    |

**Rotation:** `admin-hybrid` — auto-creates key via Admin API (`POST /v1/api_keys`).
**Revocation:** `POST /v1/api_keys/{id}/archive` (archive, not delete).

### Google / Gemini

| Secret Ref                                   | Class   | Vaultwarden Item                             | Env Var                               | Rotatable | Revocable |
| -------------------------------------------- | ------- | -------------------------------------------- | ------------------------------------- | --------- | --------- |
| `hermes/google/gemini_api_key`               | runtime | `Hermes/Google/GEMINI_API_KEY`               | `GEMINI_API_KEY`                      | ✅        | ✅ Auto   |
| `hermes/google/api_key_id`                   | config  | `Hermes/Google/API_KEY_ID`                   | —                                     | —         | Manual    |
| `hermes/google/project_id`                   | config  | `Hermes/Google/PROJECT_ID`                   | —                                     | —         | Manual    |
| `hermes/google/project_number`               | config  | `Hermes/Google/PROJECT_NUMBER`               | —                                     | —         | Manual    |
| `hermes/google/application_credentials_json` | admin   | `Hermes/Google/APPLICATION_CREDENTIALS_JSON` | `GOOGLE_APPLICATION_CREDENTIALS_JSON` | ✅        | Manual    |

**Rotation:** `admin-hybrid` — auto-creates key via API Keys API.
**Revocation:** `DELETE` via google-auth ADC.
**Note:** `GEMINI_API_KEY` is the canonical env var. `GOOGLE_API_KEY` is no longer rendered by default.

### DeepSeek

| Secret Ref                           | Class   | Vaultwarden Item                     | Env Var                       | Rotatable | Revocable |
| ------------------------------------ | ------- | ------------------------------------ | ----------------------------- | --------- | --------- |
| `hermes/deepseek/api_key`            | runtime | `Hermes/DeepSeek/API_KEY`            | `DEEPSEEK_API_KEY`            | ✅        | ❌ Manual |
| `hermes/deepseek/base_url`           | config  | `Hermes/DeepSeek/BASE_URL`           | `DEEPSEEK_BASE_URL`           | —         | Manual    |
| `hermes/deepseek/anthropic_base_url` | config  | `Hermes/DeepSeek/ANTHROPIC_BASE_URL` | `DEEPSEEK_ANTHROPIC_BASE_URL` | —         | Manual    |
| `hermes/deepseek/default_model`      | config  | `Hermes/DeepSeek/DEFAULT_MODEL`      | `DEEPSEEK_DEFAULT_MODEL`      | —         | Manual    |
| `hermes/deepseek/reasoning_model`    | config  | `Hermes/DeepSeek/REASONING_MODEL`    | `DEEPSEEK_REASONING_MODEL`    | —         | Manual    |

**Rotation:** `manual-new-key` only — no admin API. User must provide key via stdin.
**Revocation:** Manual action required — log into DeepSeek dashboard and delete the old key.

### NVIDIA NIM

| Secret Ref                  | Class   | Vaultwarden Item          | Env Var              | Rotatable | Revocable |
| --------------------------- | ------- | ------------------------- | -------------------- | --------- | --------- |
| `hermes/nvidia/api_key`     | runtime | `Hermes/NVIDIA/API_KEY`   | `NVIDIA_API_KEY`     | ✅        | ❌ Manual |
| `hermes/nvidia/base_url`    | config  | `Hermes/NVIDIA/BASE_URL`  | `NVIDIA_BASE_URL`    | —         | Manual    |

**Rotation:** `manual-new-key` only — no admin API. User must provide key via stdin.
**Revocation:** Manual action required — log into NVIDIA Build console (https://build.nvidia.com) and delete the old key.
**Rate limits:** Captured from `x-ratelimit-*` response headers on `/v1/models` probe.

### GitHub

| Secret Ref                      | Class   | Vaultwarden Item                | Env Var                    | Rotatable | Revocable |
| ------------------------------- | ------- | ------------------------------- | -------------------------- | --------- | --------- |
| `hermes/github/token`           | runtime | `Hermes/GitHub/TOKEN`           | `GITHUB_TOKEN`, `GH_TOKEN` | ✅        | ❌ Manual |
| `hermes/github/copilot_token`   | runtime | `Hermes/GitHub/COPILOT_TOKEN`   | —                          | ✅        | ❌ Manual |
| `hermes/github/app_private_key` | runtime | `Hermes/GitHub/APP_PRIVATE_KEY` | —                          | —         | ❌ Manual |
| `hermes/github/app_id`          | config  | `Hermes/GitHub/APP_ID`          | `GITHUB_APP_ID`            | —         | Manual    |
| `hermes/github/installation_id` | config  | `Hermes/GitHub/INSTALLATION_ID` | `GITHUB_INSTALLATION_ID`   | —         | Manual    |

**Rotation:** `manual-new-key` only — no admin API. Generate new token at github.com/settings/tokens.
**Revocation:** Manual action required — revoke at github.com/settings/tokens.

### Assistants (Remote Agent Delegation)

| Secret Ref                                       | Class   | Vaultwarden Item                              | Env Var                     | Rotatable | Revocable |
| ------------------------------------------------ | ------- | --------------------------------------------- | --------------------------- | --------- | --------- |
| `hermes/assistants/assistant-id/api_key`         | runtime | `Hermes/Assistants/Assistant/API_KEY`         | `ASSISTANT_API_KEY`         | ✅        | Manual    |
| `hermes/assistants/assistant-id/api_base`        | config  | `Hermes/Assistants/Assistant/API_BASE`        | `ASSISTANT_API_BASE`        | —         | Manual    |
| `hermes/assistants/assistant-id/model`           | config  | `Hermes/Assistants/Assistant/MODEL`           | `ASSISTANT_MODEL`           | —         | Manual    |
| `hermes/assistants/assistant-id/timeout_seconds` | config  | `Hermes/Assistants/Assistant/TIMEOUT_SECONDS` | `ASSISTANT_TIMEOUT_SECONDS` | —         | Manual    |

---

## Rotation Modes

| Mode               | CLI Flag                 | Description                                                                            | Providers                 |
| ------------------ | ------------------------ | -------------------------------------------------------------------------------------- | ------------------------- |
| **manual-new-key** | `--manual-new-key-stdin` | User pipes key via stdin. Key never echoed.                                            | All                       |
| **admin-hybrid**   | (default)                | Uses admin API to auto-create key, validate, store. Requires admin key in Vaultwarden. | OpenAI, Anthropic, Google |
| **bootstrap**      | `seed-from-env`          | Migrates keys from `~/.hermes/.env` into Vaultwarden. Initial setup only.              | All                       |
| **emergency**      | `--emergency`            | Immediate revoke + replace. Skips backup retention.                                    | All                       |

### Manual vs Auto-Rotation Matrix

| Provider  | Auto-Create     | Auto-Validate    | Auto-Revoke                   | Orphan Cleanup |
| --------- | --------------- | ---------------- | ----------------------------- | -------------- |
| OpenAI    | ✅ Admin API    | ✅ Live API call | ✅ `DELETE /v1/api_keys/{id}` | ✅             |
| Anthropic | ✅ Admin API    | ✅ Live API call | ✅ `POST .../archive`         | ✅             |
| Google    | ✅ API Keys API | ✅ Live API call | ✅ `DELETE` via ADC           | ✅             |
| DeepSeek  | ❌ Manual only  | ✅ Live API call | ❌ Dashboard                  | ❌             |
| NVIDIA    | ❌ Manual only  | ✅ Live API call | ❌ NVIDIA Build console       | ❌             |
| GitHub    | ❌ Manual only  | ✅ Live API call | ❌ Dashboard                  | ❌             |

---

## 14-Phase Rotation State Machine

```
STARTED
  → LOCK_ACQUIRED              (per-provider fcntl.flock)
  → PREFLIGHT_OK                (backend health, connectivity)
  → OLD_KEY_FINGERPRINTED      (sha256:xxx, last4:Ab7Q — no raw key)
  → CANDIDATE_CREATED_OR_RECEIVED (auto-create or stdin)
  → CANDIDATE_VALIDATED         (validate_with_retry: 3x backoff)
  → SECRET_STAGED               (stored in Vaultwarden, read-after-write verified)
  → SMOKE_TEST_PASSED           (live API call with staged key)
  → ENV_RENDERED_ATOMICALLY     (temp → chmod 600 → fsync → rename)
  → DEPLOYMENT_RELOADED         (Hermes Agent picks up new .env.generated)
  → POST_DEPLOY_HEALTH_OK       (usage_metrics_v2 probe)
  → OLD_KEY_REVOKED_OR_DEFERRED (auto for OpenAI/Anthropic/Google, manual for others)
  → AUDIT_WRITTEN               (JSONL fingerprint-only event)
  → COMPLETED                   (checkpoint deleted)
```

### Failure States

| Phase        | Failure State            | Recovery                                               |
| ------------ | ------------------------ | ------------------------------------------------------ |
| Preflight    | `FAILED_PREFLIGHT`       | Fix connectivity/auth, retry                           |
| Key creation | `FAILED_CREATE`          | Check admin key permissions                            |
| Validation   | `FAILED_VALIDATE`        | Key is invalid — get a new one                         |
| Store        | `FAILED_STORE`           | Check Vaultwarden connectivity                         |
| Smoke test   | `FAILED_SMOKE_TEST`      | **Auto-rollback**: `restore_secret()` restores old key |
| Env render   | `FAILED_ENV_RENDER`      | Check denylist, permissions                            |
| Reload       | `FAILED_RELOAD`          | Restart Hermes Agent                                   |
| Health check | `FAILED_HEALTH_CHECK`    | Provider API may be down                               |
| Revoke       | `FAILED_REVOKE`          | `MANUAL_ACTION_REQUIRED` — instructions printed        |
| Audit        | `FAILED_AUDIT`           | Non-fatal — rotation succeeded, audit trail incomplete |
| Any          | `ROLLED_BACK`            | Old key restored, new key invalidated                  |
| Any          | `MANUAL_ACTION_REQUIRED` | Console instructions printed, checkpoint saved         |

### Crash Recovery

Each phase transition writes a fingerprint-only checkpoint to `~/.hermes/rotation_checkpoints/<provider>.json`. If rotation is interrupted (power loss, crash):

```bash
hermes-key-rotate resume --provider <provider>
```

Resumes from the last completed phase. Checkpoints contain no raw key material — only `sha256:xxx` fingerprints and `last4` chars.

---

## Key Lifecycle Operations

### Seed (Initial Bootstrap)

```bash
# Seed all keys from .env into Vaultwarden
hermes-key-rotate seed-from-env

# Seed a single provider
echo "sk-..." | hermes-key-rotate rotate --provider openai --manual-new-key-stdin
```

### Rotate (Scheduled)

```bash
# Auto-rotate (providers with admin API support)
hermes-key-rotate rotate --provider openai

# Manual rotate
echo "sk-new-key" | hermes-key-rotate rotate --provider deepseek --manual-new-key-stdin

# Dry-run (preview without mutating)
hermes-key-rotate rotate --provider openai --dry-run
```

### Rotate (Emergency)

```bash
# Immediate revoke + replace on key compromise
echo "sk-emergency-key" | hermes-key-rotate rotate --provider openai --manual-new-key-stdin --emergency
```

### Validate

```bash
# Check if a key works before storing it
hermes-key-rotate validate --provider openai --key "sk-..."
```

### Status

```bash
# All provider keys — fingerprints + age
hermes-key-rotate --status

# Single provider
hermes-key-rotate --status --provider anthropic
```

### Render Env

```bash
# Regenerate ~/.hermes/.env.generated from Vaultwarden
hermes-key-rotate --render-env
```

### Backup & Restore

```bash
# Backup is automatic during rotation (backup_secret() before set_secret())
# Manual restore after failed rotation:
hermes-key-rotate rollback --provider openai
```

### Revoke (Manual)

```bash
# For providers without auto-revocation (DeepSeek, GitHub):
# Console prints instructions after rotation:
#   "MANUAL ACTION REQUIRED: Log into deepseek.com → API Keys → delete key ending in Ab7Q"
```

---

## Env Rendering Pipeline

```text
~/.hermes/.env (4 bootstrap vars, chmod 600)
  → bw unlock (TLS 1.3 → self-hosted Vaultwarden)
  → Vaultwarden (API keys encrypted at rest, AES-256)
  → hermes-key-rotate render-env
  → 3-layer denylist gate
  → ~/.hermes/.env.generated (runtime keys only, chmod 600, atomic write)
  → Hermes Agent reads at startup
```

### What Gets Rendered

| Secret Class | Rendered?     | Gate                       |
| ------------ | ------------- | -------------------------- |
| `runtime`    | ✅ Yes        | Passes all 3 gates         |
| `config`     | ✅ Yes        | Passes all 3 gates         |
| `admin`      | ❌ `<DENIED>` | Blocked at Gate 1, 2, or 3 |

---

## Related

- [[Architecture]] — full module map, rotation data flow, env rendering pipeline
- [[Threat Model]] — trust boundaries, rotation threat analysis, atomic write guarantees
- [[Hermes Compatibility]] — secret zero architecture, security model, migration path
- [[Route Profile Design]] — route architecture
- [[Operations Runbook]] — incident response: key compromise, Vaultwarden outage, rate limiting
- [[Quickstart]] — getting started guide
- [[Architecture Decisions]] — ADR-002 (Vaultwarden as secret store), ADR-003 (3-layer denylist), ADR-004 (two-phase rotation)
- `security/vaultwarden_backend.py` — full VaultwardenSecretBackend implementation
- `security/secret_backend.py` — SecretBackend protocol + error hierarchy
- `providers/rotation_state_machine.py` — 14-phase RotationPhase + RotationRunner
- `config/env_projection.yaml` — ENV_VAR → secret ref mapping + deny_render list
