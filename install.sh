#!/usr/bin/env bash
# Hermes Ops Kit — Install Script
# Installs Hermes Ops Kit as a Hermes plugin or standalone toolset.
#
# Usage:
#   curl -fsSL <url>/install.sh | bash
#   HERMES_OPS_KIT_VERSION=v0.1.0 bash install.sh
#
# Supported: Linux, macOS, and Windows through WSL.

set -euo pipefail

PLUGIN_NAME="hermes-ops-kit"
REPO_URL="${HERMES_OPS_KIT_REPO:-${HERMES_AI_BRIDGE_REPO:-https://github.com/redoracle/hermes-ops-kit.git}}"
VERSION="${HERMES_OPS_KIT_VERSION:-${HERMES_AI_BRIDGE_VERSION:-main}}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"
CONFIG_DIR="$HERMES_HOME/ops-kit"
BIN_DIR="$HOME/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$PLUGIN_NAME"
SCANNER_ROOT="$DATA_DIR/scanners"
TRUST_REPO="${HERMES_OPS_KIT_TRUST_REPO:-false}"
GITLEAKS_VERSION="${HERMES_OPS_KIT_GITLEAKS_VERSION:-v8.30.1}"
if [ -z "${HERMES_OPS_KIT_REPO:-}" ] && [ -z "${HERMES_AI_BRIDGE_REPO:-}" ]; then
  TRUST_REPO=true
fi

# ── Colour helpers ──────────────────────────────────────────────────
log()  { printf '\033[1;34m[ops-kit]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m  ✅ %s\033[0m\n' "$*"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1 — install it first"
}

python_is_runtime() {
  "$PYTHON_BIN" -c 'import pathlib, sys; raise SystemExit(not (sys.prefix != sys.base_prefix or pathlib.Path(sys.prefix, "conda-meta").is_dir()))'
}

pip_install_package() {
  if python_is_runtime; then
    "$PYTHON_BIN" -m pip install --disable-pip-version-check "$@"
    return $?
  fi

  if "$PYTHON_BIN" -m pip install --disable-pip-version-check --user "$@"; then
    return 0
  fi

  # Debian/Ubuntu, Homebrew, and other PEP 668 environments reject even
  # user-site installs unless this explicit override is present. Combining
  # it with --user keeps packages out of the managed system site-packages.
  if "$PYTHON_BIN" -m pip install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
    "$PYTHON_BIN" -m pip install \
      --disable-pip-version-check \
      --user \
      --break-system-packages \
      "$@"
    return $?
  fi

  return 1
}

create_scanner_venv() {
  local command_name="$1"
  local scanner_venv="$SCANNER_ROOT/$command_name"

  if [ -x "$scanner_venv/bin/python" ]; then
    return 0
  fi

  log "Creating isolated $command_name environment at $scanner_venv"
  mkdir -p "$SCANNER_ROOT"
  if ! "$PYTHON_BIN" -m venv "$scanner_venv"; then
    warn "Could not create the isolated $command_name environment."
    warn "On Debian/Ubuntu, install it with: sudo apt-get install python3-venv"
    return 1
  fi
}

reset_scanner_venv() {
  local command_name="$1"

  rm -rf "${SCANNER_ROOT:?}/$command_name"
  create_scanner_venv "$command_name"
}

scanner_works() {
  local command_name="$1"

  "$SCANNER_ROOT/$command_name/bin/$command_name" --version >/dev/null 2>&1
}

external_scanner_works() {
  local command_name="$1"
  local executable

  executable="$(command -v "$command_name" 2>/dev/null)" &&
    "$executable" --version >/dev/null 2>&1
}

install_scanner() {
  local command_name="$1"
  local scanner_bin="$SCANNER_ROOT/$command_name/bin"
  shift

  create_scanner_venv "$command_name" &&
    "$scanner_bin/python" -m pip install --disable-pip-version-check "$@" &&
    "$scanner_bin/$command_name" --version >/dev/null 2>&1
}

publish_scanner() {
  local command_name="$1"
  local launcher="$BIN_DIR/$command_name"
  local target="$SCANNER_ROOT/$command_name/bin/$command_name"

  mkdir -p "$BIN_DIR"
  if [ -e "$launcher" ] || [ -L "$launcher" ]; then
    if [ -L "$launcher" ] && [ "$(readlink "$launcher")" = "$target" ]; then
      return 0
    fi
    warn "Not replacing existing executable: $launcher"
    warn "The isolated $command_name binary is available at: $target"
    return 1
  fi

  ln -s "$target" "$launcher"
}

