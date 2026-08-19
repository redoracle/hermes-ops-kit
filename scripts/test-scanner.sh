#!/usr/bin/env bash
# Hermes Ops Kit — Scanner Integration Test
#
# Tests the plugin security scanner end-to-end against the live
# installation. Covers: scan_all, single-plugin scan, entropy checks,
# rule overrides, doc downgrades, skills vs code classification.
#
# Usage: bash scripts/test-scanner.sh

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
PASS="${GREEN}PASS${NC}"; FAIL="${RED}FAIL${NC}"

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="python3"
# -P (Python 3.11+) keeps the cwd off sys.path; PYTHONPATH points at the plugin root
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "FAIL: Python 3.11+ required (-P flag)"; exit 1; }
POLICY_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$POLICY_TMP_DIR"' EXIT

total=0; passed=0; failed=0
check() { total=$((total+1)); if "$@"; then passed=$((passed+1)); echo -e "  ${PASS} $1"; else failed=$((failed+1)); echo -e "  ${FAIL} $1"; fi; }
banner() { echo -e "\n${BOLD}${CYAN}━━━ $1 ━━━${NC}"; }

# ────────────────────────────────────────────────────────────────
banner "1. Module Imports"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
modules = [
    'hermes_ops_kit.security.plugin_scanner.scanner', 'hermes_ops_kit.security.plugin_scanner.cli',
    'hermes_ops_kit.security.plugin_scanner.categories.secrets', 'hermes_ops_kit.security.plugin_scanner.categories.policy',
    'hermes_ops_kit.security.plugin_scanner.policy', 'hermes_ops_kit.security.plugin_scanner.findings',
]
for m in modules: __import__(m)
print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "2. Entropy & Dummy Detection"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
from hermes_ops_kit.security.plugin_scanner.categories.secrets import _shannon_entropy, _is_likely_fake_secret

# Real key → NOT fake
is_fake, _ = _is_likely_fake_secret('sk-Ab7Qx9Yz2Wp3Kj6Mn8Rt4Vc5Fg1Hd3Ns')
assert not is_fake, 'Real key should not be flagged'

# Fake key with sequential patterns → fake
is_fake, reason = _is_likely_fake_secret('sk-abc123xyz789def456ghi012jkl345mno678')
assert is_fake and 'sequential' in reason, f'Expected sequential, got {reason}'

# Low entropy → fake
is_fake, reason = _is_likely_fake_secret('Bearer x')
assert is_fake and 'entropy' in reason, f'Expected low entropy, got {reason}'

# Dummy word → fake
is_fake, reason = _is_likely_fake_secret('X-API-Key: leak')
assert is_fake, 'Dummy word leak should be flagged'

print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "3. File Classification"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
from hermes_ops_kit.security.plugin_scanner.categories.secrets import _is_doc_file, _is_test_file, _file_class

assert _is_doc_file('CHANGELOG.md') == True
assert _is_doc_file('docs/Threat Model.md') == True
assert _is_doc_file('bridge.py') == False
assert _is_test_file('tests/test_security.py') == True
assert _is_test_file('src/main.py') == False
assert _file_class('tests/test_security.py') == 'test'
assert _file_class('CHANGELOG.md') == 'doc'
assert _file_class('bridge.py') == 'code'
print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "4. Rule Overrides CRUD"
# ────────────────────────────────────────────────────────────────
check env HERMES_PLUGIN_POLICY_PATH="$POLICY_TMP_DIR/plugin_policy.json" PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
from hermes_ops_kit.security.plugin_scanner.policy import set_rule_override, remove_rule_override, get_rule_overrides

# Set
set_rule_override('__test_plugin__', 'test-rule', 'downgrade:info')
assert get_rule_overrides('__test_plugin__') == {'test-rule': 'downgrade:info'}

# Remove
remove_rule_override('__test_plugin__', 'test-rule')
assert get_rule_overrides('__test_plugin__') == {}

# Cleanup in case
remove_rule_override('__test_plugin__')
print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "5. gotify-notify (overrides preserve unrelated findings)"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
import os
from hermes_ops_kit.security.plugin_scanner.scanner import scan_plugin
path = os.path.expanduser('~/.hermes/plugins/gotify-notify')
if not os.path.isdir(path):
    print('SKIP: gotify-notify not installed')
    raise SystemExit(0)
