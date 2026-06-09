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
  git -C "$PLUGIN_DIR" checkout --quiet "$VERSION"
  if git -C "$PLUGIN_DIR" symbolic-ref -q HEAD >/dev/null; then
    git -C "$PLUGIN_DIR" pull --ff-only --quiet
  fi
  ok "Plugin updated to $VERSION"
else
  log "Installing plugin into $PLUGIN_DIR"
  if [ -e "$PLUGIN_DIR" ]; then
    fail "$PLUGIN_DIR already exists and is not a Git checkout. Back it up or remove it manually, then rerun the installer."
  fi
  git clone --depth 1 --branch "$VERSION" "$REPO_URL" "$PLUGIN_DIR"
  ok "Plugin cloned ($VERSION)"
fi

# ── Pre-install security gate ───────────────────────────────────────
# Run the static scanner before pip installation or Hermes enablement.
INSTALL_ALLOWED=false
log "Running pre-install static security gate"
if PYTHONPATH="$PLUGIN_DIR" python3 - "$PLUGIN_DIR" "$PLUGIN_NAME" <<'PY'
import sys
from security.plugin_scanner.enforce import get_enforcement_decisions
from security.plugin_scanner.scanner import scan_plugin

plugin_path, plugin_name = sys.argv[1:3]
result = scan_plugin(
    plugin_name,
    plugin_path,
    profile="install",
    force=True,
    use_cache=False,
)
decisions = get_enforcement_decisions([result])
print(
    f"[ops-kit] Pre-install scan: risk={result.risk_level.value}, "
    f"findings={len(result.findings)}, errors={len(result.errors)}"
)
if decisions["blocked"]:
    print(f"[error] Blocked by security policy: {decisions['details'][plugin_name]}", file=sys.stderr)
    raise SystemExit(3)
if decisions["disable"]:
    print(f"[warn] Installation deferred: {decisions['details'][plugin_name]}", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0)
PY
then
  INSTALL_ALLOWED=true
  ok "Pre-install security gate passed"
else
  scan_rc=$?
  if [ "$scan_rc" -eq 2 ]; then
    warn "MEDIUM/HIGH risk requires explicit approval. Package install and Hermes enablement skipped."
    warn "Review the report, then run: hermes-ops-kit plugin approve $PLUGIN_NAME"
  else
    fail "Pre-install security gate failed or found CRITICAL risk (exit $scan_rc)"
  fi
fi

# ── Python package ──────────────────────────────────────────────────
if $INSTALL_ALLOWED; then
  log "Installing Python package"
  if python3 -m pip install --user -e "$PLUGIN_DIR" 2>/dev/null || \
    python3 -m pip install --user "$PLUGIN_DIR" 2>/dev/null; then
    ok "Python package installed"
  else
    warn "pip install failed — installing wrapper scripts instead"
  fi
else
  warn "Python package installation skipped until explicit approval"
fi

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

ensure_path_entry() {
  rc_file="$1"
  [ -f "$rc_file" ] || touch "$rc_file"
  grep -q "$BIN_DIR" "$rc_file" || echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$rc_file"
}

# Ensure ~/.local/bin is in PATH for common shells
case ":$PATH:" in
*":$BIN_DIR:"*)
  ;;
*)
  case "${SHELL:-}" in
    */zsh)
      ensure_path_entry "$HOME/.zshrc"
      ensure_path_entry "$HOME/.profile"
      ;;
    */fish)
      warn "fish shell detected — add $BIN_DIR to PATH manually in config.fish"
      ;;
    *)
      ensure_path_entry "$HOME/.bashrc"
      ensure_path_entry "$HOME/.profile"
      ;;
  esac
  export PATH="$BIN_DIR:$PATH"
  warn "Added $BIN_DIR to PATH for this session; restart your shell to make it permanent"
  ;;
esac
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
ok "$HERMES_HOME/.env permissions set"

# ── Security bootstrap ──────────────────────────────────────────────
log "Running first-install security bootstrap"
python3 "$PLUGIN_DIR/security/plugin_scanner/bootstrap.py" --headless --force
ok "Security bootstrap completed"

# ── Plugin enable ───────────────────────────────────────────────────
if $HERMES_OK && $INSTALL_ALLOWED; then
  hermes plugins enable "$PLUGIN_NAME" 2>/dev/null || \
    warn "Could not enable plugin automatically. Run: hermes plugins enable $PLUGIN_NAME"
  ok "Plugin enabled"
elif $HERMES_OK; then
  warn "Plugin remains disabled until explicit approval and a successful preflight"
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
log "  4. (Optional) Install external security tools for enhanced scanning:"
log "       → See docs/external-security-tools.md for platform-specific instructions"
log "       brew install gitleaks          # 150+ secret detectors"
log "       pip install semgrep             # 2,500+ SAST rules"
log "       pip install bandit              # Python security rules"
log "  5. Run: hermes-ops-kit plugin scan --profile manual"
log "  6. (Optional) Enable pre-boot security enforcement:"
log "       hermes-ops-kit preflight --dry-run   # preview without changes"
log "       hermes-ops-kit preflight              # enforce + sync config"
log "       Preflight uses fast built-in detectors; deep external tools run in manual scans."
log "  7. Run: hermes-ops-kit assistants ping <assistant-id>"
if $HERMES_OK; then
  log "  8. Restart Hermes"
fi
