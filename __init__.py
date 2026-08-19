"""Hermes Ops Kit — plugin root for the Hermes directory loader.

Directory-plugin contract (Hermes Agent v0.20): ``plugin.yaml`` +
``__init__.py`` exposing ``register(ctx)`` at the plugin-directory root.
The implementation lives in the ``hermes_ops_kit`` package; this module
only re-exports ``register`` so the loader finds it.  Entry-point
(pip) installs target ``hermes_ops_kit`` directly — see pyproject.toml.
"""

# Directory-plugin contract: under the Hermes loader (and any normal package
# import) __package__ is set and the relative import resolves. When this file
# is imported parentless (e.g. pytest's prepend-mode walk of the tests/
# package chain), there is no parent package — fail soft and leave register
# undefined rather than breaking the import. That mode never calls register().
try:
    from .hermes_ops_kit import register  # noqa: F401
except ImportError:  # pragma: no cover - parentless import (pytest package walk)
    pass

__all__ = ["register"]
