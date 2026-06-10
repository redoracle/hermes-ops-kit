---
title: Architecture
tags: [hermes, ops-kit, architecture]
created: 2026-06-04
modified: 2026-06-09
---

# Architecture

Hermes Ops Kit is a Python package organized around a plugin architecture
compatible with the Hermes Agent plugin system.

## Module Map

```text
bridge.py                  # Main CLI — subcommand dispatcher
commands.py                # Plugin CLI commands (doctor, mcp, audit, budget, image, …)
hermes_key_rotate.py       # Key rotation CLI (rotate, validate, seed, emergency, backup, restore, diff, migrate, render, status)
                        #   --render-env --merge: syncs .env.generated keys into .env
usage_metrics_v2.py        # Health / limits / usage / costs (concurrent provider probes)
hermes_skill_factory.py    # SKILL.md generator from commands + runbooks
hermes_assistant_manager.py# Assistant CRUD + ping + discover + validation
hermes_route_manager.py    # Route configuration CLI (LLM text + IMAGE ROUTES)
hermes_export.py           # Structured export: usage, security, audit, briefings

image_routes/              # Image generation routing (NOT LLM text — separate layer)
  manager.py               #   CLI: routes, doctor, test, set-default, set-route
  router.py                #   Dispatch: config→adapter, local-first, fallback on any generate() failure
  hermes_provider.py       #   OpsKitRouterProvider — self-registers via __init__.py (single plugin)
  background_edit.py       #   Subject-preserving background replacement
  adapters/
    base.py                #     Abstract BaseImageAdapter + load_dotenv() + envelope
    gemini_image.py        #     Gemini 2.5 Flash Image via google.genai
    openai_image.py        #     OpenAI gpt-image-2 / DALL-E 3 via openai SDK
    fal_image.py           #     FAL.ai Flux/Stable Diffusion via REST API
    local_comfyui.py       #     Local ComfyUI via REST API (private generation)

security/                  # Secret backend, redaction, fingerprints, locking, classification
  redaction.py             #   Single shared redact() — 16 patterns
  secret_backend.py        #   SecretBackend Protocol + SecretClass + ValidationResult + 12 error types
  vaultwarden_backend.py   #   Full Bitwarden/Vaultwarden implementation (3 auth modes, 26 refs, classification)
  bitwarden_cli_client.py  #   Safe subprocess wrapper (base64-encoded args for bw >= 2026.5.0)
  lockfile.py              #   Per-provider fcntl.flock advisory locks (prevents concurrent rotation)
  fingerprints.py          #   SHA-256 fingerprint + last4
  file_permissions.py      #   chmod 600/700 enforcement
  secret_scanner.py        #   Pre-write gate (blocks secrets in Obsidian/audit)
  plugin_scanner/          #   Plugin security scanner (defense-in-depth, v0.2.0)
    scanner.py             #     Orchestrator + objective scoring engine (score cap)
    enforce.py             #     Pre-boot policy enforcement + config synchronization
    bootstrap.py           #     First-install setup wizard
    cli.py                 #     CLI handler (scan, approve, revoke, override, policy, cache)
    findings.py            #     Finding, RiskLevel, ScanResult, ScanProfile data models
    cache.py               #     SQLite SHA-256 cache (keyed by git_commit + file_tree_sha)
    policy.py              #     Approval policy v2: plugin_policy.json + rule_overrides + JSONL audit
    plugin_scanner.yaml    #     Default configuration (profiles, timeouts, categories)
    categories/
      secrets.py           #     Secrets: regex + entropy + dummy detection + doc/test/skill downgrades
      policy.py            #     Policy: AST + regex + skill-mode + pip/curl .py guards
    rules/
      hermes-critical.yaml #     Custom Semgrep rules (ERROR severity)
      hermes-warning.yaml  #     Custom Semgrep rules (WARNING severity)

env/                       # Environment rendering pipeline (3-layer denylist gate)
  env_loader.py            #   Loads ~/.hermes/.env, validates bootstrap vars
  render_env.py            #   Renders .env.generated with deny_render + classification check
                        #   --merge flag: syncs new/updated keys from .generated into .env
  atomic_write.py          #   temp → chmod 600 → fsync → rename

providers/                 # Provider adapters + rotators + state machine (LLM text only)
  *_adapter.py (6)         #   API/CLI adapters (invoked by bridge.py as subprocesses)
  *_rotator.py  (6)        #   Key rotators with structured ValidationResult + retry + rollback
  nvidia_adapter.py        #     NVIDIA NIM adapter (OpenAI-compatible, reasoning_budget, enable_thinking)
  nvidia_rotator.py        #     NVIDIA NIM rotator (manual-new-key, PermissionDeniedError→QUOTA)
  base.py                  #   Abstract BaseRotator (validate_with_retry, revoke_key, cleanup_orphaned_key)
  rotation_state_machine.py#   14-phase RotationPhase + RotationState + RotationRunner

assistants/                # Remote agent delegation (NOT providers)
  base.py                  #   AssistantConfig + AssistantTask dataclasses
  client.py                #   Config-driven client for any assistant (OpenAI-compat)
  tool.py                  #   ai_assistant_delegate(assistant_id, ...)
  registry.py              #   Loads assistants.yaml — checks ~/.hermes/ops-kit/ first, then bundled fallback
  policy.py                #   Pre-flight policy checker
  result_sanitizer.py      #   Output redaction + validation
  task_store.py            #   SQLite task lifecycle tracking
  audit.py                 #   JSONL delegation audit

mcp/                       # MCP tool auditor + risk classifier; preflight gates servers
  auditor.py               #   Server discovery, tool audit, approve/revoke policy
  classifier.py            #   Capability detection + risk rules
  reporter.py              #   Formatted audit output

policy/                    # Centralized policy engine
  engine.py                #   PolicyDecision, scan_for_secrets, check_* functions
  decisions.py             #   Convenience wrappers with audit logging
  rules.yaml               #   Declarative rules for delegation, rotation, Obsidian, plugin_security

ui/                        # Shared terminal UI foundation
  console.py               #   Unified Console: NO_COLOR, TTY, mode dispatch
  json_output.py           #   Standard envelope: {ok, result, warnings, errors}
  output.py                #   print_result(), print_error(), print_table()

config/                    # Runtime configuration (YAML)
  assistants.yaml          #   Assistant registry
  env_projection.yaml      #   ENV_VAR → hermes/<provider>/<key> mapping (23 refs) + deny_render blocklist
  routes.yaml              #   LLM route profiles (cheap, balanced, max-quality)
  image_routes.yaml        #   Image generation route profiles
  budget.yaml              #   Budget/cost limits
  assistant_tasks.yaml     #   Scheduled task profiles

audit/                     # Sanitized audit logging
  audit_log.py             #   JSONL rotation audit
  post_rotation.py         #   Post-rotation usage_metrics_v2 integration
  ledger.py                #   Unified audit event log
  route_events.py          #   Route audit event emitters

cost_governor/             # Budget enforcement
  budget.py                #   Budget evaluation engine

docs/                      # Documentation
  architecture.md          #   This file
  hermes-compatibility.md  #   Hermes integration guide
  hermes-hook-integration.md#  Plugin scanner Hermes hook wiring
  plugin-security-scanner.md#  Plugin scanner user docs + CLI reference
  plugin-security-scanner-design.md  #  Plugin scanner design decisions + ADRs
  external-security-tools.md#  Platform-specific gitleaks/Semgrep/Bandit install guide
  threat-model.md          #   Security threat model
  route-profile-design.md  #   Route architecture

scripts/                   # Operational scripts
  test-scanner.sh          #   10-test scanner integration suite
  preflight-scan.sh        #   Pre-release secret audit

assistant_tasks/           # Scheduled assistant task profiles
  profiles.py              #   Profile definitions
```

