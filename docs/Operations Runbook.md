---
title: Operations Runbook
tags: [hermes, ops-kit, operations, runbook, incident-response]
created: 2026-06-04
modified: 2026-06-04
---

# Operations Runbook

Day-to-day operational procedures and incident response for Hermes Ops Kit.

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
# 1. Immediate rotation with emergency flag
echo "sk-new-key" | hermes-key-rotate rotate --provider <provider> --manual-new-key-stdin --emergency

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

# Resume interrupted rotation
hermes-key-rotate resume --provider <provider>

# Force rollback if stuck
hermes-key-rotate rollback --provider <provider>
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
