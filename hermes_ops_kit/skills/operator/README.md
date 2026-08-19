# Operator playbook skills (vendored)

Version-controlled copies of the **operator** skills that live, at runtime, under
`~/.hermes/skills/autonomous-ai-agents/`. They were originally authored ad-hoc during
operator sessions and were **not** tracked anywhere — vendoring them here is the source of
truth so they survive re-installs and can be reviewed/diffed.

Unlike the skills in `skills/bw/`, `skills/hermes-key-rotate/`, and `skills/hermes-ops-kit/`
(which are generated/shipped by the plugin and registered via `__init__.py`), these are
**hand-authored playbooks** with their own YAML frontmatter. They are NOT auto-registered by
the plugin loader; treat them as reference documentation and as the deploy source.

## Contents

| Skill                         | Purpose                                                                                                                                                                       |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hermes-ops-kit/`             | Operator workflow for inspecting the secret store, rotating keys, and creating/verifying Vaultwarden items via `bw`. Includes `references/` for inventory and unlock recipes. |
| `hermes-ops-kit-vaultwarden/` | `bw`-first item-level CRUD against Vaultwarden/Bitwarden, with collection/visibility gotchas.                                                                                 |
| `review-response/`            | Systematic workflow for receiving, verifying, and responding to PR review feedback — especially hermes-sweeper automated reviews. Distilled from PRs #38823 and #38853.               |
| `credential-leak-audit/`      | Find and fix credential leaks in log/display output. Covers the URL sanitization pattern (`user:pass@host`, `?api_key=`) applied to `logger.info`, `print`, and `check_info` calls.   |
| `rebase-conflict/`            | Systematic approach to rebasing a stale PR branch onto a diverged upstream. Covers parser extraction, function reordering, and same-region addition conflict patterns.                |
| `config-reconciliation/`     | Pattern for writing a new config.yaml reconciler — envelope contract, safety invariants (backup/snapshot/atomic-write/dry-run), idempotent actions, and sub-reconciler composition. Distilled from `headroom_ops/reconcile.py` and `security/plugin_scanner/enforce.py`. |

## Conventions (must stay true for any edit)

- Never hardcode the server URL or credentials — read them from `~/.hermes/.env`
  (`VAULTWARDEN_SERVER_URL`, `VAULTWARDEN_USER`, `VAULTWARDEN_PASSWORD`).
- Never `source` `~/.hermes/.env` wholesale; parse only the keys you need (a password may
  contain shell metacharacters). See [`skills/bw/SKILL.md`](../bw/SKILL.md).
- Never print a password field or a raw `BW_SESSION` token.

## Re-deploy

These are plain Markdown; deploy by copying back to the runtime skills dir:

```bash
SRC=skills/operator
DST=~/.hermes/skills/autonomous-ai-agents
cp -R "$SRC/hermes-ops-kit" "$SRC/hermes-ops-kit-vaultwarden" "$DST/"
```