## Data Flow: Key Rotation (14-phase state machine)

```text
hermes-key-rotate rotate --provider openai [--manual-new-key-stdin]
  │
  ├─ 1. Acquire per-provider lock (fcntl.flock, ~/.hermes/locks/openai.lock)
  │      └─ Blocks concurrent rotations of the same provider
  │
  ├─ 2. Load bootstrap config → SecretBackend (Bitwarden/Vaultwarden)
  │      └─ bw CLI → Bitwarden/Vaultwarden server (HTTPS, TLS)
  │
  ├─ 3. Fingerprint old key (sha256:xxx, last4:Ab7Q) — never read raw value
  │      └─ Save checkpoint to ~/.hermes/rotation_checkpoints/openai.json
  │
  ├─ 4. Acquire candidate key
  │      ├─ admin-hybrid: fetch admin key from Bitwarden/Vaultwarden → call provider Admin API
  │      │   └─ Auto-create key named "hermes-<env>-openai-<timestamp>"
  │      └─ manual: stdin pipe or TTY getpass prompt (echo disabled)
  │
  ├─ 5. validate_with_retry(candidate_key)
  │      ├─ Returns ValidationResult{valid, reason_class, http_status, retry_recommended}
  │      ├─ Transient failures (network, timeout, rate-limit, 5xx): retry 3x with backoff
  │      ├─ Permanent failures (auth_denied, forbidden, invalid_format): return immediately
  │      └─ If auto-created key fails: cleanup_orphaned_key() → delete from provider
  │
  ├─ 6. backup_secret() — snapshot old key for rollback
  │
  ├─ 7. set_secret(hermes/<p>/api_key, candidate, {secret_class: "runtime"})
  │      └─ Stores in Bitwarden/Vaultwarden with classification metadata in custom fields
  │      └─ Verify read-after-write (compare fingerprints)
  │
  ├─ 8. render_env() — generate .env.generated
      │      ├─ Runs bw sync to catch manual Vaultwarden web UI edits
      │      └─ If --merge: syncs new/updated keys from .generated into .env

  │      ├─ Gate 1: deny_render list from env_projection.yaml
  │      ├─ Gate 2: path-segment admin classification
  │      ├─ Gate 3: Bitwarden/Vaultwarden metadata renderable_to_env flag
  │      └─ Atomic write: temp → chmod 600 → fsync → rename
  │
  ├─ 9. smoke_test() — call provider API with stored key
  │      └─ On failure: restore_secret() → restore old key → mark ROLLED_BACK
  │
  ├─ 10. Post-rotation: usage_metrics_v2.py --json
  │
  ├─ 11. revoke_key() — archive/delete old key via provider Admin API
  │       └─ OpenAI: DELETE /v1/api_keys/{id}
  │       └─ Anthropic: POST /v1/api_keys/{id}/archive
  │       └─ Google: DELETE (google-auth ADC)
  │       └─ DeepSeek/GitHub: manual action required (return False)
      │       └─ NVIDIA: manual action required (return False)

  │       └─ On failure: mark MANUAL_ACTION_REQUIRED, emit console instructions
  │
  ├─ 12. write_rotation_phase_event() — structured JSONL audit
  │       └─ operation="rotation.completed", fingerprints only
  │
  └─ 13. Delete checkpoint → COMPLETED

Failure handling:
  Any phase failure → save checkpoint → ROLLED_BACK or MANUAL_ACTION_REQUIRED
  Resume with: hermes-key-rotate resume --provider openai
```

