---
title: Quickstart
tags: [hermes, ops-kit, onboarding, quickstart]
created: 2026-06-04
modified: 2026-10-27
---

# Quickstart

Get Hermes Ops Kit running in under 10 minutes.

## Prerequisites

- Python 3.11+
- Linux, macOS, or Windows through WSL for `install.sh`
- pip, Git, and Bash
- A running Bitwarden/Vaultwarden server (self-hosted or cloud)
- `bw` CLI installed and in PATH: `npm install -g @bitwarden/cli` (or `brew install bitwarden-cli`)
- Hermes Agent 0.15.x installed and configured

## Install

```bash
# Clone into Hermes plugins
git clone https://github.com/redoracle/hermes-ops-kit.git ~/.hermes/plugins/hermes-ops-kit
cd ~/.hermes/plugins/hermes-ops-kit

# Create venv and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The automated `install.sh` path installs the project `dev` extra and verifies
Pillow and `ruff`. For this manual virtual-environment path, install the same
tooling with `pip install -e '.[dev]'`.

On Windows, run this repository from WSL:

```bash
bash install-wsl.sh
```

The WSL bootstrap installs the required Ubuntu/Debian packages, including Go
for the pinned Gitleaks installation, then delegates to `install.sh`.

## Bootstrap Secrets

### 1. Create ~/.hermes/.env

```bash
cat > ~/.hermes/.env << 'EOF'
HERMES_SECRET_BACKEND=vaultwarden
VAULTWARDEN_SERVER_URL=https://your-vaultwarden.example.com
VAULTWARDEN_USER=your-email@example.com
HERMES_AUTH_MODE=bitwarden_cli_session
EOF

chmod 600 ~/.hermes/.env
```

### 2. Configure Bitwarden/Vaultwarden CLI

```bash
bw config server 'https://your-vaultwarden.example.com'
BW_SESSION="$(bw login --raw)"
export BW_SESSION
bw status
```

Enter credentials at the CLI prompts; do not put passwords in command arguments.
For an already authenticated but locked account, use
`BW_SESSION="$(bw unlock --raw)"` instead. Verify `status: unlocked` and the expected
server before reading items. If Hermes configures `BITWARDENCLI_APPDATA_DIR`, use
that same expanded absolute directory for these commands.

Save the returned value locally as `BW_SESSION='…'` in `~/.hermes/.env`, preserving
other entries and mode `600`. Do not print it or paste it into chat. On macOS,
`printf '%s' "$BW_SESSION" | pbcopy` copies it to the local clipboard for your editor;
clear the clipboard afterward with `printf '' | pbcopy`. Exporting it in one shell
does not update Hermes' saved session or an already-running process. Session mode
does not require storing your master password in `.env`.

### 3. Seed Your First API Key

```bash
# From a provider key you already have
echo "sk-your-openai-key" | hermes-key-rotate rotate --provider openai --manual-new-key-stdin

# Verify
hermes-key-rotate --status
```

### 4. Render Runtime Env

```bash
hermes-key-rotate --render-env --merge
# → Syncs vault → .env.generated, then merges new keys into .env (no duplicates)
```

### 5. Seed Remaining Providers

Repeat step 3 for each provider: `anthropic`, `gemini`, `deepseek`, `github`.

Then remove raw keys from `~/.hermes/.env` — retain the bootstrap settings and `BW_SESSION`.

## Enable in Hermes

Enable through Hermes' native package manager so the plugin's SDK dependencies
and selection are admitted together:

```bash
hermes plugins enable hermes-ops-kit
```

The resulting plugin selection in `~/.hermes/config.yaml` includes:

```yaml
plugins:
  enabled:
    - hermes-ops-kit

image_gen:
  provider: ops-kit-router
  model: auto
```

Retain your existing image route unless you intend to select `ops-kit-router`.
Restart Hermes Agent to adopt the newly published dependency environment, with
preflight enforcement if required by your plugin policy:

```bash
hermes-ops-kit preflight && hermes gateway restart
```

The plugin automatically runs a cached, report-only plugin security scan when
each Hermes session starts. No `hooks:` entry is required in
`~/.hermes/config.yaml`. A normal `hermes gateway restart` does not run
preflight. To preview or apply security decisions before plugins load:

```bash
hermes-ops-kit preflight --dry-run
hermes-ops-kit preflight && hermes gateway restart
```

Hot reload applies commands and hooks, but does not switch Python environments.
Open a fresh session for the seven Python tools; they may be deferred behind
tool search. See [managed runtime activation](Hermes%20Compatibility.md#managed-runtime-activation)
for the verified deployment procedure and acceptance limits.

## Verify

```bash
# All providers healthy?
hermes-usage

# Keys all present?
hermes-key-rotate --status

# Image routes working?
hermes-ops-kit image doctor

# MCP tools audited?
hermes-ops-kit mcp audit
```

## First Operations

```bash
# Set a route profile
hermes-route-manager apply-profile balanced

# Check costs
hermes-usage --costs

# Test image generation
hermes-ops-kit image test "a sunset over mountains" --route fast

# Run full security audit
hermes-key-rotate --doctor-secrets
```

## Daily Routine

1. Check `bw status`; log in or unlock as needed, then update `BW_SESSION` in `.env`.
2. `hermes-usage` → check all providers green
3. `hermes-key-rotate --status` → verify key fingerprints
4. `hermes-ops-kit image routes` → verify image backends

## Troubleshooting

| Symptom                   | Fix                                                              |
| ------------------------- | ---------------------------------------------------------------- |
| `bw` command not found    | `npm install -g @bitwarden/cli`                                  |
| Vault locked or unauthenticated | Follow the login/unlock steps above and refresh saved `BW_SESSION` |
| Provider key missing      | `hermes-key-rotate rotate --provider <p> --manual-new-key-stdin` |
| `.env.generated` empty    | `hermes-key-rotate --render-env`                                 |
| Image routes all `NO KEY` | Check `GEMINI_API_KEY` in `.env.generated`                       |

## Next Steps

- Read [[Architecture]] for the full module map and data flows
- Read [[Hermes Compatibility]] for Hermes integration details
- Read [[Route Profile Design]] to design custom route profiles
- Read [[Threat Model]] to understand the security model
- Read [[Operations Runbook]] for incident response procedures

## Related

- [[Architecture]] — full module map and data flows
- [[Hermes Compatibility]] — Hermes integration and security model
- [[Route Profile Design]] — design custom route profiles
- [[Threat Model]] — security model
- [[Key Management Lifecycle]] — full secret lifecycle, rotation modes, revocation matrix
- [[Operations Runbook]] — incident response procedures
- [[index]] — map of all documentation
