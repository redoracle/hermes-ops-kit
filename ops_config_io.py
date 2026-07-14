"""Hermes Ops Kit — Shared config I/O helpers.

Single home for the YAML load/atomic-save/backup logic used by every
module that patches ``~/.hermes/config.yaml`` or an ops-kit config file
(`hermes_route_manager`, `headroom.reconcile`, …).

Writes are atomic: temp file → chmod 600 → os.replace.  Like
``plugin_scanner/enforce.py``, ruamel.yaml is preferred when available
(round-trip load/dump preserves the user's comments and formatting in
``config.yaml``); PyYAML is the fallback, JSON the last resort.
"""

from __future__ import annotations

import json
import os
import shutil
import time

HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
HERMES_CONFIG = os.path.join(HERMES_HOME, "config.yaml")
OPS_KIT_DIR = os.path.join(HERMES_HOME, "ops-kit")


def _ruamel():
    try:
        from ruamel.yaml import YAML  # pyright: ignore[reportMissingImports]

        yaml = YAML()
        yaml.preserve_quotes = True
        return yaml
    except ImportError:
        return None


def load_yaml(path: str) -> dict:
    """Load a YAML (or JSON) file; missing/unparseable → {}.

    With ruamel.yaml installed the returned mapping carries comment
    metadata, so a later ``save_yaml`` round-trips the file faithfully.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return {}
    ruamel = _ruamel()
    if ruamel is not None:
        try:
            import io

            return ruamel.load(io.StringIO(content)) or {}
        except Exception:
            pass
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        try:
            return _yaml.safe_load(content) or {}
        except Exception:
            pass
    except ImportError:
        pass
    try:
        return json.loads(content) or {}
    except Exception:
        return {}


def save_yaml(path: str, data: dict) -> None:
    """Atomically write *data* to *path* (temp → chmod 600 → rename)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    ruamel = _ruamel()
    if ruamel is not None:
        try:
            with open(tmp, "w") as f:
                ruamel.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
            return
        except Exception:
            pass
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        with open(tmp, "w") as f:
            _yaml.safe_dump(data, f, indent=2, sort_keys=False)
    except ImportError:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def backup_file(path: str, suffix: str = "") -> str | None:
    """Copy *path* to a timestamped sibling backup; returns backup path.

    Returns None when the source does not exist.  Backups never
    overwrite each other (timestamped name).
    """
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.bak.{stamp}{suffix}"
    shutil.copy2(path, backup)
    return backup
