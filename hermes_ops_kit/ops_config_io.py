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
import sys
import time

# Deprecated import-time aliases, kept for the compatibility window: external code
# and tests read (and monkeypatch) these names. Inside the kit use home() and the
# helpers derived from it, which resolve at call time.
HERMES_HOME = os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
HERMES_CONFIG = os.path.join(HERMES_HOME, "config.yaml")
OPS_KIT_DIR = os.path.join(HERMES_HOME, "ops-kit")

_IMPORT_TIME_HOME = HERMES_HOME


def _native_home() -> str | None:
    """Active Hermes home from core, or None when core is not loaded in this process.

    Core resolves a context-local profile override first, then ``HERMES_HOME``,
    then the platform default, so routed profiles get their own home. Only an
    already-imported core is consulted: that is the embedded case, it costs a
    dict lookup, and a standalone process keeps the ``HERMES_HOME`` contract
    instead of paying for a failing import on every call.
    """
    module = sys.modules.get("hermes_constants")
    get_home = getattr(module, "get_hermes_home", None)
    return str(get_home()) if callable(get_home) else None


class ScopedEnvironmentUnavailable(RuntimeError):
    """The process runs inside Hermes but a required profile-scoping API is missing.

    Raised instead of falling back to the launch profile's environment, which
    could hand one profile's credentials to work done for another.
    """


def core_loaded() -> bool:
    """True when this process is Hermes core (embedded), not a standalone CLI."""
    return "hermes_constants" in sys.modules


def native(module: str, attr: str):
    """A callable/class from a core module; refuse if embedded and it is missing."""
    import importlib

    try:
        # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
        # (every caller passes a literal name of a Hermes core module)
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        raise ScopedEnvironmentUnavailable(
            f"Hermes core is loaded but {module}.{attr} is not available "
            f"({exc}); refusing to use the launch profile's environment. "
            "Run this command from a terminal, or update Hermes."
        ) from exc


def home() -> str:
    """The active Hermes home, resolved at call time.

    Order: (1) an explicit override, i.e. ``HERMES_HOME`` rebound on this module
    (the documented test/embedder contract); (2) core ``get_hermes_home()`` when
    importable; (3) the ``HERMES_HOME`` environment variable read now, then
    ``~/.hermes``.
    """
    if HERMES_HOME != _IMPORT_TIME_HOME:
        return HERMES_HOME
    native = _native_home()
    if native:
        return native
    return os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))


READ_ONLY_ENV = "HERMES_OPS_KIT_READ_ONLY"


def read_only() -> bool:
    """True in a process that must not change anything or start third-party code.

    Set only by the chat (`/ops-kit`) child. In this mode defaults are not seeded
    into the profile and MCP servers are not launched or contacted.
    """
    return os.environ.get(READ_ONLY_ENV) == "1"


def default_home() -> str:
    """The platform default Hermes root (``~/.hermes``), independent of the active profile.

    The single place that spells this path; profile homes live below or beside it.
    Used where the shared install (core source, install state) is needed rather than
    the active profile.
    """
    return os.path.expanduser("~/.hermes")


def config_path() -> str:
    """Path to the active profile's ``config.yaml``."""
    return os.path.join(home(), "config.yaml")


def hermes_config() -> str:
    """Dynamic path to ~/.hermes/config.yaml.

    Prefer this over the frozen HERMES_CONFIG constant inside the kit:
    it re-derives from HERMES_HOME at call time, so tests (and embedders)
    that monkeypatch ``ops_config_io.HERMES_HOME`` fully control the path.
    """
    return config_path()


def ops_kit_dir() -> str:
    """Dynamic path to ~/.hermes/ops-kit.

    Derives from HERMES_HOME at call time so tests and embedders that
    monkeypatch ``ops_config_io.HERMES_HOME`` redirect ops-kit directories.
    """
    return os.path.join(home(), "ops-kit")


def expand_home(path: str) -> str:
    """expanduser() that maps a leading ~/.hermes to the effective HERMES_HOME.

    Use for default config values stored as ~/.hermes-prefixed strings
    (headroom settings, comfyui workflow paths) so the HERMES_HOME override
    stays honored after the string is expanded.
    """
    p = os.path.expanduser(str(path))
    real = os.path.expanduser("~/.hermes")
    if p == real:
        return home()
    if p.startswith(real + "/"):
        return os.path.join(home(), p[len(real) + 1 :])
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
    try:
        with open(path) as source:
            parser = _ruamel()
            if parser is not None:
                data = parser.load(source)
            else:
                import yaml

                class UniqueLoader(yaml.SafeLoader):
                    pass

                def mapping(loader, node):
                    result = {}
                    for key_node, value_node in node.value:
                        key = loader.construct_object(key_node)
                        if key in result:
                            raise ValueError("duplicate mapping key")
                        result[key] = loader.construct_object(value_node)
                    return result

                UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
                data = yaml.load(source, Loader=UniqueLoader)  # noqa: S506 - SafeLoader subclass
    except Exception as exc:
        raise ConfigError(f"unparseable configuration: {path}") from exc
    if not isinstance(data, dict) or not data:
        raise ConfigError(f"empty or non-mapping configuration: {path}")
    return data


def save_yaml(path: str, data: dict) -> None:
    """Atomically write *data* to *path* (temp → chmod 600 → rename).

    A failed write never leaves the temp file behind.
    """
    import tempfile

    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)),
        prefix=f".{os.path.basename(path)}.",
        text=True,
    )
    os.close(fd)
    try:
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
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


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
    # Derive from HERMES_HOME at call time (not the import-frozen constant)
    # so the documented monkeypatch contract holds — a patched test must
    # never seed into the real ~/.hermes/ops-kit.
    deployed = os.path.join(ops_kit_dir(), name)
    if os.path.exists(deployed):
        return deployed
    bundled = bundled_path(name)
    if seed and not read_only() and os.path.exists(bundled) and not os.path.exists(deployed):
        import tempfile

        ops_dir = ops_kit_dir()
        os.makedirs(ops_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=ops_dir, prefix=f".{name}.", text=True)
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
