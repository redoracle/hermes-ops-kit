#!/usr/bin/env bash
# Hermes Ops Kit — Uninstall Script
# Removes the plugin while preserving user data by default.
#
# Usage:
#   bash uninstall.sh              # Safe: keeps config + env
#   bash uninstall.sh --purge       # Remove everything including config
#   bash uninstall.sh --purge-env   # Also remove ~/.hermes/.env (DANGEROUS)

set -euo pipefail

PLUGIN_NAME="hermes-ops-kit"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"
CONFIG_DIR="$HERMES_HOME/ops-kit"
BIN_DIR="$HOME/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$PLUGIN_NAME"
SCANNER_ROOT="$DATA_DIR/scanners"
PURGE=false
PURGE_ENV=false

# ── Parse flags ─────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=true ;;
    --purge-env) PURGE_ENV=true ;;
    --help|-h)
      echo "Usage: bash uninstall.sh [--purge] [--purge-env]"
      echo "  --purge       Remove config files in ~/.hermes/ops-kit/"
      echo "  --purge-env   Also remove ~/.hermes/.env (DANGEROUS — deletes secrets)"
      exit 0
      ;;
  esac
done

# ── Colour helpers ──────────────────────────────────────────────────
log()  { printf '\033[1;34m[ops-kit]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[1;32m  ✅ %s\033[0m\n' "$*"; }

# ── Disable plugin ──────────────────────────────────────────────────
if command -v hermes >/dev/null 2>&1; then
  hermes plugins disable "$PLUGIN_NAME" 2>/dev/null || true
  ok "Plugin disabled in Hermes"
fi

# ── Remove plugin directory ─────────────────────────────────────────
if [ -d "$PLUGIN_DIR" ]; then
  rm -rf "$PLUGIN_DIR"
  ok "Plugin directory removed: $PLUGIN_DIR"
else
  log "Plugin directory not found: $PLUGIN_DIR (already removed?)"
fi

# ── Pip uninstall ───────────────────────────────────────────────────
if python3 -m pip show hermes-ops-kit >/dev/null 2>&1; then
  python3 -m pip uninstall -y hermes-ops-kit 2>/dev/null || true
  ok "Python package uninstalled"
fi

# ── Managed scanner environment ─────────────────────────────────────
for tool in bandit semgrep; do
  if [ -L "$BIN_DIR/$tool" ] && [ "$(readlink "$BIN_DIR/$tool")" = "$SCANNER_ROOT/$tool/bin/$tool" ]; then
    rm -f "$BIN_DIR/$tool"
  fi
done
if [ -d "$SCANNER_ROOT" ]; then
  rm -rf "$SCANNER_ROOT"
  rmdir "$DATA_DIR" 2>/dev/null || true
  ok "Isolated scanner environment removed"
fi

# ── Config (optional purge) ─────────────────────────────────────────
if $PURGE; then
  if [ -d "$CONFIG_DIR" ]; then
    rm -rf "$CONFIG_DIR"
    ok "Config directory removed: $CONFIG_DIR"
  fi
else
  if [ -d "$CONFIG_DIR" ]; then
    log "Config preserved: $CONFIG_DIR (use --purge to remove)"
  fi
fi

# ── Env file (DANGEROUS — requires explicit --purge-env) ────────────
if $PURGE_ENV; then
  warn "=============================================="
  warn "  --purge-env will delete ~/.hermes/.env"
  warn "  This file may contain secrets and credentials."
  warn "  Make sure you have backups."
  warn "=============================================="
  read -rp "Type 'yes' to confirm deletion of ~/.hermes/.env: " confirm
  if [ "$confirm" = "yes" ]; then
    rm -f "$HERMES_HOME/.env" "$HERMES_HOME/.env.generated"
    ok "Env files removed"
  else
    log "Env files preserved (confirmation not given)"
  fi
else
  log "$HERMES_HOME/.env preserved (use --purge-env to remove)"
fi

echo
log "Uninstall complete."
log "The following were preserved (if they existed):"
log "  - ~/.hermes/.env (secrets)"
log "  - ~/.hermes/.env.generated"
log "  - ~/.hermes/ops-kit/ (config)"
