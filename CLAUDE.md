# Hermes Ops Kit — Claude Code Guide

Multi-provider AI orchestration with key rotation, usage metrics, and a Vaultwarden-backed secret store.

## Scope & boundaries

Changes and improvements to ops-kit must stay within its **operational lane** —
multi-provider orchestration, key rotation, usage metrics, the Vaultwarden secret store,
and image/LLM routing. They **must not overlap, duplicate, or over-engineer the Hermes
agents' core functions** (the agent runtime, messaging, model dispatch, and conversation
handling owned by the Hermes service). When a capability already exists in Hermes core,
**integrate with it — don't reimplement or wrap it here.**

## Architecture

```text
bridge.py                  # Main CLI entry point — subcommand dispatcher
commands.py                # Plugin CLI commands (doctor, mcp, audit, budget, image, …)
hermes_key_rotate.py       # Key rotation orchestration
usage_metrics_v2.py        # Health, limits, usage, cost telemetry across all providers
hermes_skill_factory.py    # SKILL.md generator from commands + runbooks
hermes_assistant_manager.py# Assistant registry CRUD + validation
hermes_route_manager.py    # Route configuration CLI (LLM text routes + IMAGE ROUTES)
hermes_export.py           # Structured export: usage, security, audit, briefings

image_routes/              # Image generation routing (NOT LLM text — separate layer)
  manager.py               #   CLI: routes, doctor, test, set-default, set-route
  router.py                #   Dispatch: config→adapter, local-first, cloud fallback
  adapters/
    base.py                #     Abstract BaseImageAdapter + load_dotenv() + envelope
    gemini_image.py        #     Gemini 2.5 Flash Image (Nano Banana) via google.genai
    openai_image.py        #     OpenAI gpt-image-2 / DALL-E 3 via openai SDK
    fal_image.py           #     FAL.ai Flux/Stable Diffusion via REST API
    local_comfyui.py       #     Local ComfyUI via REST API (private generation)

security/                  # Secret backend, redaction, fingerprints, permissions
  redaction.py             #   Single shared redact() for all adapters
  secret_backend.py        #   SecretBackend Protocol + error hierarchy
  vaultwarden_backend.py   #   Full Vaultwarden/Bitwarden implementation
  bitwarden_cli_client.py  #   Safe subprocess wrapper: expandvars+expanduser+abspath

env/                       # Environment rendering pipeline
  env_loader.py            #   Loads ~/.hermes/.env
  render_env.py            #   Renders ~/.hermes/.env.generated from Vaultwarden
  atomic_write.py          #   temp→chmod 600→fsync→rename

providers/                 # Provider adapters + rotators (LLM text only)
  *_adapter.py (5)         #   API/CLI adapters (invoked by bridge.py as subprocesses)
  *_rotator.py  (5)        #   Key rotators (invoked by hermes_key_rotate.py)
  base.py                  #   Abstract BaseRotator

assistants/                # Remote agent delegation (NOT providers)
  base.py                  #   AssistantConfig + AssistantClient Protocol
  client.py                #   Config-driven client for any assistant (OpenAI-compat)
  tool.py                  #   ai_assistant_delegate(assistant_id, ...) — generic
  registry.py              #   Loads config/assistants.yaml (no PyYAML dependency)
  policy.py                #   Pre-flight policy checker (secrets, shell, mutation)
  result_sanitizer.py      #   Output redaction + validation
  task_store.py            #   SQLite task lifecycle tracking
  audit.py                 #   JSONL delegation audit

mcp/                       # MCP tool auditor + risk classifier
  auditor.py               #   Server discovery, tool audit, approve/revoke policy
  classifier.py            #   Capability detection + risk rules
  reporter.py              #   Formatted audit output

assistant_tasks/           # Scheduled assistant task profiles
cost_governor/             # Budget enforcement + route restrictions

config/env_projection.yaml #   ENV_VAR → hermes/<provider>/<key> mapping
config/assistants.yaml     #   Assistant registry (Assistant capabilities + policy)
config/routes.yaml         #   LLM Route profiles (cheap, balanced, max-quality)
config/image_routes.yaml   #   Image generation route profiles (local, fast, quality, fallback)

policy/                     # Centralized policy engine
  engine.py                #   PolicyDecision, scan_for_secrets, check_* functions
  decisions.py             #   Convenience wrappers with audit logging
  rules.yaml               #   Declarative rules for delegation, rotation, Obsidian

ui/                         # Shared terminal UI foundation
  console.py               #   Unified Console: NO_COLOR, TTY, mode dispatch
  json_output.py           #   Standard envelope: {ok, result, warnings, errors}
  output.py                #   print_result(), print_error(), print_table()

audit/                     # Sanitized audit logging
  audit_log.py             #   JSONL audit events (no raw secrets)
  post_rotation.py         #   Post-rotation usage_metrics_v2 integration

docs/obsidian_sink.py      # Sanitized Obsidian writes (secret scanner gate)
skills/                    # Generated SKILL.md files for Hermes native skill loader

# Hermes image_gen provider plugin (deployed to ~/.hermes/plugins/image_gen/ops-kit-router/)
# Bridges the Hermes image_generate tool to ops-kit's image_routes.yaml routing.
# Configure in ~/.hermes/config.yaml:  image_gen: { provider: ops-kit-router, model: auto }

tests/test_security.py     # 33 security tests
tests/test_snapshots.py    # 14 snapshot tests
tests/test_simulator.py    # 8 failure scenarios
tests/cli/                 # 28 CLI integration tests (fixtures + false pos/neg + output modes)
```

