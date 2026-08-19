# Hermes Compatibility Audit

Monitor and compare official Hermes Agent changelogs
(https://github.com/NousResearch/hermes-agent/releases) against the current
hermes-ops-kit implementation, then propose an implementation plan that keeps
ops-kit (1) always compatible with the latest hermes-agent release and
(2) improved toward enterprise-grade quality where possible.

## When to use

Invoke this skill after any hermes-agent release, before a ops-kit release, or
when asked "is ops-kit compatible with the latest Hermes?" / "what gaps exist
vs the new Hermes release?" / "audit compatibility."

## Prerequisites (grounded data — do NOT reason from memory)

1. **Release data** — run the grounded fetcher (never hallucinate release notes):
   ```bash
   python3 scripts/hermes_compat_audit.py --json --releases 3
   ```
   On network/rate-limit failure it exits 0 with `fetch_error` set — degrade to
   auditing against the local manifest only, and say so explicitly in the report.
   For the full release body, `WebFetch` the `html_url` of the latest release.

2. **Compatibility manifest** — `config/compat.yaml` records the target Hermes
   version, codename, and per-feature coverage (`covered` / `partial` / `missing`
   / `not-ops-kit-lane`). This is the baseline the passes update.

3. **Scope boundary** — re-read `CLAUDE.md` "Scope & boundaries": ops-kit owns
   provider routing, secret/key lifecycle, usage/cost governance, plugin/MCP
   security, diagnostics, remote assistant delegation, image/LLM routing. It must
   NOT reimplement Hermes core (agent runtime, messaging, model dispatch,
   conversation handling). When core now owns a capability, the audit must mark
   it `not-ops-kit-lane` and recommend **integration, not duplication**.

## The audit — 3 passes, each updating `docs/compat-audit.md`

`docs/compat-audit.md` is the living audit report. Each pass APPENDS a dated
section; never delete prior passes (lineage matters). Start the file from the
template at the bottom of this skill if it does not exist.

### Pass 1 — Structural comparison (coverage map)

For each feature area in the latest release notes:
- Map it to an ops-kit lane (or `not-ops-kit-lane`).
- Classify: `covered` / `partial` / `missing` / `not-ops-kit-lane`.
- Cite the ops-kit file:line that implements it (use `rg`/`Read`), or the core
  file:line that owns it.
- Update `config/compat.yaml` `features:` if a new area appears or status changed.
- Append the **Pass 1 — Coverage map** section to `docs/compat-audit.md` with a
  table (area | lane | status | evidence file:line).

### Pass 2 — Gap analysis (scope + effort)

For every `partial` and `missing` item from Pass 1:
- **Scope verdict**: ops-kit-lane vs Hermes-core-lane vs shared-boundary.
- **Overlap/risk**: does ops-kit duplicate core? (e.g. secret backend vs
  `agent/secret_sources`). If so, recommend defer-to-core, not a new implementation.
- **Effort**: one-line vs scoped-refactor vs architectural.
- Verify each claim against the actual code (Read the cited files) — do not assert
  from the release notes alone.
- Append the **Pass 2 — Gap analysis** section with (item | scope | effort | risk |
  recommendation).

### Pass 3 — Implementation plan (prioritized)

Produce a prioritized plan with two explicit goals:
1. **Compatibility** — keep ops-kit compatible with the latest hermes-agent.
2. **Enterprise-grade improvement** — premium coding quality where ops-kit can
   improve (reuse, simplification, efficiency, test coverage, defense-in-depth).

Structure as tiers:
- 🟢 Tier 1 — quick wins (one-line, in-lane, low risk).
- 🟡 Tier 2 — moderate (scoped refactor, in-lane).
- 🔴 Tier 3 — architectural (requires decision; defer-to-core migrations).
- ⛔ Do-not — Hermes-core-lane items that must NOT be built in ops-kit.

Each tier item: file:line to touch, what to do, verification command. Append the
**Pass 3 — Implementation plan** section. Then print the plan to the user.

## Quality bar (enterprise-grade)

- **Grounded**: every claim cites file:line in ops-kit or core. No memory-only
  assertions about release contents or code behaviour.
- **Scope-disciplined**: never propose reimplementing core. Integration > wrapper
  > reimplementation.
- **Verified**: run the smallest relevant test first (`python3 -m pytest tests/ -q`),
  escalate to broader suites only when the touched area justifies it.
- **No silent caps**: if a pass samples or truncates, state what was dropped.
- **Idempotent**: re-running against the same release converges to the same report.

## `docs/compat-audit.md` template (create if absent)

```markdown
# Hermes Ops Kit — Compatibility Audit Report

Living document. Each audit run appends a dated pass. Do not rewrite history.

## Baseline
- Target Hermes: <version> (<codename>) — from config/compat.yaml
- Latest release fetched: <tag> (<published_at>) — from scripts/hermes_compat_audit.py
- Match status: MATCH | DRIFT

## Pass 1 — Coverage map (<date>)
| Area | Lane | Status | Evidence |
| --- | --- | --- | --- |
| ... | ... | ... | file:line |

## Pass 2 — Gap analysis (<date>)
| Item | Scope | Effort | Risk | Recommendation |

## Pass 3 — Implementation plan (<date>)
### 🟢 Tier 1 — Quick wins
### 🟡 Tier 2 — Moderate
### 🔴 Tier 3 — Architectural
### ⛔ Do-not (Hermes-core-lane)
```
