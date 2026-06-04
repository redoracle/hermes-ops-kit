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
