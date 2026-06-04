#!/usr/bin/env bash
# Hermes Ops Kit — Pre-Flight Secret Scan
# Run before every release to verify no secrets are present in the working tree.
#
# Usage:
#   bash scripts/preflight-scan.sh
#
# Exit code: non-zero if any pattern is detected.

set -euo pipefail

EXCLUDE_DIRS=".venv|__pycache__|.pytest_cache|.ruff_cache|.git"

echo "=== Pre-Flight Secret Scan ==="
echo ""

# ── 1. IP addresses (non-127.x) ─────────────────────────────────────────
echo "1. Non-loopback IP addresses …"
HITS=$(grep -rPn --include="*.py" --include="*.yaml" --include="*.yml" --include="*.md" --include="*.sh" \
  '\b(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)\d{1,3}\.\d{1,3}\b' \
  . 2>/dev/null | grep -vE "$EXCLUDE_DIRS" || true)
if [ -n "$HITS" ]; then
  echo "  FAIL: found non-local IP addresses"
  echo "$HITS"
  exit 1
fi
echo "  PASS"

# ── 2. SSH user references ─────────────────────────────────────────────
echo "2. SSH user references …"
HITS=$(grep -rPn --include="*.py" --include="*.yaml" --include="*.yml" --include="*.md" --include="*.sh" \
  'ubuntu@' . 2>/dev/null | grep -vE "$EXCLUDE_DIRS" || true)
if [ -n "$HITS" ]; then
  echo "  FAIL: found SSH user references"
  echo "$HITS"
  exit 1
fi
echo "  PASS"

# ── 3. Cloud provider / host references ────────────────────────────────
echo "3. Cloud provider / host references …"
HITS=$(grep -rPni --include="*.py" --include="*.yaml" --include="*.yml" --include="*.md" \
  '(oracle|orap|orace|oracle cloud|oracle vps)' . 2>/dev/null | grep -vE "$EXCLUDE_DIRS" || true)
if [ -n "$HITS" ]; then
  echo "  FAIL: found cloud provider / host references"
  echo "$HITS"
  exit 1
fi
echo "  PASS"

# ── 4. Personal names ──────────────────────────────────────────────────
echo "4. Personal / org names …"
HITS=$(grep -rPni --include="*.py" --include="*.yaml" --include="*.yml" --include="*.md" \
  '(sancho|@.*_bot|oooooo|redoracle)' . 2>/dev/null | grep -vE "$EXCLUDE_DIRS" || true)
if [ -n "$HITS" ]; then
  echo "  FAIL: found personal / org names"
  echo "$HITS"
  exit 1
fi
echo "  PASS"

# ── 5. Real filesystem paths ──────────────────────────────────────────
echo "5. Real filesystem paths …"
HITS=$(grep -rPn --include="*.py" --include="*.yaml" --include="*.yml" --include="*.md" \
  '/home/ubuntu|AI_STUDIO/' . 2>/dev/null | grep -vE "$EXCLUDE_DIRS" || true)
if [ -n "$HITS" ]; then
  echo "  FAIL: found real filesystem paths"
  echo "$HITS"
  exit 1
fi
echo "  PASS"

# ── 6. API key patterns (real keys, not redaction rules) ──────────────
echo "6. Real API key patterns …"
HITS=$(grep -rPn --include="*.py" --include="*.yaml" --include="*.md" \
  '(sk-ant-|sk-proj-|sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,}|ghp_|github_pat_)' \
  . 2>/dev/null | grep -vE "$EXCLUDE_DIRS" || true)
# Allow known fake keys in tests and redaction code
ALLOWED="sk-abc123testsecretnotreal|sk-ant-api03-abc123|sk-proj-abc123|AIzaSyDabc123|ghp_abc123"
if [ -n "$HITS" ]; then
  REAL=$(echo "$HITS" | grep -vE "$ALLOWED" || true)
  if [ -n "$REAL" ]; then
    echo "  FAIL: found real API key patterns"
    echo "$REAL"
    exit 1
  fi
fi
echo "  PASS"

# ── 7. Gitleaks ────────────────────────────────────────────────────────
echo "7. Gitleaks scan …"
if command -v gitleaks &>/dev/null; then
  gitleaks detect --source . --no-git --redact
  echo "  PASS"
else
  echo "  SKIP (gitleaks not installed)"
fi

echo ""
echo "=== All scans passed ==="
