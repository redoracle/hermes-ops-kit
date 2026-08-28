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


def hermes_config() -> str:
    """Dynamic path to ~/.hermes/config.yaml.

    Prefer this over the frozen HERMES_CONFIG constant inside the kit:
    it re-derives from HERMES_HOME at call time, so tests (and embedders)
    that monkeypatch ``ops_config_io.HERMES_HOME`` fully control the path.
    """
    return os.path.join(HERMES_HOME, "config.yaml")


def expand_home(path: str) -> str:
    """expanduser() that maps a leading ~/.hermes to the effective HERMES_HOME.

    Use for default config values stored as ~/.hermes-prefixed strings
    (headroom settings, comfyui workflow paths) so the HERMES_HOME override
    stays honored after the string is expanded.
    """
    p = os.path.expanduser(str(path))
    real = os.path.expanduser("~/.hermes")
    if p == real:
        return HERMES_HOME
    if p.startswith(real + "/"):
        return os.path.join(HERMES_HOME, p[len(real) + 1:])
    return p


def _ruamel():
    try:
        from ruamel.yaml import YAML  # pyright: ignore[reportMissingImports]

        yaml = YAML()
        yaml.preserve_quotes = True
        return yaml
    except ImportError:
        return None


class ConfigError(Exception):
    """Raised by load_yaml_strict for missing/unparseable/non-mapping config."""


def load_yaml(path: str) -> dict:
    """Load a YAML (or JSON) file; missing/unparseable/non-mapping → {}.

    With ruamel.yaml installed the returned mapping carries comment
    metadata, so a later ``save_yaml`` round-trips the file faithfully.
    A root that is not a mapping (list/scalar) is treated as unparseable
    so callers can rely on ``.get()``.
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

            data = ruamel.load(io.StringIO(content))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    try:
        import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        try:
            data = _yaml.safe_load(content)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    except ImportError:
        pass
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def load_yaml_strict(path: str) -> dict:
    """Fail-closed variant: raises ConfigError instead of returning {}.

    For callers where a silently-empty config is worse than an error
    (e.g. security-adjacent diagnostics). The security-critical loaders
    in ``security/`` keep their own implementations deliberately.
    """
    if not os.path.exists(path):
        raise ConfigError(f"missing: {path}")
    data = load_yaml(path)
    if not data:
        raise ConfigError(f"empty or unparseable: {path}")
    return data


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


def bundled_path(name: str) -> str:
    """Path of a packaged default config (hermes_ops_kit/config/<name>)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", name)


def deployed_or_bundled(name: str, *, seed: bool = False) -> str:
    """Resolve an ops-kit config: deployed file wins, else the bundled default.

    With seed=True the bundled default is copied to the deployed location on
    first use so later edits land in ~/.hermes/ops-kit.  Single implementation
    of the "deployed else bundled" pattern (headroom, plugin_scanner,
    assistant_tasks, budget, routes, image_routes).
    """
    deployed = os.path.join(OPS_KIT_DIR, name)
    if os.path.exists(deployed):
        return deployed
    bundled = bundled_path(name)
    if seed and os.path.exists(bundled) and not os.path.exists(deployed):
        import tempfile

        os.makedirs(OPS_KIT_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=OPS_KIT_DIR, prefix=f".{name}.", text=True)
        os.close(fd)
        try:
            shutil.copyfile(bundled, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, deployed)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return deployed
    return bundled
