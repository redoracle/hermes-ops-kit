#!/usr/bin/env bash
# Hermes Ops Kit — Windows Subsystem for Linux bootstrap.
# Installs Linux prerequisites, then delegates to install.sh.

set -euo pipefail

fail() { printf '[error] %s\n' "$*" >&2; exit 1; }
log()  { printf '[ops-kit-wsl] %s\n' "$*"; }

grep -qi microsoft /proc/version 2>/dev/null || \
  fail "This script must run inside Windows Subsystem for Linux."
command -v apt-get >/dev/null 2>&1 || \
  fail "This WSL installer currently supports Ubuntu/Debian distributions with apt-get."

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1; then
  SUDO=(sudo)
else
  fail "sudo is required to install WSL prerequisites."
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/install.sh" ] || \
  fail "install.sh must be next to install-wsl.sh. Clone the repository, then rerun this script."

log "Installing WSL prerequisites"
"${SUDO[@]}" apt-get update
"${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  bash \
  ca-certificates \
  git \
  golang-go \
  grep \
  python3 \
  python3-pip \
  python3-venv

log "Delegating to install.sh"
exec bash "$SCRIPT_DIR/install.sh" "$@"