## Image Routes (separate from LLM AUX ROUTES)

**IMAGE ROUTES ≠ AUX ROUTES.** Two separate routing layers with different semantics:

| Layer              | Purpose                                     | Example                                            |
| ------------------ | ------------------------------------------- | -------------------------------------------------- |
| ROUTE + AUX ROUTES | "Which LLM handles this text task?"         | chat, vision analysis, web extraction              |
| IMAGE ROUTES       | "Which image backend renders this request?" | ComfyUI local, Gemini Image, DALL-E/gpt-image, FAL |

AUX ROUTE `vision = gemini-2.5-flash` means **image analysis** (looking at screenshots),
NOT image generation. Image generation is a tool/media backend concern, not an LLM task.

### Routing pipeline

```
Hermes image_generate tool
  → ops-kit-router image provider (~/.hermes/plugins/image_gen/ops-kit-router/)
  → image_routes.yaml (local → fast → quality → fallback)
  → Adapter (local-comfyui | gemini-2.5-flash-image | gpt-image-2 | fal-ai/flux-2-pro)
  → PNG saved to ~/.hermes/cache/images/
```

### Configuration

**`~/.hermes/ops-kit/image_routes.yaml`:**

```yaml
default_route: local
routes:
  local: { provider: local-comfyui, model: flux-local } # private
  fast: { provider: gemini, model: gemini-2.5-flash-image } # ~$0.04/img
  quality: { provider: openai, model: gpt-image-2 } # paid
  fallback: { provider: fal, model: fal-ai/flux-2-pro } # paid
policies:
  prefer_local: true
  allow_cloud_fallback: true
  max_generation_seconds: 180
  output_dir: "~/.hermes/cache/images"
```

**`~/.hermes/config.yaml` (top-level):**

```yaml
image_gen:
  provider: ops-kit-router
  model: auto
```

### CLI

```bash
hermes-ops-kit image routes              # Show all image routes + status
hermes-ops-kit image doctor              # Validate config + backend health
hermes-ops-kit image test "prompt"       # Test generation with default route
hermes-ops-kit image test "..." --route quality --aspect-ratio square
hermes-ops-kit image set-default quality # Change default route
hermes-ops-kit image set-route fast gemini gemini-2.5-flash-image
hermes-route-manager show                # Includes IMAGE ROUTES section
hermes-usage                             # Includes IMAGE ROUTES section
```

### Adapter output envelope

All adapters return:

```json
{
  "ok": true,
  "type": "image",
  "provider": "openai",
  "model": "gpt-image-2",
  "image_path": "/Users/tesla/.hermes/cache/images/openai_20260603_212437_016db267.png",
  "image_paths": ["..."],
  "mime_type": "image/png",
  "caption": "Generated with gpt-image-2",
  "duration_ms": 25485
}
```

### API key loading

Adapters call `load_dotenv()` (in `image_routes/adapters/base.py`) before checking
API keys. This loads `~/.hermes/.env` automatically — no need to source it manually.
Safe to call repeatedly (loads once per process).

## Key conventions

- **Subprocess-based adapters.** `bridge.py` invokes provider adapters as standalone scripts communicating via JSON on stdout. Never imports them directly.
- **Shared redaction.** All five adapters import `redact()` from `security.redaction.py`. No more inline `SECRET_PATTERNS` duplication.
- **SecretBackend Protocol.** Provider rotators depend ONLY on `SecretBackend` — never on Vaultwarden internals.
- **Two-phase rotation.** Store candidate → smoke test → activate only if smoke passes → revoke old. Never revoke before smoke.
- **No raw secrets in logs, Obsidian, or audit.** Every write path is gated by `secret_scanner.assert_clean()`.
- **Atomic env writes.** `env/atomic_write.py` for `.env.generated` — never partial files.

## Secret backend

The canonical secret store is a self-hosted Vaultwarden/Bitwarden-compatible server.
Credentials live in `~/.hermes/.env` (chmod 600).

**Bootstrap env vars (`~/.hermes/.env`):**

```
HERMES_SECRET_BACKEND=vaultwarden
VAULTWARDEN_SERVER_URL=https://<host>
VAULTWARDEN_USER=<email>
VAULTWARDEN_PASSWORD=<master-password>
HERMES_AUTH_MODE=bitwarden_cli_session
BW_SESSION=<session-key>
```

**One-time setup:** `bw config server <url>` → `bw login <email>` → `bw unlock`
**Daily:** session expires → `bw unlock` → copy new key to `.env`
**Never set** `BITWARDENCLI_APPDATA_DIR` — let `bw` use its default.
If you must override it, use an absolute path. The `_env()` method in
`bitwarden_cli_client.py` normalizes via `expandvars`+`expanduser`+`abspath`
to prevent a relative `$HOME/...` from creating artifacts in the CWD.

