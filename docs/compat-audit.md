# Hermes Ops Kit — Compatibility Audit Report

Living document. Each audit run appends a dated pass. Do not rewrite history.

Produced by the `hermes-compat-audit` skill (`skills/hermes-compat-audit/SKILL.md`)
using grounded data from `scripts/hermes_compat_audit.py` and `config/compat.yaml`.

## Baseline

- **Target Hermes:** 0.19.0 (Quicksilver) — from `config/compat.yaml`
- **Latest release fetched:** `v2026.7.20` (2026-07-20T18:35:55Z) — from GitHub API
- **Match status:** ✅ MATCH (release name contains "0.19.0")
- **Coverage tally:** 6 covered · 2 partial · 1 missing · 5 not-ops-kit-lane

> Hermes core uses calendar versioning for git tags (`v2026.7.20`); "0.19.0" is
> the changelog's semver label for the same Quicksilver release.

---

## Pass 1 — Coverage map (2026-07-24)

| Area | Lane | Status | Evidence (file:line) |
| --- | --- | --- | --- |
| SecretSource (Bitwarden+1Password, multi-vault) | secret lifecycle | partial | `security/secret_backend.py:171` (SecretBackend); 1Password+multi-vault owned by core `agent/secret_sources/` |
| Fireworks AI provider | provider routing | covered | `providers/fireworks_adapter.py`, `providers/fireworks_rotator.py`, `bridge.py:21` PROVIDERS, `usage_metrics_v2.py:39` |
| DeepInfra provider | provider routing | covered | `providers/deepinfra_adapter.py`, `providers/deepinfra_rotator.py`, registry wired |
| Upstage Solar provider | provider routing | missing | not present in core checkout (v0.18.2); re-check when core adds it |
| Reasoning effort tiers (max/ultra) | route config | covered | `hermes_route_manager.py` `REASONING_EFFORTS` + `BUILTIN_PROFILES` + `--effort` flag |
| Fireworks redaction prefixes | redaction | covered | `security/redaction.py` (`fw-`/`fw_`/`fpk_`, `xai-`, `fal_`) |
| Credential-read guard (image-gen local files) | security | covered | `security/credential_read_guard.py` + `image_routes/adapters/gemini_image.py`, `local_comfyui.py` |
| Nous plan allowance governance | cost governance | covered | `cost_governor/plan_status.py` + `cost_governor/budget.py` (allowance gating) |
| Delivery-obligation ledger | messaging | not-ops-kit-lane | Hermes core gateway (`state.db` redelivery) |
| Sessions export (Quarto/HF/lineage) | conversation export | not-ops-kit-lane | Hermes core `hermes_cli/session_export_*.py` |
| Smart approvals / deny rules / yolo | runtime approval | not-ops-kit-lane | Hermes core `tools/approval.py` |
| Profile-based message routing | gateway | not-ops-kit-lane | Hermes core `hermes_cli/profiles.py`; shared boundary for per-profile secrets |
| LM Studio JIT loading | model dispatch | not-ops-kit-lane | Hermes core model dispatch |
| Live transcripts + durable delegation | assistant delegation | partial | `assistants/tool.py` (remote-HTTP, sync); durable ledger = core `delegate_task` |

---

## Pass 2 — Gap analysis (2026-07-24)

