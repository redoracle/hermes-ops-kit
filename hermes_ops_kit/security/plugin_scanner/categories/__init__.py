"""Hermes Ops Kit — Plugin Scanner: Categories Package.

Each category module exposes a run() function that takes a plugin path
and returns a list of Finding objects. Categories are independently
composable and can be enabled/disabled per scan profile.

MVP categories: secrets, policy.
Future categories: code, dependencies, behavior, reputation.
"""

from __future__ import annotations
