---
name: review-response
description: Receive, verify, and respond to PR review feedback — especially hermes-sweeper automated reviews.
---

# Review Response

Systematic workflow for receiving and acting on code review feedback, whether from a human
or an automated hermes-sweeper run.

## Detection

A hermes-sweeper review carries these markers in the body:

```
<!-- hermes-sweeper:review=<pr-number> -->
<!-- hermes-sweeper:review-verdict=keep_open salvageability=<low|medium|high> -->
```

The verdict `keep_open` means the review surfaces blocking concerns. The `salvageability`
grade tells you how much of the existing work is reusable:

| Grade   | Meaning |
|---------|---------|
| `high`  | Minor rework; most of the diff is correct |
| `medium` | Several items need rewriting or transplanting |
| `low`   | Fundamental re-architecture needed |

## Workflow

### 1. Read — complete the feedback without reacting

Pull every comment — issue comments, inline review comments, and the review body.
Use `gh api` to get them all:

```bash
# Review body
gh api repos/{owner}/{repo}/pulls/{pr}/reviews --jq '.[] | select(.user.login == "reviewer")'

# Inline comments
gh api repos/{owner}/{repo}/pulls/{pr}/comments --jq '.[] | select(.pull_request_review_id == <review-id>)'

# Issue comments
gh api repos/{owner}/{repo}/issues/{pr}/comments --jq '.[] | select(.user.login == "reviewer")'
```

### 2. Understand — restate each item in your own words

For each problem the reviewer identified, restate it as a concrete technical requirement.
If anything is unclear, ask before implementing.

### 3. Verify — check against the codebase

**Never implement feedback without verifying it first.** For each claimed problem:

- Check the referenced line numbers against both the PR branch AND upstream main
- Run `rg` searches to confirm the reviewer's claims about callers, imports, etc.
- Check whether the commit hashes referenced by the reviewer exist in your repo

Common hermes-sweeper accuracy issues:
- Line numbers may be stale (from a different snapshot)
- Commit hashes may reference commits not present in your fork
- "Current" state descriptions may target `upstream/main`, not your branch

### 4. Evaluate — is it technically correct for THIS codebase?

External feedback = suggestions to evaluate, not orders to follow. Before implementing:

- Does the fix make sense for this codebase's architecture?
- Would it break existing functionality?
- Is the reviewer's understanding of the codebase current?
- YAGNI check: is the suggested feature actually used?

### 5. Implement — one item at a time, test each

Order of implementation:
1. Blocking issues (breaks, security)
2. Simple fixes (typos, imports)
3. Complex fixes (refactoring, logic)

Run the full test suite after each fix. Never batch changes without testing.

### 6. Respond — technical acknowledgment, no performative agreement

When replying to GitHub:
- **Never**: "You're absolutely right!", "Great point!", "Thanks for catching that!"
- **Instead**: State what was fixed, where, and the test result
- **Inline comments**: Reply in the thread with `gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies`
- **Overall review**: Post a PR comment summarizing addressed items

Example response:

```
Addressed all four issues:

1. **JSON validity** — Remediation print loop guarded by `if not _json_mode:`
2. **Fallback chain** — Replaced with `get_fallback_chain()` from fallback_config
3. **Auxiliary tasks** — Dynamic `sorted(auxiliary.keys())` replaces hardcoded list
4. **Parser location** — Flags moved to `subcommands/doctor.py`

80 passed, 0 failed.
```

## When to push back

Push back with technical reasoning when the suggestion:
- References a file or commit that doesn't exist in your repo
- Would break existing functionality
- Conflicts with established architecture
- Describes a problem already fixed in your branch

Example: "The file `hermes_cli/subcommands/doctor.py` does not exist on this branch or on
`upstream/main`. The referenced commit `568e1276` is not present in this repository. The
parser flags remain correctly in `main.py`."

## Git operations during review

```bash
# Always unset GITHUB_TOKEN so gh uses its own auth
unset GITHUB_TOKEN

# Check if branch is behind upstream
git fetch upstream main
git merge-base --is-ancestor HEAD upstream/main || echo "NEEDS REBASE"

# Trial merge to detect conflicts
git merge --no-commit --no-ff upstream/main
# ... inspect conflicts ...
git merge --abort
```
