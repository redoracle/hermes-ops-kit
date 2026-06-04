---
title: Architecture Decisions
tags: [hermes, ops-kit, architecture, decisions, adr]
created: 2026-06-04
modified: 2026-06-04
---

# Architecture Decisions

Key architectural decisions for Hermes Ops Kit, their rationale, and consequences.

## ADR-001: Subprocess-Based Provider Adapters

**Status:** Accepted (2026-05)

**Context:** Provider adapters (OpenAI, Anthropic, Gemini, DeepSeek, GitHub) need to be invoked by the Hermes Agent runtime. Two options: direct import or subprocess invocation.

**Decision:** Invoke adapters as **subprocesses** communicating via JSON on stdout. Never import them directly.

**Rationale:**
- **Process isolation:** adapter crashes don't bring down Hermes
- **Language independence:** adapters could be rewritten in other languages
- **Clean env:** each adapter gets a fresh `os.environ`
- **Timeout control:** subprocess timeout per adapter invocation

**Consequences:**
- Slightly higher latency (process spawn ~50ms)
- JSON serialization overhead on every call
- Easier to test (mock stdin/stdout)
- See: [[Architecture]] (Subprocess Adapter Pattern section)

---

## ADR-002: Bitwarden/Vaultwarden as Canonical Secret Store

**Status:** Accepted (2026-05)

**Context:** Provider API keys were stored in `~/.hermes/.env`. As the number of providers grew (5 LLM + 4 image), this became unwieldy and insecure.

**Decision:** **Secret zero architecture** — store all API keys in a self-hosted Bitwarden/Vaultwarden server. Only 4 bootstrap vars remain in `.env`.

**Rationale:**
- Encryption at rest (AES-256) on the Vaultwarden server
- `BW_SESSION` short-lived (daily expiry), revocable from server
- Provider keys can be rotated without touching `.env`
- Same pattern as HashiCorp Vault, AWS Secrets Manager

**Consequences:**
- Adds Vaultwarden as a runtime dependency
- Daily `bw unlock` required (acceptable operational cost)
- Admin keys classified separately, never reach `.env.generated`
- See: [[Hermes Compatibility#Security Model]]

---

## ADR-003: Three-Layer Admin Key Denylist

**Status:** Accepted (2026-05)

**Context:** Admin API keys (used for auto-rotation) must never leak into the runtime environment file used by Hermes Agent.

**Decision:** Three independent gates in `render_env_content()`:

1. **Hard `deny_render` list** in `config/env_projection.yaml` — named admin refs hard-blocked
2. **Path-segment classification** — any ref containing `admin_key`/`admin_secret`/`admin_token` auto-blocked
3. **Bitwarden/Vaultwarden metadata flag** — secrets with `secret_class=admin` have `renderable_to_env=False`

**Rationale:**
- Defense in depth — no single gate failure exposes admin keys
- Even if someone adds `hermes/openai/admin_key` to projection, it's blocked with `<DENIED>`
- Path-segment gate catches misclassified secrets

**Consequences:**
- Admin key rotation requires `--admin` flag (explicit intent)
- See: [[Architecture]] (Data Flow: Env Rendering Safety section)

---

## ADR-004: Two-Phase Key Rotation with Checkpointing

**Status:** Accepted (2026-06)

**Context:** Key rotation is a multi-step process prone to partial failure (network errors, API rate limits, power loss).

**Decision:** 14-phase state machine with:
- **Two-phase commit:** store candidate → smoke test → activate only if smoke passes → revoke old
- **Per-provider advisory locks:** `fcntl.flock` prevents concurrent rotations
- **Checkpointing:** each phase transition writes a fingerprint-only checkpoint to `~/.hermes/rotation_checkpoints/`

**Rationale:**
- Never revoke before smoke test passes
- Interrupted rotations resume from last checkpoint
- Locks prevent race conditions between two terminals

**Consequences:**
- Rotation takes ~5-15 seconds (acceptable for security operation)
- Checkpoint files must be cleaned up after successful rotation
- See: [[Architecture]] (Data Flow: Key Rotation section), [[Threat Model]] (Key Rotation Threat Analysis section)

---

## ADR-005: Image Routes as Separate Routing Layer

**Status:** Accepted (2026-06)

**Context:** Image generation (ComfyUI, Gemini, DALL-E, FAL) requires different routing logic than LLM text — local-first with cloud fallback, different health check semantics, different error handling.

**Decision:** **IMAGE ROUTES ≠ AUX ROUTES.** Two separate routing layers with different configuration files and adapters. AUX ROUTE `vision = gemini-2.5-flash` means image *analysis*, not generation.

**Rationale:**
- Different failure modes (GPU OOM vs API rate limit)
- Different cost models (per-image vs per-token)
- Different health checks (ComfyUI connectivity vs API key validation)
- Avoids polluting LLM routing with image concerns

**Consequences:**
- Two config files: `routes.yaml` (LLM) and `image_routes.yaml` (image)
- Separate CLI namespace: `hermes-ops-kit image ...`
- See: [[Route Profile Design#Two Separate Routing Layers]]

---

## Related

- [[Architecture]] — full module map and data flows
- [[Hermes Compatibility]] — integration and security model
- [[Route Profile Design]] — route architecture
- [[Threat Model]] — threat analysis
- [[Key Management Lifecycle]] — full secret lifecycle, rotation modes, revocation matrix
- [[Operations Runbook]] — incident response procedures
- [[Quickstart]] — getting started guide
