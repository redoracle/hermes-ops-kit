---
title: Hermes Compatibility
tags: [hermes, ops-kit, integration, security]
created: 2026-06-04
modified: 2026-10-27
---

# Hermes Compatibility

Hermes Ops Kit is an **optional operational/security plugin** for the
[Hermes Agent](https://github.com/NousResearch/hermes-agent). It extends
Hermes — it does NOT replace any core functionality.

## What Ops Kit Provides

| Capability           | How it works                                                                                        |
| -------------------- | --------------------------------------------------------------------------------------------------- |
| Secret projection    | Reads from Bitwarden/Vaultwarden, renders `~/.hermes/.env.generated` (3-layer denylist + `--merge`) |
| Key rotation         | Validation/retry, rollback, per-provider locks and shared credential commit locking                   |
| Key validation       | Structured `ValidationResult` with `reason_class`, `http_status`, `retry_recommended`               |
| Emergency rotation   | Immediate revoke + replace on key compromise via `emergency` subcommand                             |
| Bulk seed            | `seed-from-env` migrates all runtime keys from `.env` to Bitwarden/Vaultwarden                      |
| Admin key seeding    | `seed-admin` subcommand with live API validation before storage                                     |
| Provider health      | Live API probes for all 5 LLM providers                                                             |
| Route diagnostics    | Reads `~/.hermes/config.yaml` model/aux/fallback, displays in UI                                    |
| MCP auditing         | Scans configured MCP servers, classifies tools by risk                                              |
| Usage/cost telemetry | OpenAI + Anthropic admin API cost data (needs admin keys)                                           |
| Config patching      | Writes `~/.hermes/config.yaml` for route profiles, image_gen                                        |

## What Ops Kit Does NOT Replace

Ops Kit never touches:

- **Hermes runtime routing** — model invocation decisions remain in Hermes core
- **Model invocation** — the actual LLM calls are Hermes core responsibility
- **Memory** — Hermes native memory system
- **Skills** — Hermes skill loader and execution
- **Scheduler** — Hermes cron/task scheduler
- **Gateway** — Hermes API gateway (health, routing, auth)
- **Auxiliary route resolution** — Hermes determines which model handles vision, web_extract, etc.

Ops Kit provides **metadata and tooling** around these systems — not replacements for them.

## Required Hermes Configuration

In `~/.hermes/config.yaml`:

Use `hermes plugins enable hermes-ops-kit` to apply plugin selection through
native dependency admission. The following shows the resulting settings; editing
the enabled list alone does not reconcile the managed Python environment.

```yaml
plugins:
  enabled:
    - hermes-ops-kit            # Ops kit + image_gen provider (self-registers)

image_gen:
  provider: ops-kit-router
  model: auto
```

## Files Written by Ops Kit

| Path                                      | Purpose                                      | Safe to delete?                                     |
| ----------------------------------------- | -------------------------------------------- | --------------------------------------------------- |
| `~/.hermes/.env.generated`                | Rendered env vars from Bitwarden/Vaultwarden | Yes (re-render with `hermes-key-rotate render-env`) |
| `~/.hermes/.env.generated.bak`            | Previous .env.generated before rotation      | Yes (backup is rotated on each render)              |
| `~/.hermes/locks/<provider>.lock`         | Per-provider rotation advisory lock          | Yes (auto-cleaned on process exit)                  |
| `~/.hermes/rotation_checkpoints/<p>.json` | Legacy checkpoint (recovery unsupported)            | Yes (only relevant during an active rotation)       |
| `~/.hermes/ops-kit/routes.yaml`           | Route display labels + profiles              | Yes (will be regenerated from defaults)             |
| `~/.hermes/ops-kit/image_routes.yaml`     | Image generation routes                      | Yes (will be regenerated)                           |
| `~/.hermes/ops-kit/assistants.yaml`       | Assistant registry                           | **No** (your assistant configuration)               |
| `~/.hermes/ops-kit/budget.yaml`           | Budget/cost limits                           | Yes (will be regenerated)                           |
| `~/.hermes/ops-kit/audit/events.jsonl`    | Audit trail                                  | Yes (but you lose audit history)                    |
| `~/.hermes/key-rotation-audit.jsonl`      | Key rotation log                             | Yes (but you lose rotation history)                 |
| `~/.hermes/assistants/audit.jsonl`        | Assistant delegation log                     | Yes (but you lose delegation history)               |
| `~/.hermes/assistants/tasks.sqlite`       | Task lifecycle DB                            | Yes (active tasks lost)                             |
| `~/.hermes/mcp_policy.json`               | MCP approval whitelist + preflight input     | Yes (will be regenerated)                           |

## Security Model

Ops Kit follows a **"secret zero" architecture** — the same pattern used by
HashiCorp Vault and AWS Secrets Manager:

```text
~/.hermes/.env          (4 bootstrap vars, chmod 600)
        │
        │ bw unlock (TLS 1.3 → self-hosted Bitwarden/Vaultwarden)
        ▼
Bitwarden/Vaultwarden    (API keys encrypted at rest, AES-256)
        │
        │ hermes-key-rotate render-env [--merge] (sync + 3-layer denylist)
        ▼
~/.hermes/.env.generated (runtime keys only, chmod 600, atomic write)
        │
        │ Hermes Agent reads at startup
        ▼
     Hermes
```

**Why this is safer:** instead of 10+ long-lived API keys sitting in a
single `.env` file forever, you have 4 bootstrap vars that unlock an
encrypted store. `BW_SESSION` is a sensitive vault-unlock credential. Refresh it
when the configured CLI reports a locked or unauthenticated session; do not assume
a fixed daily expiry. Provider keys can be rotated without changing bootstrap settings.

**Admin keys are stored in Bitwarden/Vaultwarden too** (`seed-admin`), classified
as `secret_class=admin` with `renderable_to_env=false`. The 3-layer
denylist ensures they can never reach `.env.generated`. This means the
auto-creation and auto-revocation features (OpenAI Admin API, Anthropic
Admin API, Google API Keys API) work without any admin credentials in
the runtime environment.

## Gemini API Key

Ops Kit standardises on `GEMINI_API_KEY` for all Google Gemini operations
(text generation, image generation, key rotation). `GOOGLE_API_KEY` is
no longer rendered by `hermes-key-rotate render-env` (removed from the
default projection mapping in `env_projection.yaml`). The Gemini image
adapter temporarily suppresses `GOOGLE_API_KEY` during client creation
to prevent SDK auto-detection warnings when both vars coexist.

If you need `GOOGLE_API_KEY` for other Google tools, add it back to your
`env_projection.yaml` — both vars map to the same `hermes/google/gemini_api_key`
Vaultwarden secret.

## Environment Variables

Ops Kit requires **only these bootstrap vars** in `~/.hermes/.env` (chmod 600).
Provider API keys should be stored in Bitwarden/Vaultwarden, not in `.env`:

```bash
HERMES_SECRET_BACKEND=vaultwarden          # Currently only Bitwarden/Vaultwarden is supported
VAULTWARDEN_SERVER_URL=https://<host>       # Your Vaultwarden/Bitwarden server
VAULTWARDEN_USER=<email>                    # Bootstrap user
HERMES_AUTH_MODE=bitwarden_cli_session      # or bitwarden_cli_password, bitwarden_cli_api_key
BW_SESSION=<session-key>                   # Sensitive local unlock credential
```

These are the session-mode settings. Password mode may require
`VAULTWARDEN_PASSWORD`; do not store a master password solely for session mode.
See [local session refresh](quickstart.md#2-configure-bitwardenvaultwarden-cli).

After seeding keys into Bitwarden/Vaultwarden (`hermes-key-rotate rotate --provider <p>`),
remove provider API keys from `.env`. The rendered `.env.generated` file is
the runtime source for Hermes Agent.

### Migration path

```bash
# 1. Seed keys
echo "sk-..." | hermes-key-rotate rotate --provider openai --manual-new-key-stdin
# ... repeat for all providers

# 2. Verify
hermes-key-rotate --status          # all refs present
hermes-key-rotate render-env        # generates .env.generated

# 3. Remove provider keys from ~/.hermes/.env
# Keep only: HERMES_SECRET_BACKEND, VAULTWARDEN_*, HERMES_AUTH_MODE, BW_SESSION
```

## Rollback

Every mutating operation preserves the previous state:

- **Key rotation:** `backup_secret()` before rotation → `restore_secret()` on failure
- **Config patches:** `~/.hermes/config.yaml.bak` created before mutations
- **Env rendering:** atomic write (temp → fsync → rename) — never partial files

## Version Compatibility

| Ops Kit | Hermes Agent |
| ------- | ------------ |
| 0.2.x   | 0.15.x       |
| 0.11.0 hardening candidate | 0.22.2 canary, commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e` |

Ops Kit is developed against the Hermes plugin API. Breaking changes in the
Hermes plugin system may require ops-kit updates.

## Related

- [[Architecture]] — module architecture
- [[Threat Model]] — security threat model
- [[Key Management Lifecycle]] — full secret lifecycle, rotation modes, revocation matrix
- [[Operations Runbook]] — incident response procedures
- [[Quickstart]] — getting started guide

## Current-core acceptance and installation boundaries

The hardening changes are checked against local Hermes commit
`bf0203a794a07aac3ff764972ab9087baa4e4f4e` (v0.22.2 canary), using its real directory
loader in a temporary Hermes home. Directory loading must work without an installed
top-level ops-kit distribution. Wheel entry-point loading is tested separately.
A standalone Conda CLI and Hermes' managed interpreter are separate installations;
a successful standalone health command does not prove plugin activation or API health.
Ops-kit repair and update modify only the standalone one: a target that Hermes owns (or
whose ownership cannot be established) is refused with the native maintenance commands
([Ownership guard](install-reconciler.md#ownership-guard)).

Result replacement uses `transform_tool_result`; `post_tool_call` is an observer.
Hermes consumes the first string transformer result, so ops-kit-owned tool outputs
are redacted at their own boundary as well. Other plugins' transformation ordering
limits any claim of universal result sanitization.

Native source-image arguments are adapted from `image_url` / `reference_image_urls`.
Only a single local image is supported; the selected route advertises image capability
only when its adapter consumes the image (currently Gemini). Other edits fail explicitly.
No URL downloader or additional multi-reference backend is introduced. A caller-supplied
`model` applies only to the route the caller selected: a fallback route, or the local
route substituted by `prefer_local`, uses its own configured model even when it shares
the provider.

Budget status distinguishes unavailable spend from explicit numeric observations.
These CLI checks do not intercept Hermes core model dispatch. Remote delegation
checks the complete serialized task envelope, required HTTPS/token settings, and
response byte limits; endpoint redirects are refused. Reserved task constraints must be
booleans and can only tighten the configured restrictions. The `/ops-kit` slash command
returns bounded, redacted text for a read-only subset of commands
([Chat commands](Operations%20Runbook.md#chat-commands-ops-kit)).

### Managed runtime activation

For a directory installation at `~/.hermes/plugins/hermes-ops-kit`, preserve local
changes and back up the plugin and `config.yaml` before updating its files. Then:

```bash
hermes plugins enable hermes-ops-kit
hermes gateway restart
hermes pm doctor
```

Native enablement resolves the plugin's `pyproject.toml` and publishes its selection
and managed environment together. A refusal leaves both unchanged. `hermes pm repair`
rebuilds the recorded graph; it does not add a disabled plugin's requirements.
Restarting adopts a newly published environment; plugin hot reload alone does not
switch the running interpreter. Commands and transformation hooks can activate
immediately, while Python tools apply to fresh sessions. Inspect the session's full
`tools.show` catalog when tool search defers their individual schemas.

Headroom remains an external CLI installed in a separate environment. Its former
optional package extra pulled MCP 1.x into a workspace where Hermes pins MCP 2.x,
so it is no longer part of ops-kit's package metadata. See [Headroom](Headroom.md).

### Local acceptance evidence — 2026-10-27

The candidate was deployed and enabled through native admission, followed by an idle
gateway restart. The replacement gateway was ready and used the new managed
environment (`google-genai` 2.12.1, `fal-client` 0.13.1). Hot reload succeeded and a
fresh gateway session's full catalog contained all seven ops-kit tools. All 134
packaged Python/YAML files matched both the directory installation and managed
package snapshot. Existing model and image routes were preserved.

Verification passed: 457 tests with the actual Hermes core/runtime contract, wheel
and source builds, lint, 11 focused Headroom tests, a bounded Gemini text canary
using the installed managed-runtime adapter, and read-only Bitwarden sync/reads
with admin-key exclusion from in-memory rendering.

Limits: the fresh-session check inspected the owner-computed tool catalog without
submitting an LLM turn. No paid FAL image generation or live key rotation/revocation
was performed. OpenAI's earlier canary was blocked by account credit exhaustion.
Inference reservations totalled US$0.30 against a US$3 cap; actual billing was not
retrieved. These are dated local acceptance results, not a guarantee for every
provider, future release or account.