migrate_legacy_semgrep() {
  if ! "$PYTHON_BIN" - <<'PY'
from importlib import metadata

try:
    requirements = metadata.requires("semgrep") or []
except metadata.PackageNotFoundError:
    raise SystemExit(1)

normalized = [requirement.lower().replace(" ", "") for requirement in requirements]
conflicts = any(
    requirement.startswith("ruamel.yaml") and "<0.18" in requirement
    for requirement in normalized
)
raise SystemExit(not conflicts)
PY
  then
    return 0
  fi

  log "Migrating legacy Semgrep out of the active Python environment"
  "$PYTHON_BIN" -m pip uninstall --disable-pip-version-check -y semgrep ||
    fail "Could not remove the legacy Semgrep package."

  # Older Semgrep releases pin boltons~=21.0, which breaks current Conda.
  # Restore Conda's requirement after removing the legacy scanner package.
  if [ -d "$("$PYTHON_BIN" -c 'import sys; print(sys.prefix)')/conda-meta" ]; then
    pip_install_package "boltons>=23" ||
      fail "Could not restore Conda's boltons dependency after scanner migration."
  fi
}

headroom_proxy_ready() {
  # True only when the `headroom proxy` runtime deps (fastapi/uvicorn) are
  # importable by the interpreter behind the launcher.  A bare `headroom-ai`
  # install (without the [proxy] extra) ships the binary but crashes on
  # `proxy` with "No module named 'fastapi'".
  local launcher pybin
  launcher="$(command -v headroom)" || return 1
  pybin="$(head -1 "$launcher" 2>/dev/null | sed 's/^#!//' | awk '{print $1}')"
  [ -x "$pybin" ] || return 1
  "$pybin" -c 'import fastapi, uvicorn' >/dev/null 2>&1
}

install_headroom() {
  # Headroom proxy (PyPI: headroom-ai) — optional optimization layer for
  # `hermes-ops-kit headroom`.  Non-fatal: ops-kit works without it and
  # the reconciler keeps routes direct when the binary is missing.
  # The [proxy] extra is required: without it `headroom proxy` cannot bind.
  # LiteLLM is intentionally NOT installed: upstream.mode=provider needs
  # no aggregator; bring your own LiteLLM for upstream.mode=litellm.
  if command -v headroom >/dev/null 2>&1 && headroom_proxy_ready; then
    ok "Using existing Headroom installation ($(headroom --version 2>/dev/null || echo '?'))"
    return 0
  fi

  if command -v pipx >/dev/null 2>&1; then
    if pipx install --force "headroom-ai[proxy]" >/dev/null 2>&1; then
      ok "Headroom installed via pipx and verified"
      return 0
    fi
  fi
  if pip_install_package "headroom-ai[proxy]" && headroom_proxy_ready; then
    ok "Headroom installed via pip and verified"
    return 0
  fi

  warn "Headroom not installed: the 'hermes-ops-kit headroom' overlay stays inactive."
  warn "Install later with: pipx install 'headroom-ai[proxy]'"
  return 0
}

install_gitleaks() {
  if command -v gitleaks >/dev/null 2>&1; then
    return 0
  fi

  if command -v brew >/dev/null 2>&1; then
    brew install gitleaks
  elif command -v go >/dev/null 2>&1; then
    GOBIN="$BIN_DIR" go install "github.com/zricethezav/gitleaks/v8@$GITLEAKS_VERSION"
  else
    warn "Gitleaks not installed: Homebrew/Linuxbrew or Go is required."
    warn "Install Go, then run: GOBIN=\"$BIN_DIR\" go install github.com/zricethezav/gitleaks/v8@$GITLEAKS_VERSION"
    return 0
  fi

  command -v gitleaks >/dev/null 2>&1 || [ -x "$BIN_DIR/gitleaks" ] || \
    fail "Gitleaks installation verification failed"
  ok "Gitleaks installed and verified"
}

# ── Prerequisites ───────────────────────────────────────────────────
log "Checking prerequisites"
require_cmd git
require_cmd python3
require_cmd grep
PYTHON_BIN="$(command -v python3)"

case "$(uname -s 2>/dev/null || printf unknown)" in
  Linux | Darwin)
    ;;
  *)
    fail "Unsupported platform. Use Linux, macOS, or Windows through WSL."
    ;;
esac

python3 -m pip --version >/dev/null 2>&1 || \
  fail "Python pip is required. Install python3-pip (Linux) or a Python distribution that includes pip."

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || \
  fail "Python 3.11 or newer is required (found $PYVER)"
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
if PYTHONPATH="$PLUGIN_DIR" "$PYTHON_BIN" - "$PLUGIN_DIR" "$PLUGIN_NAME" "$TRUST_REPO" <<'PY'
import sys
from security.plugin_scanner.enforce import get_enforcement_decisions
from security.plugin_scanner.scanner import scan_plugin

plugin_path, plugin_name, trust_repo = sys.argv[1:4]
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
if result.errors:
    print(f"[error] Pre-install scan incomplete: {result.errors}", file=sys.stderr)
    raise SystemExit(3)