## Data Flow: Env Rendering Safety (3-layer denylist)

```text
render_env_content(backend, projection)
  │
  ├─ For each (ENV_VAR, secret_ref) in projection:
  │
  ├─ Gate 1: Hard deny_render list
  │     └─ secret_ref in {hermes/openai/admin_key, hermes/anthropic/admin_key, ...}
  │        → "# ENV_VAR=<DENIED: in deny_render list — not renderable>"
  │
  ├─ Gate 2: Path-segment admin classification
  │     └─ any segment in {admin_key, admin_secret, admin_token, service_account_json}
  │        → "# ENV_VAR=<DENIED: classified as admin by path>"
  │
  └─ Gate 3: Bitwarden/Vaultwarden metadata flag
        └─ backend.get_metadata(ref).renderable_to_env == False
           → "# ENV_VAR=<DENIED: classified admin — not renderable>"

Only secrets that pass all 3 gates are rendered into .env.generated.
```

## Data Flow: Provider Invocation (Bridge)

```text
Hermes tool call: ai_provider_invoke
  │
  ├─ tools.py handler
  │     └─ subprocess: bridge.py <provider> <operation> <json_payload>
  │
  ├─ bridge.py
  │     └─ import provider_adapter (OpenAI/Anthropic/Gemini/DeepSeek/GitHub)
  │     └─ adapter.invoke(json_payload) → JSON stdout
  │
  └─ redact() output → return to Hermes
```

