# Hermes Ops Kit

Multi-provider AI orchestration with key rotation, usage metrics, and remote assistant delegation — backed by a self-hosted Vaultwarden secret store.

## Installation

> Replace `your-org` below with the GitHub org/user that hosts your fork, or set
> `HERMES_OPS_KIT_REPO=https://github.com/your-org/hermes-ops-kit.git` before running `install.sh`.

### Remote install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/your-org/hermes-ops-kit/main/install.sh | bash
```

Pinned version:

```bash
HERMES_OPS_KIT_VERSION=v0.1.0 bash -c "$(curl -fsSL https://raw.githubusercontent.com/your-org/hermes-ops-kit/main/install.sh)"
```

### Git clone + plugin

```bash
git clone https://github.com/your-org/hermes-ops-kit.git ~/.hermes/plugins/hermes-ops-kit
hermes plugins enable hermes-ops-kit
```

### Pip install

```bash
pip install git+https://github.com/your-org/hermes-ops-kit.git
```

### Uninstall

```bash
bash uninstall.sh              # Safe: keeps config + env
bash uninstall.sh --purge       # Also remove config
bash uninstall.sh --purge-env   # Also remove ~/.hermes/.env (DANGEROUS)
```

## Features

### Provider Adapters

5 standalone adapter scripts invoked as subprocesses by `bridge.py`. Each communicates via JSON on stdout with consistent response shapes.

| Provider      | Adapter                         | Surface         | Operations                                                                             |
| ------------- | ------------------------------- | --------------- | -------------------------------------------------------------------------------------- |
| OpenAI        | `providers/openai_adapter.py`   | API (SDK)       | `chat`, `extract`, `review`, `models`                                                  |
| Anthropic     | `providers/claude_adapter.py`   | API (SDK) + CLI | `api_chat`, `api_extract`, `review`, `analyze`, `readonly`                             |
| Google Gemini | `providers/gemini_adapter.py`   | API (SDK) + CLI | `generate`, `grounded`, `cli_plan`, `models`                                           |
| GitHub        | `providers/github_adapter.py`   | CLI             | `pr_list`, `pr_view`, `pr_diff`, `issue_list`, `ci_status`, `search_code`, `read_file` |
| DeepSeek      | `providers/deepseek_adapter.py` | API (SDK)       | `chat`, `extract`, `review`, `models`                                                  |

All adapters share a unified `security/redaction.py` module — 16 secret patterns covering API keys, tokens, sessions, private keys, and `.env` blocks.

### Key Rotation (`hermes_key_rotate.py`)

Two-phase rotation system backed by Vaultwarden/Bitwarden.

**Commands:**

- `--doctor-secrets` — comprehensive backend + permissions + env diagnostic
- `--secret-backend vaultwarden --healthcheck` — Vaultwarden connectivity
- `--secret-backend vaultwarden --unlock` / `--lock` / `--sync` / `--list-refs`
- `--render-env` — generate `~/.hermes/.env.generated` from Vaultwarden
- `--status` — fingerprint + age report for all provider secrets
- `--provider <name> --apply` — execute rotation (OpenAI/Anthropic/Google/DeepSeek/GitHub)
- `--dry-run` — preview without mutating

**Provider rotators:**

| Provider      | Mode                           | Automation                     |
| ------------- | ------------------------------ | ------------------------------ |
| DeepSeek      | `manual-new-key` via stdin     | Semi-manual                    |
| OpenAI        | Project service account        | Full-auto (needs admin key)    |
| Google Gemini | API Keys API                   | Full-auto                      |
| Anthropic     | Admin API hybrid               | Partial (falls back to manual) |
| GitHub        | App installation token minting | Token generation               |

**Rollback support:** `backup_secret()` before rotation → `restore_secret()` if smoke test fails.

**Post-rotation:** `audit/post_rotation.py` runs `usage_metrics_v2.py --json` against the new `.env.generated` to verify provider health.

### Environment Projection (`env/`)

- `env/env_loader.py` — loads `~/.hermes/.env`, validates bootstrap vars
- `env/render_env.py` — reads secrets from Vaultwarden via `SecretBackend`, renders `.env.generated`
- `env/atomic_write.py` — temp → chmod 600 → fsync → atomic rename
- `config/env_projection.yaml` — 23 env var → secret ref mappings across all providers + assistants

### Secret Backend Setup

Self-hosted Vaultwarden/Bitwarden-compatible server. Requires `bw` CLI (`brew install bitwarden-cli`).

**One-time setup:**

```bash
# Read bootstrap values with grep|cut (never `source`: a password may contain metacharacters).
bw config server "$(grep '^VAULTWARDEN_SERVER_URL=' ~/.hermes/.env | cut -d= -f2-)"
bw login "$(grep '^VAULTWARDEN_USER=' ~/.hermes/.env | cut -d= -f2-)"
bw unlock
```

**`~/.hermes/.env` bootstrap (chmod 600):**

```bash
HERMES_SECRET_BACKEND=vaultwarden
VAULTWARDEN_SERVER_URL=https://<your-vaultwarden-host>
VAULTWARDEN_USER=<email>
VAULTWARDEN_PASSWORD=<master-password>
HERMES_AUTH_MODE=bitwarden_cli_session
BW_SESSION=<session-key>
```

**Daily use:** `BW_SESSION` expires periodically. Refresh with `bw unlock`, copy the new session key to `.env`. The doctor auto-detects the session — no `export` needed.

**Important:** Do NOT set `BITWARDENCLI_APPDATA_DIR`. Let `bw` use its default data directory.

### Secret Backend (`security/`)

- **`secret_backend.py`** — `SecretBackend` Protocol + `SecretValue`/`SecretMetadata` dataclasses + 12 error types
- **`vaultwarden_backend.py`** — Full implementation with 3 auth modes (password, API key, session), 26 secret refs, backup/restore
- **`bitwarden_cli_client.py`** — Safe `bw` CLI wrapper (list args only, stdin auth, timeout, redaction, forbidden command blocking)
- **`redaction.py`** — 16 patterns: OpenAI, Anthropic, Gemini, GitHub, Vaultwarden, Assistant, Bearer, PEM, `.env`
- **`fingerprints.py`** — `sha256:xxx` + `last4` — no raw secrets in logs
- **`file_permissions.py`** — 600/700 enforcement + diagnostic checks
- **`secret_scanner.py`** — Pre-write gate blocking raw secrets in Obsidian/audit/logs

### Usage Metrics (`usage_metrics_v2.py`)

Concurrent provider health checks with rich terminal display.

**Views:** default (rich), `--compact`, `--models`, `--limits`, `--costs`, `--verbose`, `--json`
**Flags:** `--plain` (ASCII-only, no Unicode), `--no-color` (strip ANSI), `--provider` / `-p` (single provider)

**Sections:** ROUTE → AUX ROUTES → IMAGE ROUTES → ASSISTANTS → PROVIDERS → LIMITS → WARNINGS

**Checks:**

- Provider health + model inventories (live API probes)
- Rate limits (GitHub core, Gemini RPD, OpenAI headers, DeepSeek balance)
- Usage/cost telemetry (OpenAI + Anthropic admin keys)
- CLI versions: codex, gh, gh copilot, gemini, claude
- Bridge health + Hermes agent status + gateway

### Route Manager (`hermes-route-manager.py`)

CLI for managing Hermes routing configuration. Reads from `~/.hermes/config.yaml` (native Hermes model/auxiliary/fallback_providers) and `routes.yaml` (display labels, cost classes, profiles).

```bash
hermes-route-manager show                           # Current route config (+ IMAGE ROUTES)
hermes-route-manager doctor                         # Validate configuration
hermes-route-manager providers                      # List configured providers
hermes-route-manager set-primary copilot gpt-5.4-mini
hermes-route-manager set-utility gemini gemini-2.5-flash
hermes-route-manager set-aux vision gemini gemini-2.5-flash
hermes-route-manager fallback add openai gpt-5.4-mini
hermes-route-manager fallback list
hermes-route-manager apply-profile cheap|balanced|max-quality
hermes-route-manager export --json
```

### Image Route Manager (`hermes-ops-kit image`)

**IMAGE ROUTES ≠ AUX ROUTES.** Image generation is a tool/media backend concern, not an LLM text task. A dedicated routing layer dispatches image generation requests to the configured backend: local ComfyUI (private), Gemini 2.5 Flash Image "Nano Banana" (fast/cheap), OpenAI gpt-image-2 (high quality), or FAL.ai Flux (cloud fallback).

Routes are defined in `image_routes.yaml` with priority-based fallback:

```yaml
default_route: local
routes:
  local: { provider: local-comfyui, model: flux-local, priority: 10 } # private
  fast: { provider: gemini, model: gemini-2.5-flash-image, priority: 20 } # ~$0.04/img
  quality: { provider: openai, model: gpt-image-2, priority: 30 } # paid
  fallback: { provider: fal, model: fal-ai/flux-2-pro, priority: 40 } # paid
