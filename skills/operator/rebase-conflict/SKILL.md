---
name: rebase-conflict
description: Systematic approach to rebasing a stale PR branch onto a diverged upstream main — from PRs #38823 and #38853.
---

# Rebase Conflict Resolution

Systematic workflow for rebasing a stale PR branch when upstream has moved significantly.
Distilled from rebasing two PRs (#38823, #38853) that were each 10+ commits behind
`upstream/main`.

## Pre-flight

```bash
# Always unset GITHUB_TOKEN
unset GITHUB_TOKEN

# Fetch latest upstream
git fetch upstream main

# Check how far behind you are
git merge-base HEAD upstream/main
git log --oneline HEAD..upstream/main | wc -l  # commits behind

# Save current work (uncommitted fixes)
git stash push -m "pre-rebase-wip"
```

## Phase 1: Understand the divergence

Before attempting the rebase, understand WHAT changed upstream:

```bash
# Which of YOUR files were also modified upstream?
git diff --name-only HEAD..upstream/main -- $(git diff --name-only main...HEAD)

# Check if any new files were added upstream that conflict
git show upstream/main:<path> 2>/dev/null
```

Key questions to answer:
1. Was any code EXTRACTED to a new file (god-file split)?
2. Were functions REORDERED (e.g., native-first vs text-first ordering)?
3. Were new functions ADDED in the same region as your additions?

## Phase 2: Trial merge

```bash
# Stash first, then trial merge
git stash push -m "pre-rebase-check"
git rebase upstream/main
# ... if conflicts ...
git rebase --abort
```

Read each conflict carefully. Three common patterns:

### Pattern A: Parser extraction

Upstream extracted inline argument-parser code into `hermes_cli/subcommands/<name>.py`.
Your branch adds flags to the old inline location.

**Resolution**: Accept upstream's extraction. Move your new flags to the subcommands file.

```python
# OLD (your branch, inline in main.py)
doctor_parser.add_argument("--json", ...)
doctor_parser.add_argument("--verbose", ...)

# NEW (upstream main)
build_doctor_parser(subparsers, cmd_doctor=cmd_doctor)

# FIX: Add flags to subcommands/doctor.py in build_doctor_parser()
```

### Pattern B: Function reordering

Upstream reordered branches within a function (e.g., capability check before fallback
check). Your branch added log calls in the OLD order.

**Resolution**: Preserve upstream's ordering. Transplant your log calls into the
correct branches without reordering them.

```python
# UPSTREAM: native-first
supports = _lookup_supports_vision(provider, model, cfg)
if supports is True:
    return "native"           # ← add logger.debug here
if supports is False:
    return "text"             # ← add logger.debug here
if _explicit_aux_vision_override(cfg):
    return "text"             # ← add logger.info here
return "text"                 # ← add logger.debug here for unknown
```

### Pattern C: New function in the same region

Upstream added a function between two of yours, or your branch added functions in
a region where upstream also added code.

**Resolution**: Keep BOTH functions. Order: upstream additions first, then your
additions, respecting their logical grouping.

## Phase 3: Execute the rebase

```bash
git rebase upstream/main

# For each commit that conflicts:
# 1. Read the conflict markers
grep -n '<<<<<<< HEAD\|=======\|>>>>>>>' <file>

# 2. Resolve — keep upstream structure, transplant your changes

# 3. Stage and continue
git add <file>
git rebase --continue

# 4. If a later commit's changes are ALREADY applied (from resolving an earlier
#    conflict), skip it:
git rebase --skip
```

## Phase 4: Restore working changes

After successful rebase:

```bash
# Pop stashed fixes
git stash pop

# If stash conflicts:
# - Read both sides of each conflict
# - Keep upstream additions that your stash didn't know about
# - Re-apply your fix on top
```

## Phase 5: Verify

```bash
# Syntax check every modified file
python3 -c "
import ast
for f in ['file1.py', 'file2.py']:
    ast.parse(open(f).read())
    print(f'{f}: OK')
"

# Run the full test suite
uv run pytest tests/ -q -m "not integration" -o "addopts="

# Verify diff looks correct
git diff upstream/main --stat
git log --oneline upstream/main..HEAD
```

## Common pitfalls

| Pitfall | Fix |
|---------|-----|
| `if moa_config is None:` without body | Upstream restructured conditionals — read full upstream version, restore the body |
| Closing `>>>>>>>` marker left behind | `grep -rn '>>>>>>>'` and remove |
| Stash conflicts after rebase | Resolve by hand: keep upstream code, layer your fixes |
| Forgetting the `--force-with-lease` | `git push --force-with-lease origin <branch>` |

## Post-rebase check list

- [ ] All files parse without syntax errors
- [ ] No `<<<<<<<`, `=======`, or `>>>>>>>` markers remain
- [ ] Full test suite passes (0 regressions)
- [ ] `git diff upstream/main --stat` shows only expected files
- [ ] Upstream additions preserved (check with `git show upstream/main:<file>`)
- [ ] Stashed working changes re-applied correctly

## Related patterns

- [[review-response]] — systematic approach to receiving and acting on review feedback
- [[credential-leak-audit]] — finding and fixing credential leaks in output