## Data Flow: Preflight Enforcement

```text
hermes-ops-kit preflight
  │
  ├─ 1. Plugin scan (startup profile, 12s timeout)
  │     └─ security/plugin_scanner/scanner.py:scan_all()
  │
  ├─ 2. Policy enforcement decisions
  │     ├─ Plugin decisions: enforce.py:get_enforcement_decisions()
  │     │     ├─ CRITICAL → blocked (excluded from config)
  │     │     ├─ HIGH/MEDIUM + approved → allowed (included)
  │     │     └─ HIGH/MEDIUM + unapproved → deferred (excluded)
  │     └─ MCP decisions: enforce.py:get_mcp_enforcement_decisions()
  │           ├─ Incomplete discovery → server disabled
  │           ├─ Unapproved HIGH tool → server disabled
  │           └─ CRITICAL tool → server blocked (blocks boot)
  │
  ├─ 3. Apply enforcement (dry_run=False → writes)
  │     └─ enforce.py:apply_enforcement()
  │           ├─ Sync plugin config to ~/.hermes/config.yaml
  │           └─ Sync MCP server enable/disable flags
  │
  └─ 4. Return structured decision (ok, decisions, enforcement, mcp_decisions)
        └─ policy/decisions.py:preflight_decision() wraps the full pipeline
```

## Plugin Lifecycle

1. Hermes starts → loads plugin manifest (`plugin.yaml`)
2. Calls `register(ctx)` in `__init__.py`
3. Ops Kit registers 7 tools, 2 hooks, 1 CLI command, 1 skill
4. Session-start hook: checks permissions and reports cached plugin scan decisions
5. Post-tool-call hook: redacts output for every tool call
6. Preflight command (optional): scans all plugins → blocks unsafe ones from loading

## Subprocess Adapter Pattern

Provider adapters are invoked as **subprocesses** (not imported directly).
This provides:

- **Process isolation:** adapter crashes don't bring down Hermes
- **Language independence:** adapters could be rewritten in other languages
- **Clean env:** each adapter gets a fresh `os.environ`
- **Timeout control:** subprocess timeout per adapter invocation

Communication is via JSON on stdout. All adapters return a consistent
envelope: `{ok: bool, provider: str, model: str, result: ...}`

## Related

- [[Hermes Compatibility]] — Hermes integration guide
- [[Route Profile Design]] — route profile architecture
- [[Threat Model]] — security threat model
- [[Architecture Decisions]] — key architectural decisions (ADRs)
- [[Key Management Lifecycle]] — full secret lifecycle, rotation modes, revocation matrix
- [[Operations Runbook]] — incident response procedures
- [[Quickstart]] — getting started guide
- `tests/test_security.py` — 33 security tests
- `tests/test_snapshots.py` — 14 snapshot tests
- `tests/test_plugin_scanner.py` — 109 plugin scanner tests
- `scripts/test-scanner.sh` — 10-test scanner integration suite
