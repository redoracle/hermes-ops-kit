---
title: Operations Runbook
tags: [hermes, ops-kit, operations, runbook, incident-response]
created: 2026-06-04
modified: 2026-10-28
---

# Operations Runbook

Day-to-day operational procedures and incident response for Hermes Ops Kit.

## Plugin deployment and dependency reconciliation

Preserve local plugin changes and verified private copies of the plugin and
`config.yaml` before deploying an update. Enable with
`hermes plugins enable hermes-ops-kit` so native PM reconciles the dependency graph;
`hermes pm repair` alone cannot add a disabled plugin's requirements. Restart an
idle gateway with `hermes gateway restart` after a new environment is published.
Do not install dependencies into Hermes' managed environment with raw pip.
`hermes-ops-kit install doctor --repair` and `install update` enforce this: they
refuse a target that Hermes owns (or whose ownership cannot be established) and name
the native commands to use instead — `hermes plugins update hermes-ops-kit`,
`hermes pm install` or `hermes update`. The refusal is recorded in
`~/.hermes/ops-kit/update_log.jsonl` as `stopped_at: ownership`; see
[Ownership guard](install-reconciler.md#ownership-guard).

Check the replacement gateway's readiness and selected interpreter, then verify
all seven ops-kit tools in a fresh session's full catalog (`tools.show`). Tool-search
deferral can omit individual tool schemas from the initial banner. Hot-reloaded
commands/hooks alone do not prove SDK availability or provider inference.

For vault session recovery, follow [Quickstart](quickstart.md#2-configure-bitwardenvaultwarden-cli)
using the configured server and CLI data directory. Preserve `.env` entries and
mode `600`; never copy a password or session token into an issue or chat.
An unset `BWS_ACCESS_TOKEN` warning refers to Hermes' separate Bitwarden Secrets
Manager integration, not proof that ops-kit's Bitwarden CLI session is invalid.

See [compatibility acceptance evidence](Hermes%20Compatibility.md#local-acceptance-evidence--2026-10-27)
for the tested core revision, deployment checks and explicit provider limits.

## Dependency updates (Dependabot)

Dependabot opens PRs; nothing applies them automatically (see
[[Architecture Decisions#ADR-007: Maintainer-Controlled Dependency Integration|ADR-007]]).
Integrate each one yourself so the commit carries your authorship and the checks have run:

```bash
gh pr list --author "app/dependabot"
gh pr view <N> --json files,statusCheckRollup      # what changes, and did its checks pass?
git switch -c deps/<topic> main
gh pr diff <N> | git apply                         # apply the candidate yourself
uv lock --check && uv run --locked python -m pytest tests/ -q   # when dependencies changed
git commit -am "chore(deps): <summary>"            # no bot trailer
git push origin deps/<topic>                       # then merge per the usual flow
gh pr close <N> --comment "Applied manually in <sha>"
```

Changes under `.github/workflows/` can only be pushed with the `workflows` permission, so
apply those from your own credentials, never from a workflow token.

Every `uses:` line in `.github/workflows/` is pinned to a full commit SHA with a version comment
(`# v7.0.1`), which Dependabot keeps current. Dependabot waits 7 days (`cooldown`) before proposing a
new release; security updates are not delayed.

## Release approval

`.github/workflows/release.yml` runs on every push to `main` with a write token and the PyPI
token. Its job declares `environment: release`, so publishing can wait for a human, but only
once the environment has reviewers. This is a one-time repository setting; the workflow cannot
set it:

1. GitHub repository **Settings > Environments > New environment**, named `release`.
2. Enable **Required reviewers** and add the maintainer(s). Optionally restrict deployment
   branches to `main`.
3. Move `PYPI_API_TOKEN` from repository secrets into this environment's secrets, so only an
   approved run can read it.

Until then the job runs unattended, as before. The Node dependencies of the release job are
pinned by `package-lock.json` and installed with `npm ci --ignore-scripts`; Dependabot proposes
updates to the lock like any other dependency (see *Dependency updates*).

## Chat commands (`/ops-kit`)

`/ops-kit` is read-only from a chat surface, because anyone who can message the
agent can send it. Reachable: `doctor`, `audit tail|search`, `mcp list|risks|tools|policy`,
`budget status`, `maintenance profiles`, `image routes`, `routes show`,
`headroom status`, `install doctor`, `plugin policy`, `assistant list`. Each runs in its
own process with no stdin, a 20 s timeout and a 64 KiB capture limit, in read-only mode
(`HERMES_OPS_KIT_READ_ONLY=1`: nothing is seeded into the profile, no MCP server is
started); the reply is redacted and capped at about 3500 characters. Everything else — rotation, approvals,
scans, repairs, exports, usage probes, image generation — answers "run it in a
terminal: `hermes-ops-kit …`". Flags are limited to presentation (`--json`, `--compact`,
`--verbose`, `--plain`, `--no-color`) plus the value flags a command really has; in
particular `--repair` and `--python` are refused. Core runs a synchronous handler inline,
so in the terminal UI a command blocks the prompt for up to the timeout.

## Health Checks

```bash
# Full diagnostic
hermes-key-rotate --doctor-secrets

# Secret backend health
hermes-key-rotate --secret-backend vaultwarden --healthcheck

# Key rotation status (fingerprints + age)
hermes-key-rotate --status

# Usage overview
hermes-usage --verbose

# Image routes health
hermes-ops-kit image doctor
```

### What to Check Daily

| Check                           | Command                           | Healthy Signal              |
| ------------------------------- | --------------------------------- | --------------------------- |
| Bitwarden/Vaultwarden reachable | `hermes-key-rotate --healthcheck` | `backend: ok`               |
| All provider keys present       | `hermes-key-rotate --status`      | All refs show fingerprints  |
| No expired sessions             | `hermes-key-rotate --status`      | `BW_SESSION` valid          |
| Image routes ready              | `hermes-ops-kit image routes`     | All routes `READY`          |
| No quota exhaustion             | `hermes-usage --limits`           | All providers within limits |

## Incident Response

### Incident: Key Compromised

```bash
# 1. Immediate rotation (emergency — separate subcommand)
echo "sk-new-key" | hermes-key-rotate emergency --provider <provider> --yes-i-understand-downtime-risk

# 2. Verify new key works
hermes-key-rotate --status --provider <provider>

# 3. Regenerate env
hermes-key-rotate --render-env

# 4. Check all providers still functional
hermes-usage --json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'{p}: {v[\"status\"]}') for p,v in d.get('providers',{}).items()]"
```

### Incident: Bitwarden/Vaultwarden Unreachable

```bash
# 1. Check connectivity
curl -I $VAULTWARDEN_SERVER_URL

# 2. Try re-authentication
hermes-key-rotate --secret-backend vaultwarden --unlock

# 3. If server is down, use cached .env.generated
# Hermes reads .env.generated directly — keys remain usable
cat ~/.hermes/.env.generated  # verify it exists and is recent
```

### Incident: Provider Rate Limited

```bash
# 1. Check which provider
hermes-usage --limits

# 2. Switch to fallback route
hermes-route-manager apply-profile cheap

# 3. Monitor recovery
hermes-usage --compact  # re-run until limits clear
```

### Incident: Image Generation Failing

```bash
# 1. Diagnose all backends
hermes-ops-kit image doctor

# 2. If local ComfyUI is down
hermes-ops-kit image set-default fast  # switch to Gemini cloud

# 3. Test
hermes-ops-kit image test "test prompt" --route fast
```

## Recovery Procedures

### Restore from Rotation Failure

```bash
# Check rotation state
hermes-key-rotate --status --provider <provider>

# `resume` returns UnsupportedOperation: inspect the credential state with --status,
# then run `rotate --provider <provider>` again
```

### Regenerate .env.generated

```bash
# If .env.generated is missing or corrupted
hermes-key-rotate --unlock         # ensure session is valid
hermes-key-rotate --render-env     # regenerate
```

### Restore Config from Backup

```bash
# Config patches create .bak automatically
cp ~/.hermes/config.yaml.bak ~/.hermes/config.yaml
cp ~/.hermes/ops-kit/image_routes.yaml.bak ~/.hermes/ops-kit/image_routes.yaml
```

## Log Locations

| Log                  | Path                                             | Rotation                 |
| -------------------- | ------------------------------------------------ | ------------------------ |
| Key rotation audit   | `~/.hermes/key-rotation-audit.jsonl`             | Append-only              |
| Assistant delegation | `~/.hermes/assistants/audit.jsonl`               | Append-only              |
| Task lifecycle       | `~/.hermes/assistants/tasks.sqlite`              | SQLite                   |
| Audit events         | `~/.hermes/ops-kit/audit/events.jsonl`           | Append-only              |
| Rotation checkpoints | `~/.hermes/rotation_checkpoints/<provider>.json` | Overwritten per rotation |

## Lock Files

Per-provider advisory locks prevent concurrent rotations:

```bash
# Check for stale locks
ls -la ~/.hermes/locks/

# Locks auto-clean on process exit — manual removal only if process crashed
rm ~/.hermes/locks/<provider>.lock
```

## Related

- [[Architecture]] — module architecture and data flows
- [[Hermes Compatibility]] — Hermes integration and security model
- [[Threat Model]] — threat analysis and mitigations
- [[Key Management Lifecycle]] — full secret lifecycle, rotation modes, revocation matrix
- [[Quickstart]] — getting started guide
