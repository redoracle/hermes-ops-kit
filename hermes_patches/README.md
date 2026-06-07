# Hermes Runtime Patches

These files mirror runtime changes applied outside this plugin package.

- `hermes-agent/tools/image_generation_tool.py` mirrors `~/.hermes/hermes-agent/tools/image_generation_tool.py`

Image gen provider registration (`ops-kit-router`) is now handled directly by
the ops-kit plugin's `register()` function in `__init__.py` — no separate plugin needed.

They are kept here so the subject-preserving background-edit path can be
reapplied when the Hermes agent runtime or image generation provider is updated.
