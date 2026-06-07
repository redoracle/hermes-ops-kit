"""Hermes Ops Kit — Image Generation Router Provider (thin plugin).

Thin plugin deployed to ``~/.hermes/plugins/image_gen/ops-kit-router/``.
Adds the hermes-ops-kit package directory to ``sys.path`` so absolute
imports like ``from security.redaction import redact`` resolve, then
imports and registers the ``OpsKitRouterProvider`` from ops-kit's
``image_routes/hermes_provider.py``.

This avoids the complex import chain in the main ops-kit plugin's
``register()`` that triggers ``tools.py`` → ``security.redaction``
before ``sys.path`` has been primed.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)


def _find_ops_kit_dir() -> str | None:
    """Locate the hermes-ops-kit plugin directory on disk."""
    candidates = [
        os.path.expanduser("~/.hermes/plugins/hermes-ops-kit"),
        os.path.expanduser("~/GIT/INFRA/tools/hermes-ops-kit"),
    ]
    for p in candidates:
        init = os.path.join(p, "__init__.py")
        if os.path.isfile(init):
            return os.path.abspath(p)
    return None


def register(ctx) -> None:
    """Wire ``OpsKitRouterProvider`` into the Hermes image_gen registry."""
    ops_kit_dir = _find_ops_kit_dir()
    if not ops_kit_dir:
        logger.warning(
            "ops-kit-router image_gen plugin: ops-kit not found on disk; skipping registration"
        )
        return

    # Prime sys.path so absolute imports inside ops-kit (e.g.
    # ``from security.redaction import redact``) resolve correctly.
    if ops_kit_dir not in sys.path:
        sys.path.insert(0, ops_kit_dir)

    try:
        from image_routes.hermes_provider import OpsKitRouterProvider
    except ImportError as exc:
        logger.warning(
            "ops-kit-router image_gen plugin: cannot import OpsKitRouterProvider: %s",
            exc,
        )
        return

    try:
        ctx.register_image_gen_provider(OpsKitRouterProvider())
    except Exception as exc:
        logger.warning("ops-kit-router image_gen plugin: registration failed: %s", exc)
        return

    logger.debug("ops-kit-router image_gen provider registered successfully")