| Item | Scope | Effort | Risk | Recommendation |
| --- | --- | --- | --- | --- |
| SecretSource (1Password + multi-vault + provenance) | shared-boundary | architectural | HIGH — ops-kit `vaultwarden_backend`+`render_env` parallel-implement core `agent/secret_sources/` (split-brain) | **Defer to core**: thin adapter calling `agent.secret_sources`, progressively retire `vaultwarden_backend.py`/`render_env.py`. Do NOT build an ops-kit 1Password backend (duplicates core). |
| Upstage Solar provider | ops-kit-lane (when core adds it) | refactor | none | Wait for core adapter; re-run this skill when a release adds Upstage. No action now (cannot mirror an adapter that doesn't exist). |
| Live transcripts on remote delegation | ops-kit-lane (optional) | refactor | low | Optional: streaming in `assistants/client.py` + transcript file in `assistants/tool.py` for observability. Durable delivery ledger stays core-lane. |

---

## Pass 3 — Implementation plan (2026-07-24)

### 🟢 Tier 1 — Quick wins (DONE 2026-07-24)
- ✅ Redaction patterns: Fireworks `fw-`/`fw_`/`fpk_`, xAI `xai-`, Fal `fal_` — `security/redaction.py`
- ✅ `reasoning_effort` in route profiles + `--effort` flag — `hermes_route_manager.py`
- ✅ Drift fix: `route_verifier.py` env_map (nvidia/zai) + `commands.py` layering comment
- ✅ Fireworks + DeepInfra adapters/rotators + full registry wiring (bridge, usage_metrics, budget, commands, env_projection, hermes_key_rotate)

### 🟡 Tier 2 — Moderate (DONE 2026-07-24)
- ✅ Shared credential-read guard — `security/credential_read_guard.py` + image adapter refactor
- ✅ Allowance-aware cost governance — `cost_governor/plan_status.py` + `budget.py` (integrates core `hermes_cli.nous_account`)
- ⏭️ (Optional, deferred) Live transcripts on remote assistant delegation

### 🔴 Tier 3 — Architectural (REQUIRES DECISION)
- 🔲 Defer secret fetching to `agent.secret_sources` of Hermes core (thin adapter; retire `vaultwarden_backend.py`/`render_env.py`). Resolves the SecretSource split-brain without duplicating core. Per `CLAUDE.md:8-13` "integrate, don't reimplement."

### ⛔ Do-not (Hermes-core-lane — would duplicate core)
- Delivery-obligation ledger / `state.db` redelivery (messaging)
- Sessions export Quarto/HuggingFace/prompt-only/lineage (conversation export)
- Smart approvals / LLM reviewer / deny rules / yolo (runtime command approval)
- Profile-based gateway routing / LM Studio JIT / MoA dispatch / effort clamping (model dispatch)

### Verification
- Full suite: `python3 -m pytest tests/ -q` (224+ tests)
- Compat audit: `python3 scripts/hermes_compat_audit.py --releases 3`
- Adapter smoke: `python3 providers/fireworks_adapter.py --operation models`

---

## Post-review hardening (2026-07-24)

An 8-agent adversarial review (security / correctness / enterprise-quality /
scope-compliance) confirmed 19 findings (0 critical/high). All security &
correctness findings fixed; larger refactors deferred:

**Fixed**
- Adapters (fireworks/deepinfra/deepseek): redact the `structured` field too
  (same model output as `text` — was a redaction bypass for `op_extract`).
- Rotators (fireworks/deepinfra/deepseek): `redact()` exception `detail` and
  smoke-test strings at the source (was un-redacted, unlike the adapters).
- `hermes_key_rotate.py` legacy `--provider` now uses `PROVIDER_CHOICES`
  (was hardcoded — rejected nvidia/fireworks/deepinfra).
- `budget.py`: read nested `budgets.daily_usd`/`monthly_usd` from budget.yaml
  (silent config-ignored bug — operator limits were ignored).
- `credential_read_guard.py`: dual-root check (profile mode) + narrow
  `skills/.hub` (core parity, unblocks user skill assets).
- `local_comfyui.py`: dropped the guard (workflow_path is operator-configured,
  outside the guard's model-supplied-path contract).
- Redaction: added JSON secret-field pattern (opaque DeepInfra keys).
- Base-URL env override: fireworks + deepseek adapter & health-check now honor
  `*_BASE_URL` like deepinfra (was split-brain vs the rotator).
- `commands.py` doctor fallback derives from `usage_metrics_v2.PROVIDERS`.
- `scripts/hermes_compat_audit.py`: empty/non-list releases handled + URL read
  from `config/compat.yaml`.
- Tests: +11 (json-field redaction, credential-guard dual-root/skills/.hub,
  budget nested-read, compat-audit edge cases, adapter allowlist/key-guard/models).

**Deferred (follow-up)**
- Factor OpenAI-compat commonality onto `BaseRotator` / a shared ops module
  (~600 lines duplicated across the 3 adapter+rotator pairs). Enterprise-quality;
  not a correctness/security bug. Do alongside the next provider addition.
- Read `actions.block.block_classes` from budget.yaml instead of hardcoding
  `("paid_premium","paid_standard")` (values agree today; single-source-of-truth).

---

## Pass 4 — Tier 3 architectural (2026-07-24)

The Pass-3 deferred items are now implemented, plus the SecretSource read-path
integration and a latent render_env bug fix.

| Area | Lane | Status | Evidence |
| --- | --- | --- | --- |
| SecretSource read-path integration | secret lifecycle | covered | `security/secret_source_bridge.py` queries core `hermes_cli.env_loader._SECRET_SOURCES`; `env/render_env.py` annotates `# source=` + core-conflict |
| OpenAI-compat dedup | provider routing | covered | `providers/_openai_compat_ops.py` (OpenAICompatAdapter + run_cli + OpenAICompatRotator); 6 per-provider files are thin subclasses |
| block_classes from config | cost governance | covered | `cost_governor/budget.py` reads `actions.block.block_classes` |
| render_env env_projection load | env rendering | covered | `env/render_env.py:_load_env_projection` (was a latent bug — YAML edits silently ignored) |

**Key design decision (scope-disciplined):** core's `SecretSource` is read-only/bulk
(`fetch(cfg, home_path)`, no per-secret `get`), so ops-kit cannot delegate a single
read. Instead `secret_source_bridge` queries core's *provenance* (which vars core
provides + from which source) and `render_env` annotates/surfaces conflicts — core's
`apply_all` wins at runtime, ops-kit stops silently duplicating the read path.
WRITES (rotation: set/backup/restore) stay on the Vaultwarden `SecretBackend` —
core has no write API. This is integration, not re-implementation.

**Remaining Tier 3 (not started):** full retirement of `vaultwarden_backend.py`/
`render_env.py` is not feasible/desirable — rotation needs a writable backend and
`render_env` is still needed for vars core's SecretSource doesn't provide + for the
post-rotation env re-render. `hermes_export.py` rename not done — it is a breaking
CLI change (`hermes-export` entry point + 7 internal refs) for marginal clarity.
283 tests passing (incl. cross-registry drift detection + code-review regression tests).