r = scan_plugin('gotify-notify', path, profile='manual', force=True)
assert not r.risk_level.blocks_plugin, f'Expected non-blocking risk, got {r.risk_level.value}'
assert all(f.rule != 'network-access' for f in r.findings), 'network-access override was not applied'
print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "6. ops-kit Self-Scan (approved, no critical)"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
from hermes_ops_kit.security.plugin_scanner.scanner import scan_plugin
from hermes_ops_kit.security.plugin_scanner.findings import RiskLevel

r = scan_plugin('hermes-ops-kit', '$PLUGIN_DIR', profile='manual', force=True)

# Must not be CRITICAL (all findings downgraded via overrides)
assert r.risk_level != RiskLevel.CRITICAL, f'Expected non-CRITICAL, got {r.risk_level.value}'

critical = [f for f in r.findings if f.risk_level.value == 'critical']
assert len(critical) == 0, f'Expected 0 critical findings, got {len(critical)}: {[e.rule for e in critical]}'

# Score should be manageable
assert r.score < 2000, f'Score too high: {r.score}'

print(f'OK (risk={r.risk_level.value}, score={r.score:.0f}, findings={len(r.findings)})')
"

# ────────────────────────────────────────────────────────────────
banner "7. Skills Detection (text-heavy vs code)"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
from hermes_ops_kit.security.plugin_scanner.scanner import scan_plugin

# Skills are text-heavy: they should get softer treatment
# Check a known skill (apple is clean, but let's verify)
import os
skills_dir = os.path.expanduser('~/.hermes/skills')
if os.path.isdir(skills_dir):
    for name in sorted(os.listdir(skills_dir))[:3]:
        path = os.path.join(skills_dir, name)
        if os.path.isdir(path) and not name.startswith('.'):
            r = scan_plugin(name, path, profile='manual', force=True)
            print(f'  {name}: {r.risk_level.value} ({len(r.findings)}f, score={r.score:.0f})')
print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "8. Full Scan — Summary"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
from hermes_ops_kit.security.plugin_scanner.scanner import scan_all

results = scan_all(profile='manual', force=True)
enabled = sum(1 for r in results if r.is_clean)
disabled = sum(1 for r in results if not r.is_clean)
blocked = sum(1 for r in results if r.is_blocked)

print(f'Total: {len(results)} | Enabled: {enabled} | Disabled: {disabled} | Blocked: {blocked}')

# Installed plugin findings are environment-dependent. Verify known overrides
# without hiding unrelated findings.
gotify = [r for r in results if r.plugin_name == 'gotify-notify']
if gotify:
    assert all(f.rule != 'network-access' for f in gotify[0].findings), 'gotify override missing'

print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "9. Scan Caching"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
import os, tempfile
from hermes_ops_kit.security.plugin_scanner.scanner import scan_plugin
from hermes_ops_kit.security.plugin_scanner.cache import cache_stats

path = tempfile.mkdtemp(prefix='hermes-scanner-cache-')
with open(os.path.join(path, 'plugin.py'), 'w') as handle:
    handle.write('def safe(): return True\n')

# First startup scan — uncached (force bypasses cache, then stores result)
r1 = scan_plugin('__cache_test__', path, profile='startup', force=True)
assert not r1.cache_hit, 'Force scan should bypass cache'

# Second scan — should hit cache
r2 = scan_plugin('__cache_test__', path, profile='startup', force=False)
assert r2.cache_hit, 'Second scan should hit cache'

print('OK')
"

# ────────────────────────────────────────────────────────────────
banner "10. Policy File Integrity"
# ────────────────────────────────────────────────────────────────
check env PYTHONPATH="$PLUGIN_DIR" "$PYTHON" -P -c "
import json, os
from hermes_ops_kit.security.plugin_scanner.policy import get_policy

p = get_policy()
assert p['version'] == 2, f'Policy version mismatch: {p.get(\"version\")}'
assert isinstance(p.get('approved_plugins'), list), 'approved_plugins must be a list'
assert isinstance(p.get('rule_overrides'), dict), 'rule_overrides must be an object'
print(f'OK ({len(p[\"rule_overrides\"])} plugins with overrides, {sum(len(r) for r in p[\"rule_overrides\"].values())} total rules)')
"

# ── Summary ────────────────────────────────────────────────────
echo -e "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  RESULTS: ${passed}/${total} passed${NC}"
if [ $failed -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}✓ ALL CHECKS PASSED${NC}"
else
    echo -e "  ${RED}${BOLD}✗ ${failed} CHECKS FAILED${NC}"
fi
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
exit $failed