```

A **Hermes image_gen provider plugin** (`~/.hermes/plugins/image_gen/ops-kit-router/`) bridges the native `image_generate` tool to ops-kit routing. Configure in `~/.hermes/config.yaml`:

```yaml
image_gen:
  provider: ops-kit-router
  model: auto
```

**Commands:**

```bash
hermes-ops-kit image routes                        # Show all image routes + status
hermes-ops-kit image doctor                        # Validate config + backend health
hermes-ops-kit image test "a cat wearing a hat"    # Test generation (default route)
hermes-ops-kit image test "..." --route quality --aspect-ratio square
hermes-ops-kit image set-default quality           # Change default route
hermes-ops-kit image set-route fast gemini gemini-2.5-flash-image
```

**Image backends (adapters):**

| Adapter            | Provider      | Model                  | Cost             |
| ------------------ | ------------- | ---------------------- | ---------------- |
| `local_comfyui.py` | Local ComfyUI | flux-local             | free (local GPU) |
| `gemini_image.py`  | Google Gemini | gemini-2.5-flash-image | ~$0.04/image     |
| `openai_image.py`  | OpenAI        | gpt-image-2 / dall-e-3 | paid             |
| `fal_image.py`     | FAL.ai        | fal-ai/flux-2-pro      | paid             |

All adapters auto-load API keys from `~/.hermes/.env` via `load_dotenv()`.
Output saved to `~/.hermes/cache/images/` with standardized envelope.

### Assistant Manager (`hermes-assistant-manager.py`)

CLI for managing the assistant registry (`assistants.yaml`). Safe by default: atomic writes, secret scanning, backups, file locking.

```bash
hermes-assistant-manager.py list --json           # List all assistants
hermes-assistant-manager.py get assistant-id --json      # Show Assistant config
hermes-assistant-manager.py template assistant-id         # Print Assistant template
hermes-assistant-manager.py add <id> --display-name ... --type ... --role ... --transport ...
hermes-assistant-manager.py enable <id> | disable <id>
hermes-assistant-manager.py set <id> <dot.path> <value>
hermes-assistant-manager.py remove <id> --yes
hermes-assistant-manager.py validate               # Schema + secret scan
hermes-assistant-manager.py doctor                 # Operational checks
hermes-assistant-manager.py ping assistant-id --json      # Healthcheck
hermes-assistant-manager.py backup | restore <file>
```

### Remote Assistants (`assistants/`)

Config-driven delegation to remote Hermes agent runtimes. Zero per-assistant Python files needed.

- **`client.py`** — Config-driven OpenAI-compatible client for any assistant
- **`tool.py`** — `ai_assistant_delegate(assistant_id, task, capability, ...)` — generic delegation
- **`registry.py`** — YAML → `AssistantConfig` loader (no PyYAML dependency)
- **`policy.py`** — Pre-flight security checker (blocks secrets, shell, mutation, scanning)
- **`result_sanitizer.py`** — Output redaction + secret detection heuristics
- **`task_store.py`** — SQLite task lifecycle (`~/.hermes/assistants/tasks.sqlite`)
- **`audit.py`** — JSONL delegation audit (`~/.hermes/assistants/audit.jsonl`)
- **`config/assistants.yaml`** — Assistant registry: capabilities, policy, security, tool, system prompt

**Assistant Profiler ☁️ (example-assistant):** Remote Hermes agent runtime. Configured via assistant registry with capabilities, policy, and security constraints. See `config/assistants.yaml` for a full example. Uses the configured LLM backend. NOT a coding agent.

### Audit & Documentation

- `audit/audit_log.py` — JSONL rotation audit (`~/.hermes/key-rotation-audit.jsonl`)
- `audit/post_rotation.py` — Post-rotation `usage_metrics_v2` integration + doctor report
- `docs/obsidian_sink.py` — Sanitized Obsidian writes via `mcp-vault` with secret scanner gate
- 8 Obsidian notes: key rotation log, status, runbook, risks, spec, assistants, assistant runbook

### MCP Auditor (`hermes-ops-kit mcp`)

Security audit for configured MCP servers and tools. Classifies each tool by capability (critical → blocked, high → approval required) and supports atomic whitelisting.

```bash
hermes-ops-kit mcp audit                     # Full tool security audit
hermes-ops-kit mcp risks                     # Risk summary
hermes-ops-kit mcp approve --server obsidian-mcp-vault  # Whitelist all tools of a server
hermes-ops-kit mcp approve --all             # Whitelist all configured servers
hermes-ops-kit mcp revoke                    # Remove all approvals
hermes-ops-kit mcp policy                   # Show current approval policy
```

Policy persists to `~/.hermes/mcp_policy.json` (no PyYAML dependency).

### Skill Factory (`hermes-skill-factory`)

Generates SKILL.md files from built-in command metadata or Obsidian runbook notes.

```bash
hermes-skill-factory from-command hermes-key-rotate
hermes-skill-factory from-runbook <obsidian-vault>/HERMES_KEY_ROTATION.md
hermes-skill-factory list
hermes-skill-factory validate hermes-key-rotate
```

### Tests

75 tests across 5 files: 33 security, 14 snapshots, 28 CLI integration (fixtures, false positive/negative, output modes).

```bash
python3 -m pytest tests/ -v                  # 75 passed
python3 tests/test_simulator.py --all         # 8 failure scenarios
```

## Architecture

```text
bridge.py                  # Main CLI — subcommand dispatcher
commands.py                # Plugin CLI commands (doctor, mcp, audit, budget, image, …)
hermes_key_rotate.py       # Key rotation CLI
usage_metrics_v2.py        # Health / limits / usage / costs
hermes_skill_factory.py    # SKILL.md generator
hermes_assistant_manager.py# Assistant CRUD + validation
hermes_route_manager.py    # Route configuration (LLM text + IMAGE ROUTES)
hermes_export.py           # Structured export: usage, security, audit, briefings

