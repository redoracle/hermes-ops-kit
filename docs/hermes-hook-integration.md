# Hermes Hook Integration — Plugin Security Scanner

> How the plugin scanner integrates with Hermes sessions and preflight.

---

## Overview

Hermes lifecycle hooks can run the scanner for reporting and post-load checks:

- **on_session_start** — Report on loaded plugins; cannot gate plugin loading
- **install.sh security gate** — Deep scan before first install
- **manual update scan** — Force rescan after git pull / update

For actual boot enforcement, use the preflight command documented below.
A normal `hermes gateway restart` does not run preflight; it only triggers the
report-only session hook after plugins load.

## Configuration

No hook configuration is required. When `hermes-ops-kit` is enabled, its
Python plugin registration subscribes `plugin_security_scan` to Hermes
`on_session_start`.

Do not add `plugin:` / `hook:` entries under `~/.hermes/config.yaml` `hooks:`.
That block configures shell-command hooks and requires a `command` field.

## Hook Behavior by Profile

| Trigger                    | Profile | Categories      | Timeout | Behavior           |
| -------------------------- | ------- | --------------- | ------- | ------------------ |
| `on_session_start`         | startup | secrets, policy | 12s     | Report only        |
| `install.sh` security gate | install | secrets, policy | 60s     | Block installation |
| Manual update scan         | update  | secrets, policy | 60s     | Report / exit code |

## CI/CD Usage

For CI pipelines, use the `ci` profile which is optimized for headless execution:

```bash
# In CI pipeline
hermes-ops-kit plugin scan --profile ci --json --force

# Exit code 1 if any plugin is blocked (non-zero = pipeline failure)
```

```yaml
# GitHub Actions example
- name: Plugin Security Scan
  run: |
    hermes-ops-kit plugin scan --profile ci --json > scan-results.json
    # Fail pipeline if blocked plugins found
    python3 -c "
    import json, sys
    data = json.load(open('scan-results.json'))
    blocked = [p for p in data['result']['plugins'] if p['risk_level'] == 'critical']
    if blocked:
        print(f'BLOCKED: {len(blocked)} plugins have critical findings')
        sys.exit(1)
    print('All plugins passed security scan')
    "
```

## Approval Mode Selection

| Mode          | Behavior                                   | Use Case     |
| ------------- | ------------------------------------------ | ------------ |
| `auto`        | Auto-decides based on risk level (default) | Headless, CI |
| `interactive` | Prompts operator for medium/high findings  | Local dev    |
| `strict`      | Blocks everything > low, no override       | Production   |

```yaml
# ~/.hermes/ops-kit/plugin_scanner.yaml
approval:
  mode: auto
```

## Session Start Hook Flow

The session hook is informational because Hermes loads plugins before firing it:

```text
Hermes starts
    │
    ▼
Hermes loads configured plugins
    │
    ▼
on_session_start hook reports findings
```

Use `hermes-ops-kit preflight && hermes gateway run` to gate loading.
For a one-off safe restart, run
`hermes-ops-kit preflight && hermes gateway restart`.

## Manual Scan After Approval

After approving a plugin, rescan to confirm it loads:

```bash
# 1. Approve the plugin
hermes-ops-kit plugin approve my-plugin

# 2. Verify status
hermes-ops-kit plugin policy

# 3. Force rescan (now approved, will show ENABLED)
hermes-ops-kit plugin scan --plugin my-plugin --force
```

## Testing Hook Integration

```bash
# Simulate startup scan
hermes-ops-kit plugin scan --profile startup

# Simulate install scan on a new plugin
hermes-ops-kit plugin scan --profile install --plugin /path/to/new/plugin --force

# Simulate CI scan (JSON output, no TTY)
NO_COLOR=1 hermes-ops-kit plugin scan --profile ci --json --force
```

## 0.2.0 Hook Behavior Notes

### Approval Reflection

Plugin approval affects the enforcement decision for MEDIUM and HIGH risk.
It never changes the scanner's objective risk result, and CRITICAL findings
remain a hard boot block. To accept a specific critical pattern, use an
audited per-rule override so the exception is explicit and narrow.

### Rule Overrides at Scan Time

Rule overrides (from `plugin_policy.json` → `rule_overrides`) are applied
during scanning, before scoring. This means:

- A `downgrade:info` override on a CRITICAL rule prevents the plugin from
  being blocked by that finding
