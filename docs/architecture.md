# Architecture

Hermes Ops Kit is a Python package organized around a plugin architecture
compatible with the Hermes Agent plugin system.

## Module Map

```
bridge.py                  # Main CLI — subcommand dispatcher
commands.py                # Plugin CLI commands (doctor, mcp, audit, budget, image, …)
hermes_key_rotate.py       # Key rotation orchestration
usage_metrics_v2.py        # Health / limits / usage / costs (concurrent provider probes)
hermes_skill_factory.py    # SKILL.md generator from commands + runbooks
hermes_assistant_manager.py# Assistant CRUD + validation + discover
hermes_route_manager.py    # Route configuration CLI (LLM text + IMAGE ROUTES)
hermes_export.py           # Structured export: usage, security, audit, briefings

image_routes/              # Image generation routing (NOT LLM text — separate layer)
  manager.py               #   CLI: routes, doctor, test, set-default, set-route
  router.py                #   Dispatch: config→adapter, local-first, cloud fallback
  adapters/
    base.py                #     Abstract BaseImageAdapter + load_dotenv() + envelope
    gemini_image.py        #     Gemini 2.5 Flash Image via google.genai
    openai_image.py        #     OpenAI gpt-image-2 / DALL-E 3 via openai SDK
    fal_image.py           #     FAL.ai Flux/Stable Diffusion via REST API
    local_comfyui.py       #     Local ComfyUI via REST API (private generation)

security/                  # Secret backend, redaction, fingerprints, permissions
  redaction.py             #   Single shared redact() — 16 patterns
  secret_backend.py        #   SecretBackend Protocol + error hierarchy (12 error types)
  vaultwarden_backend.py   #   Full Vaultwarden implementation (3 auth modes, 26 refs)
  bitwarden_cli_client.py  #   Safe subprocess wrapper (list args, stdin auth, forbidden cmds)
  fingerprints.py          #   SHA-256 fingerprint + last4
  file_permissions.py      #   chmod 600/700 enforcement
  secret_scanner.py        #   Pre-write gate (blocks secrets in Obsidian/audit)

env/                       # Environment rendering pipeline
  env_loader.py            #   Loads ~/.hermes/.env, validates bootstrap vars
  render_env.py            #   Renders ~/.hermes/.env.generated from Vaultwarden
  atomic_write.py          #   temp → chmod 600 → fsync → rename

providers/                 # Provider adapters + rotators (LLM text only)
  *_adapter.py (5)         #   API/CLI adapters (invoked by bridge.py as subprocesses)
  *_rotator.py  (5)        #   Key rotators (invoked by hermes_key_rotate.py)
  base.py                  #   Abstract BaseRotator

assistants/                # Remote agent delegation (NOT providers)
  base.py                  #   AssistantConfig + AssistantTask dataclasses
  client.py                #   Config-driven client for any assistant (OpenAI-compat)
  tool.py                  #   ai_assistant_delegate(assistant_id, ...)
  registry.py              #   Loads assistants.yaml (no PyYAML dependency)
  policy.py                #   Pre-flight policy checker
  result_sanitizer.py      #   Output redaction + validation
  task_store.py            #   SQLite task lifecycle tracking
  audit.py                 #   JSONL delegation audit

mcp/                       # MCP tool auditor + risk classifier
  auditor.py               #   Server discovery, tool audit, approve/revoke policy
  classifier.py            #   Capability detection + risk rules
  reporter.py              #   Formatted audit output

policy/                    # Centralized policy engine
  engine.py                #   PolicyDecision, scan_for_secrets, check_* functions
  decisions.py             #   Convenience wrappers with audit logging
  rules.yaml               #   Declarative rules for delegation, rotation, Obsidian

ui/                        # Shared terminal UI foundation
  console.py               #   Unified Console: NO_COLOR, TTY, mode dispatch
  json_output.py           #   Standard envelope: {ok, result, warnings, errors}
  output.py                #   print_result(), print_error(), print_table()

config/                    # Runtime configuration (YAML)
  assistants.yaml          #   Assistant registry
  env_projection.yaml      #   ENV_VAR → hermes/<provider>/<key> mapping (23 refs)
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
  obsidian_sink.py         #   Sanitized Obsidian writes (secret scanner gate)
  hermes-compatibility.md  #   Hermes integration guide
  threat-model.md          #   Security threat model
  route-profile-design.md  #   Route architecture
  architecture.md          #   This file

assistant_tasks/           # Scheduled assistant task profiles
  profiles.py              #   Profile definitions
```

## Data Flow: Key Rotation

```
User: hermes-key-rotate --provider openai --apply
  │
  ├─ 1. Load SecretBackend (Vaultwarden)
  │      └─ bw CLI → Vaultwarden server (HTTPS, TLS)
  │
  ├─ 2. Read current key from Vaultwarden
  │      └─ fingerprint: sha256:xxx (last4: Ab7Q)
  │
  ├─ 3. Call provider API admin endpoint to create NEW key
  │      └─ OPENAI_ADMIN_KEY from Vaultwarden
  │
  ├─ 4. Store new key as CANDIDATE in Vaultwarden
  │
  ├─ 5. Smoke test: call provider API with new key
  │      └─ Simple models.list() or equivalent
  │
  ├─ 6. If smoke passes: ACTIVATE new key (update Vaultwarden item)
  │      └─ Write .env.generated (atomic: temp → chmod 600 → fsync → rename)
  │
  ├─ 7. Post-rotation: usage_metrics_v2.py --json
  │      └─ Verify provider health with new key
  │
  ├─ 8. REVOKE old key via provider admin API
  │
  └─ 9. Audit: write JSONL event (fingerprints only, no raw keys)
```

## Data Flow: Provider Invocation (Bridge)

```
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

## Plugin Lifecycle

1. Hermes starts → loads plugin manifest (`plugin.yaml`)
2. Calls `register(ctx)` in `__init__.py`
3. Ops Kit registers 7 tools, 2 hooks, 1 CLI command, 1 skill
4. Startup hook: checks `~/.hermes/.env` permissions (must be chmod 600)
5. Post-tool-call hook: redacts output for every tool call

## Subprocess Adapter Pattern

Provider adapters are invoked as **subprocesses** (not imported directly).
This provides:

- **Process isolation:** adapter crashes don't bring down Hermes
- **Language independence:** adapters could be rewritten in other languages
- **Clean env:** each adapter gets a fresh `os.environ`
- **Timeout control:** subprocess timeout per adapter invocation

Communication is via JSON on stdout. All adapters return a consistent
envelope: `{ok: bool, provider: str, model: str, result: ...}`
