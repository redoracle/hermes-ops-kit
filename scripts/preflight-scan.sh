#!/usr/bin/env bash
# Hermes Ops Kit — Pre-Flight Secret Scan
# Run before every release to verify no secrets are present in the working tree.
#
# Usage:
#   bash scripts/preflight-scan.sh
#
# Exit code: non-zero if any pattern is detected (2 when GNU grep with -P is missing).
#
# Scope: files git tracks plus new files it does not ignore, so the scan covers exactly what
# a commit could contain and never wanders into .venv, node_modules or local-only folders.

set -euo pipefail

# Run from the repository root whatever the caller's directory is.
cd "$(dirname "$0")/.."

# The checks use PCRE (grep -P). BSD grep, the macOS default, rejects -P, and a hidden error
# would make every check pass without looking at anything.
if echo x | grep -qP x 2>/dev/null; then
  GREP="grep"
elif command -v ggrep >/dev/null 2>&1 && echo x | ggrep -qP x 2>/dev/null; then
  GREP="ggrep"
else
  echo "FAIL: GNU grep with -P support is required (macOS: brew install grep)"
  exit 2
fi

# hits <grep flags> <pattern> <path-regex of files allowed to match> <file globs...>
# Prints "path:line:text" for every match outside the allowed files. A trailing /dev/null
# keeps grep from reading stdin when git lists no file.
hits() {
  local flags=$1 pattern=$2 allowed=$3
  shift 3
  git ls-files -c -o --exclude-standard -z -- "$@" \
    | xargs -0 "$GREP" "-${flags}" -- "$pattern" /dev/null 2>/dev/null \
    | grep -vE "^(${allowed}):" || true
}

fail() { # <title> <hits>
  echo "  FAIL: $1"
  echo "$2"
  exit 1
}

TEXT=("*.py" "*.yaml" "*.yml" "*.md")
# The script and its test hold deliberate leak examples (the test proves each check fails).
THIS="scripts/preflight-scan\.sh|tests/test_preflight_scan\.py"

echo "=== Pre-Flight Secret Scan ==="
echo ""

# ── 1. IP addresses (private and CGNAT ranges) ──────────────────────────
echo "1. Non-loopback IP addresses …"
# The URL-policy and route tests use private-range addresses as test data on purpose.
IP_TESTS="tests/test_url_policy\.py|tests/test_image_download\.py|tests/test_route_runtime_harness\.py"
HITS=$(hits Pn '\b(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)\d{1,3}\.\d{1,3}\b' \
  "$IP_TESTS|$THIS" "${TEXT[@]}" "*.sh")
[ -z "$HITS" ] || fail "found non-local IP addresses" "$HITS"
echo "  PASS"

# ── 2. SSH user references ──────────────────────────────────────────────
echo "2. SSH user references …"
HITS=$(hits Pn 'ubuntu@' "$THIS" "${TEXT[@]}" "*.sh")
[ -z "$HITS" ] || fail "found SSH user references" "$HITS"
echo "  PASS"

# ── 3. Cloud provider / host references ─────────────────────────────────
echo "3. Cloud provider / host references …"
# The maintainer's public GitHub handle appears in the generated changelog and in the ADR /
# design text that describes who commits; those files are the only allowed matches.
HANDLE_DOCS="CHANGELOG\.md|docs/Architecture Decisions\.md|docs/plugin-security-scanner-design\.md"
# The project's own repository URL contains the handle and is meant to be public.
OWN_REPO_URL='github(usercontent)?\.com/redoracle/hermes-ops-kit'
HITS=$(hits Pni '(oracle|orap|orace|oracle cloud|oracle vps)' "$HANDLE_DOCS|$THIS" "${TEXT[@]}" \
  | grep -vE "$OWN_REPO_URL" || true)
[ -z "$HITS" ] || fail "found cloud provider / host references" "$HITS"
echo "  PASS"

# ── 4. Personal names ───────────────────────────────────────────────────
echo "4. Personal / org names …"
HITS=$(hits Pni '(sancho|@.*_bot|oooooo|redoracle)' "$HANDLE_DOCS|$THIS" "${TEXT[@]}" \
  | grep -vE "$OWN_REPO_URL" || true)
[ -z "$HITS" ] || fail "found personal / org names" "$HITS"
echo "  PASS"

# ── 5. Real filesystem paths ────────────────────────────────────────────
echo "5. Real filesystem paths …"
HITS=$(hits Pn '[/]home[/]ubuntu|AI_STUDIO/' "$THIS" "${TEXT[@]}")
[ -z "$HITS" ] || fail "found real filesystem paths" "$HITS"
echo "  PASS"

# ── 6. Real API key patterns ────────────────────────────────────────────
echo "6. Real API key patterns …"
# Allowed: the files that DEFINE detection patterns (redaction, delegation policy, result
# sanitizer, declarative rules), the tests (fake keys; real ones are gitleaks' job below),
# and documentation placeholders such as "sk-ant-admin-..." or a backticked prefix `ghp_`.
KEY_FILES="hermes_ops_kit/security/redaction\.py|hermes_ops_kit/assistants/(policy|result_sanitizer)\.py|hermes_ops_kit/policy/rules\.yaml|hermes_ops_kit/usage_metrics_v2\.py|tests/.*"
HITS=$(hits Pn '(sk-ant-|sk-proj-|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|ghp_|github_pat_)' \
  "$KEY_FILES|$THIS" "*.py" "*.yaml" "*.md")
# Known fake values and documentation placeholders
# shellcheck disable=SC2016  # backticks are literal characters in this regex
ALLOWED='sk-abc123testsecretnotreal|sk-ant-api03-abc123|sk-proj-abc123|AIzaSyDabc123|ghp_abc123|sk-ant-admin-\.\.\.|"sk-ant-"|`ghp_`|`github_pat_`'
if [ -n "$HITS" ]; then
  REAL=$(echo "$HITS" | grep -vE "$ALLOWED" || true)
  [ -z "$REAL" ] || fail "found real API key patterns" "$REAL"
fi
echo "  PASS"

# ── 7. Gitleaks ─────────────────────────────────────────────────────────
echo "7. Gitleaks scan …"
if command -v gitleaks &>/dev/null; then
  gitleaks detect --source . --no-git --redact
  echo "  PASS"
else
  echo "  SKIP (gitleaks not installed)"
fi

echo ""
echo "=== All scans passed ==="