image_routes/  (5 modules) # Image generation routing (separate from LLM AUX ROUTES)
security/     (8 modules)  # Secret backend, redaction, fingerprints, permissions
env/          (3 modules)  # Env loading, rendering, atomic writes
providers/    (11 modules) # 5 LLM text adapters + 5 rotators + base class
assistants/   (8 modules)  # Config-driven delegation (no per-assistant files)
mcp/          (3 modules)  # MCP tool auditor, risk classifier, reporter
policy/       (3 modules)  # Centralized policy engine, rules, decisions
ui/           (4 modules)  # Console, JSON output, tables, status rendering
config/       (4 YAML)     # Env projection, assistant registry, route profiles, image routes
audit/        (3 modules)  # Rotation audit, post-rotation, unified ledger
assistant_tasks/           # Scheduled assistant task profiles
cost_governor/             # Budget enforcement + route restrictions
docs/         (1 module)   # Obsidian sink
tests/        (5 files)    # 75 tests (33 security + 14 snapshots + 28 CLI) + 8 simulators
```

## Quick Start

```bash
# Health check
hermes-usage --compact

# Unified diagnostic (all subsystems)
hermes-ops-kit doctor

# Route management
hermes-route-manager show
hermes-route-manager apply-profile cheap

# Image generation
hermes-ops-kit image routes
hermes-ops-kit image test "a cat wearing a hat"

# Assistant management
hermes-assistant-manager list
hermes-assistant-manager ping assistant-id
hermes-assistant-manager discover http://<assistant-url>

# Export reports
hermes-export report usage --format md
hermes-export vault-briefing person "John Doe"

# Simulate failures (no real API calls)
python3 tests/test_simulator.py --all

# Generate skills from commands
hermes-skill-factory from-command hermes-key-rotate

# Run all tests (47 collected + 8 simulators)
python3 -m pytest tests/ -v
python3 tests/test_simulator.py --all
```

## Security

- **No raw secrets** in stdout, stderr, logs, audit JSONL, or Obsidian
- **Secret scanner gate** before every Obsidian/audit write
- **Two-phase rotation** — never revoke old key before smoke test passes
- **Atomic env writes** — temp → chmod 600 → fsync → rename
- **bw CLI safety** — subprocess list args, stdin auth, forbidden command blocking
- **HTTPS required** for secret backend
- **Read-only by default** for remote assistants
- **16 redaction patterns** covering all provider key types + sessions + private keys