- An `allow` override removes the finding entirely (zero score contribution)
- Overrides are evaluated per-plugin per-rule — they don't affect other plugins

### Score Cap

The score cap mechanism ensures that many low-severity findings can't
aggregate to block a plugin:

- No CRITICAL individual finding → max HIGH
- No HIGH individual finding → max MEDIUM

This means documentation-heavy plugins with many INFO findings won't be
blocked by score aggregation alone.

### Skills Directory

The scanner now auto-detects skill-type plugins (>60% `.md` files) and
applies softer severity. Prompt injection findings in a skill are
classified as topic content (INFO), not attacks (ERROR).

## Pre-Boot Enforcement (Preflight)

> **Hermes session hooks fire AFTER plugin loading.** The scanner cannot block
> plugins at runtime because by the time the hook fires, plugins are already
> loaded. A normal gateway restart does not run preflight. Instead, ops-kit
> provides a **preflight command** that runs
> before Hermes boots and synchronizes scanner decisions with
> `~/.hermes/config.yaml`.

### How it works

```text
hermes-ops-kit preflight  →  scan + enforce  →  hermes gateway run
     ↓                          ↓
  exit 2 if blocked        updates plugins.enabled / plugins.disabled
```

1. **Scan** all plugins with built-in detectors (uses cache; external deep tools are skipped)
2. **Compare** scan results against approval policy (`plugin_policy.json`)
3. **Audit MCP tools** and disable servers with unknown or unapproved HIGH tools
4. **Sync** `~/.hermes/config.yaml` so unsafe plugins and MCP servers are excluded
5. **Exit 0** if safe to boot, **exit 2** if CRITICAL plugins or MCP tools exist

> **⚠️ Boot time impact:** Preflight adds scan time to every Hermes boot.
> With a warm cache (7-day TTL), the overhead is ~1-2s (cache lookup only).
> When the cache is cold or expired — first boot, plugin updates, scanner
> version changes — the overhead is ~12s (full startup scan). External tools
> (Semgrep, gitleaks, Bandit) add additional time per scan if installed.
> This is a trade-off: slower boot for guaranteed pre-execution security
> checks. Skip with `hermes gateway run` (no preflight) if boot speed is
> critical.

### Usage

```bash
# Preview what would change (no config mutation)
hermes-ops-kit preflight --dry-run

# Enforce scanner decisions into config.yaml
hermes-ops-kit preflight

# Machine-readable output for CI/supervisors
hermes-ops-kit preflight --json

# Safe boot: preflight gates Hermes startup
alias hermes-safe='hermes-ops-kit preflight && hermes gateway run'

# Safe one-off gateway restart
hermes-ops-kit preflight && hermes gateway restart
```

### Supervisor Integration

**launchd** (macOS):

```xml
<key>ProgramArguments</key>
<array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>hermes-ops-kit preflight && exec hermes gateway run</string>
</array>
```

**systemd** (Linux):

```ini
[Service]
ExecStartPre=/usr/local/bin/hermes-ops-kit preflight
ExecStart=/usr/local/bin/hermes gateway run
```

### Enforcement Rules

| Risk Level | Approved? | Action                                     |
| ---------- | --------- | ------------------------------------------ |
| NONE / LOW | —         | Existing opt-in state preserved            |
| MEDIUM     | No        | Added to `plugins.disabled`                |
| MEDIUM     | Yes       | Existing opt-in state preserved            |
| HIGH       | No        | Added to `plugins.disabled`                |
| HIGH       | Yes       | Existing opt-in state preserved            |
| CRITICAL   | No        | **Blocks boot** (exit 2)                   |
| CRITICAL   | Yes       | **Still blocks boot**; use a rule override |

### MCP Enforcement Rules

Hermes exposes MCP activation at server granularity, so one unsafe tool disables
the entire server. Preflight uses static tool declarations only; it never launches
or contacts an unaudited MCP server. Servers without static tool declarations are
disabled until they can be reviewed interactively and configured.

| Tool Audit Result                       | Server Action                         |
| --------------------------------------- | ------------------------------------- |
| No tools discovered / audit incomplete  | Set `mcp_servers.<id>.enabled: false` |
| LOW / MEDIUM only                       | Preserve existing enabled state       |
| HIGH with explicit tool/server approval | Preserve existing enabled state       |
| HIGH without approval                   | Set `enabled: false`                  |
| CRITICAL, even if approved              | Set `enabled: false` and block boot   |
