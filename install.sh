#!/usr/bin/env bash
# Hermes Ops Kit — Install Script
# Installs Hermes Ops Kit as a Hermes plugin or standalone toolset.
#
# Usage:
#   curl -fsSL <url>/install.sh | bash
#   HERMES_OPS_KIT_VERSION=v0.1.0 bash install.sh

set -euo pipefail

PLUGIN_NAME="hermes-ops-kit"
REPO_URL="${HERMES_OPS_KIT_REPO:-${HERMES_AI_BRIDGE_REPO:-https://github.com/your-org/hermes-ops-kit.git}}"
VERSION="${HERMES_OPS_KIT_VERSION:-${HERMES_AI_BRIDGE_VERSION:-main}}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"
CONFIG_DIR="$HERMES_HOME/ops-kit"

# ── Colour helpers ──────────────────────────────────────────────────
log()  { printf '\033[1;34m[ops-kit]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m  ✅ %s\033[0m\n' "$*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1 — install it first"
}

# ── Prerequisites ───────────────────────────────────────────────────
log "Checking prerequisites"
require_cmd git
require_cmd python3

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
log "Python $PYVER detected"

# ── Hermes check (optional) ─────────────────────────────────────────
HERMES_OK=false
if command -v hermes >/dev/null 2>&1; then
  HERMES_OK=true
  log "Hermes detected — will install as plugin"
else
  warn "Hermes not found in PATH — installing as standalone toolset only"
  warn "For plugin mode, install Hermes first: https://github.com/NousResearch/hermes-agent"
fi

# ── Directory setup ─────────────────────────────────────────────────
mkdir -p "$HERMES_HOME/plugins" "$CONFIG_DIR"
chmod 700 "$HERMES_HOME" 2>/dev/null || true
ok "Directories created"

# ── Clone / update plugin ───────────────────────────────────────────
if [ -d "$PLUGIN_DIR/.git" ]; then
  log "Updating existing plugin at $PLUGIN_DIR"
  git -C "$PLUGIN_DIR" fetch --tags --quiet
  git -C "$PLUGIN_DIR" checkout "$VERSION"
  git -C "$PLUGIN_DIR" pull --ff-only 2>/dev/null || true
  ok "Plugin updated to $VERSION"
else
  log "Installing plugin into $PLUGIN_DIR"
  rm -rf "$PLUGIN_DIR"
  git clone --depth 1 --branch "$VERSION" "$REPO_URL" "$PLUGIN_DIR"
  ok "Plugin cloned ($VERSION)"
fi

# ── Python package ──────────────────────────────────────────────────
log "Installing Python package"
python3 -m pip install --user -e "$PLUGIN_DIR" 2>/dev/null || \
  python3 -m pip install --user "$PLUGIN_DIR" 2>/dev/null || \
  warn "pip install failed — installing wrapper scripts instead"
ok "Python package installed"

# ── CLI wrappers (always available, even without pip) ──────────────
log "Installing CLI commands"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

# Explicit mapping: CLI name → Python script
for entry in \
  "hermes-ops-kit:bridge.py" \
  "hermes-usage:usage_metrics_v2.py" \
  "hermes-key-rotate:hermes_key_rotate.py" \
  "hermes-assistant-manager:hermes_assistant_manager.py" \
  "hermes-route-manager:hermes_route_manager.py" \
  "hermes-export:hermes_export.py" \
  "hermes-skill-factory:hermes_skill_factory.py"; do

  cli_name="${entry%%:*}"
  script="${entry##*:}"
  cat > "$BIN_DIR/$cli_name" << WRAPPER
#!/bin/bash
cd "$PLUGIN_DIR" && exec python3 "$PLUGIN_DIR/$script" "\$@"
WRAPPER
  chmod +x "$BIN_DIR/$cli_name"
done

# Ensure ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
  if [ -f "$HOME/.bashrc" ]; then
    grep -q "$BIN_DIR" "$HOME/.bashrc" || echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$HOME/.bashrc"
  fi
  export PATH="$BIN_DIR:$PATH"
fi
ok "CLI commands installed ($BIN_DIR)"

# ── Config templates ────────────────────────────────────────────────
log "Installing config templates"
for f in env_projection.yaml assistants.yaml; do
  if [ ! -f "$CONFIG_DIR/$f" ] && [ -f "$PLUGIN_DIR/config/$f" ]; then
    cp "$PLUGIN_DIR/config/$f" "$CONFIG_DIR/$f"
    ok "  $f → $CONFIG_DIR/$f"
  else
    log "  $f already exists, skipped"
  fi
done

# ── Env file ────────────────────────────────────────────────────────
if [ ! -f "$HERMES_HOME/.env" ]; then
  log "Creating $HERMES_HOME/.env"
  touch "$HERMES_HOME/.env"
fi
chmod 600 "$HERMES_HOME/.env" 2>/dev/null || true
ok "~/.hermes/.env permissions set"

# ── Plugin enable ───────────────────────────────────────────────────
if $HERMES_OK; then
  hermes plugins enable "$PLUGIN_NAME" 2>/dev/null || \
    warn "Could not enable plugin automatically. Run: hermes plugins enable $PLUGIN_NAME"
  ok "Plugin enabled"
fi

# ── Doctor ──────────────────────────────────────────────────────────
log "Running doctor"
if command -v hermes-ops-kit >/dev/null 2>&1; then
  hermes-ops-kit doctor 2>/dev/null || true
elif [ -x "$PLUGIN_DIR/hermes_key_rotate.py" ]; then
  python3 "$PLUGIN_DIR/hermes_key_rotate.py" --doctor-secrets 2>/dev/null || true
fi

# ── Done ────────────────────────────────────────────────────────────
echo
log "Installation complete"
echo
log "Next steps:"
log "  1. Edit ~/.hermes/.env with Vaultwarden bootstrap:"
log "       HERMES_SECRET_BACKEND=vaultwarden"
log "       VAULTWARDEN_SERVER_URL=<vaultwarden-url>"
log "       VAULTWARDEN_USER=..."
log "       VAULTWARDEN_PASSWORD=..."
log "  2. Run: hermes-ops-kit doctor"
log "  3. Run: hermes-usage --compact"
log "  4. Run: hermes-ops-kit assistants ping <assistant-id>"
if $HERMES_OK; then
  log "  5. Restart Hermes"
fi
