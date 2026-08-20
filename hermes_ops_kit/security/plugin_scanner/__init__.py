"""Hermes Ops Kit — Plugin Security Scanner.

Defense-in-depth security scanning for Hermes plugins.
Organized as composable scan categories with SHA-256 caching and
disable-by-default approval workflow.

MVP (Phase 1): secrets + policy categories, SHA cache, approval policy.
Future: code, dependencies, behavior, reputation categories.
"""

from __future__ import annotations

__version__ = "0.5.0"