if trust_repo.lower() in {"1", "true", "yes"}:
    if decisions["blocked"] or decisions["disable"]:
        print(
            "[warn] Trusted repository contains expected privileged capabilities; "
            "continuing after complete self-scan.",
            file=sys.stderr,
        )
    raise SystemExit(0)
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
  migrate_legacy_semgrep

  log "Installing Python package and quality tooling"
  if ! pip_install_package -e "${PLUGIN_DIR}[dev]" && \
    ! pip_install_package "${PLUGIN_DIR}[dev]"; then
    fail "Python package installation failed. Check pip output above for the failing dependency."
  fi

  "$PYTHON_BIN" -c 'from PIL import Image' || fail "Pillow verification failed after installation"
  "$PYTHON_BIN" -m ruff --version >/dev/null || fail "ruff verification failed after installation"
  ok "Python package, Pillow, and ruff installed and verified"

  log "Installing external security scanners"

  # Older Semgrep releases conflict with ops-kit's ruamel.yaml requirement.
  # Give every optional scanner its own environment so neither the application
  # nor another scanner can be modified by its dependency resolver.
  if scanner_works bandit; then
    if publish_scanner bandit; then
      ok "Bandit installed, verified, and published (isolated)"
    fi
  elif external_scanner_works bandit; then
    ok "Using existing Bandit installation"
  elif install_scanner bandit "bandit"; then
    if publish_scanner bandit; then
      ok "Bandit installed, verified, and published (isolated)"
    fi
  else
    warn "Bandit installation failed; the plugin scanner will use built-in detectors."
  fi

  if scanner_works semgrep; then
    if publish_scanner semgrep; then
      ok "Semgrep installed, verified, and published (isolated)"
    fi
  elif external_scanner_works semgrep; then
    ok "Using existing Semgrep installation"
  elif install_scanner semgrep "semgrep"; then
    if publish_scanner semgrep; then
      ok "Semgrep installed, verified, and published (isolated)"
    fi
  else
    warn "Latest Semgrep is unavailable; trying Semgrep 1.96.0 for older Linux systems."
    if reset_scanner_venv semgrep &&
      install_scanner semgrep "semgrep==1.96.0" "setuptools>=65,<70"; then
      if publish_scanner semgrep; then
        ok "Semgrep 1.96.0 installed, verified, and published (isolated)"
      fi
    else
      warn "Semgrep installation failed; the plugin scanner will use built-in detectors."
      warn "See docs/external-security-tools.md for platform-specific options."
    fi
  fi

  mkdir -p "$BIN_DIR"
  install_gitleaks
  install_headroom
  export PATH="$BIN_DIR:$PATH"
else
  warn "Python package installation skipped until explicit approval"
fi

# ── CLI wrappers (always available, even without pip) ──────────────
log "Installing CLI commands"
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
#!/usr/bin/env bash
export PATH="$BIN_DIR:\$PATH"
cd "$PLUGIN_DIR" && exec "$PYTHON_BIN" "$PLUGIN_DIR/$script" "\$@"
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
"$PYTHON_BIN" "$PLUGIN_DIR/security/plugin_scanner/bootstrap.py" --headless --force
ok "Security bootstrap completed"

# ── Plugin enable ───────────────────────────────────────────────────
if $HERMES_OK && $INSTALL_ALLOWED; then
  if hermes plugins enable "$PLUGIN_NAME" 2>/dev/null; then
    ok "Plugin enabled"
  else
    warn "Could not enable plugin automatically. Run: hermes plugins enable $PLUGIN_NAME"
  fi
elif $HERMES_OK; then
  warn "Plugin remains disabled until explicit approval and a successful preflight"
fi

# ── Doctor ──────────────────────────────────────────────────────────
log "Running doctor"
if command -v hermes-ops-kit >/dev/null 2>&1; then
  hermes-ops-kit doctor 2>/dev/null || true
elif [ -x "$PLUGIN_DIR/hermes_key_rotate.py" ]; then
  "$PYTHON_BIN" "$PLUGIN_DIR/hermes_key_rotate.py" --doctor-secrets 2>/dev/null || true
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
log "  4. External security tools were installed and verified when supported."
log "       → See docs/external-security-tools.md for platform-specific details"
log "  5. Run: hermes-ops-kit plugin scan --profile manual"
log "  6. A cached, report-only security scan runs at each Hermes session start."
log "     IMPORTANT: a normal Hermes gateway restart does NOT run preflight."
log "     Safe restart: hermes-ops-kit preflight && hermes gateway restart"
log "     To preview enforcement changes: hermes-ops-kit preflight --dry-run"
log "  7. Run: hermes-ops-kit assistants ping <assistant-id>"
if $HERMES_OK; then
  log "  8. Restart safely: hermes-ops-kit preflight && hermes gateway restart"
fi