mcp-vault/Obsidian is NOT a secret store.

## CLI reference

```bash
# Key rotation
hermes-key-rotate --doctor-secrets          # Full diagnostic
hermes-key-rotate --healthcheck             # Backend health
hermes-key-rotate --status                  # Fingerprints + age report
hermes-key-rotate --render-env              # Generate ~/.hermes/.env.generated
hermes-key-rotate --dry-run                 # Preview without mutating
hermes-key-rotate --provider deepseek --manual-new-key-stdin

# Vaultwarden operations
hermes-key-rotate --secret-backend vaultwarden --healthcheck
hermes-key-rotate --secret-backend vaultwarden --unlock
hermes-key-rotate --secret-backend vaultwarden --lock
hermes-key-rotate --secret-backend vaultwarden --sync
hermes-key-rotate --secret-backend vaultwarden --list-refs

# Usage metrics
hermes-usage                       # Rich boxed view (default)
hermes-usage --compact             # Minimal routing view
hermes-usage --json                # Machine-readable JSON
hermes-usage --models              # Model inventory
hermes-usage --limits              # Rate limits detail
hermes-usage --costs               # Usage/cost telemetry (needs admin keys)
hermes-usage --verbose             # All sections
hermes-usage --plain               # ASCII output (no Unicode, no color)
hermes-usage --no-color            # Strip ANSI escape sequences
hermes-usage -p github             # Single provider

# MCP auditor
hermes-ops-kit mcp audit                     # Full MCP tool security audit
hermes-ops-kit mcp list                      # Server + tool inventory
hermes-ops-kit mcp risks                     # Risk summary only
hermes-ops-kit mcp approve --server <id>     # Whitelist all tools from a server
hermes-ops-kit mcp approve --all             # Whitelist all configured servers
hermes-ops-kit mcp approve --tool <full_id>  # Whitelist a single tool
hermes-ops-kit mcp revoke                    # Remove all MCP approvals
hermes-ops-kit mcp policy                   # Show current approval policy

# Image generation routes
hermes-ops-kit image routes                  # Show all image routes + status
hermes-ops-kit image doctor                  # Validate config + backend health
hermes-ops-kit image test "prompt"           # Test generation with default route
hermes-ops-kit image test "..." --route fast --aspect-ratio square
hermes-ops-kit image set-default quality     # Change default route
hermes-ops-kit image set-route fast gemini gemini-2.5-flash-image

# Skill factory
hermes-skill-factory from-command <name>     # Generate SKILL.md from built-in command
hermes-skill-factory from-runbook <path>     # Generate SKILL.md from Obsidian runbook
hermes-skill-factory list                    # List generated skills
hermes-skill-factory validate <name>         # Validate a generated skill
```

## MCP Policy

MCP tool risks are classified by capability detection (critical → blocked, high → approval).
The `mcp approve` commands persist a whitelist to `~/.hermes/mcp_policy.json` (no PyYAML
dependency). Approved tools bypass blocking/approval and show `approved ✓` in audits.

**Atomic approval** — `--server <id>` approves all tools of that server at once.
**Global approval** — `--all` approves every configured MCP server.
**Revoke** — `mcp revoke` clears all approvals.

Policy is also synced to `~/.hermes/config.yaml` (best-effort, requires PyYAML).

## Deployment

To deploy ops-kit to a remote Hermes agent instance, sync the plugin directory
to `~/.hermes/plugins/hermes-ops-kit/` on the target host and enable it in
`~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-ops-kit
    - ops-kit-router
```

Copy relevant config files (`image_routes.yaml`, `routes.yaml`) to
`~/.hermes/ops-kit/` on the target host. See `docs/hermes-compatibility.md`
for detailed setup instructions.

## Assistants

Remote Hermes agent runtimes available for task delegation. See `config/assistants.yaml` for
configuration. Assistants are configured via the assistant registry with capabilities, policy,
and security constraints. The orchestrator delegates via `ai_assistant_delegate("<assistant-id>", ...)`.

```bash
# Check assistant health (integrated into usage_metrics_v2)
hermes-usage --json | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('_assistants',{}),indent=2))"

# Delegate from Python
python3 -c "from assistants.tool import ai_assistant_delegate; print(ai_assistant_delegate('assistant-id', task='Say hello', capability='review'))"
```

Default policy: read-only, no secrets, no shell, no file mutation. Credentials in Vaultwarden (`Hermes/Assistants/Assistant/API_KEY`).

## Tests

```bash
python3 -m pytest tests/ -v                    # 75 tests (33 security + 14 snapshots + 28 CLI)
python3 tests/test_simulator.py --all          # 8 simulator scenarios (no real API calls)
```

## Security

Store all API keys and secrets in Vaultwarden (not in docs, config files, or Obsidian).
The ops-kit secret backend reads from Vaultwarden at runtime — no raw keys should ever
be written to disk outside of `~/.hermes/.env` (chmod 600). See `docs/threat-model.md`
for the full security model.
