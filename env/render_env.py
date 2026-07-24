"""
Hermes Ops Kit — Environment Renderer

Reads provider secrets from Vaultwarden via the SecretBackend interface
and renders ~/.hermes/.env.generated.

Uses the mapping defined in config/env_projection.yaml (spec section 14).
Never logs raw rendered values.

Safety gates:
  1. deny_render list — hard-blocks admin refs from ever reaching .env.generated
  2. Classification check — skips secrets with renderable_to_env=False
  3. Atomic write — temp → chmod 600 → fsync → rename
"""

from __future__ import annotations

import os
import re


# ── Denylist loader ─────────────────────────────────────────────────────


def _load_deny_render() -> set[str]:
    """Load the deny_render list from config/env_projection.yaml.

    Returns a set of secret ref paths that must never appear in .env.generated.

    NOTE: This intentionally uses manual line-by-line parsing instead of a
    YAML library (e.g. PyYAML).  Rationale:
      - render_env runs during key rotation startup; avoiding a YAML import
        keeps startup fast and avoids a dependency edge.
      - The deny_render section is intentionally flat and simple: one ref
        per line, no nesting, no multiline values, no anchors/aliases.
      - If the deny_render format ever grows to need nested structures or
        complex YAML features, replace this parser with a proper YAML load.
    """
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "env_projection.yaml",
    )
    deny: set[str] = set()
    try:
        with open(config_path) as f:
            in_deny = False
            for line in f:
                stripped = line.strip()
                if stripped.startswith("deny_render:"):
                    in_deny = True
                    continue
                if in_deny:
                    if stripped.startswith("env_projection:"):
                        break
                    item = stripped.lstrip("- ").strip().strip('"').strip("'")
                    if item and not item.startswith("#"):
                        deny.add(item)
    except (FileNotFoundError, OSError):
        pass
    return deny


DENY_RENDER: set[str] = _load_deny_render()


# ── env_projection loader ─────────────────────────────────────────────
# Loads the env_projection: map from config/env_projection.yaml so YAML edits
# take effect. Previously only deny_render was parsed and the projection fell
# back to DEFAULT_PROJECTION, silently ignoring env_projection.yaml additions
# (e.g. FIREWORKS_API_KEY / DEEPINFRA_API_KEY were never rendered).
_KEY_VAL_RE = re.compile(r"^\s{2,}([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\S+)\s*$")


def _load_env_projection() -> dict[str, str]:
    """Load the env_projection mapping from config/env_projection.yaml (manual parse)."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "env_projection.yaml",
    )
    proj: dict[str, str] = {}
    try:
        with open(config_path) as f:
            in_proj = False
            for line in f:
                s = line.rstrip("\n")
                stripped = s.strip()
                if stripped.startswith("env_projection:"):
                    in_proj = True
                    continue
                if not in_proj:
                    continue
                # A new top-level key (col 0, non-comment) ends the section.
                if s and not s[0].isspace() and not stripped.startswith("#"):
                    break
                if not stripped or stripped.startswith("#"):
                    continue
                m = _KEY_VAL_RE.match(s)
                if m:
                    key = m.group(1)
                    val = m.group(2).strip('"').strip("'")
                    if key and val:
                        proj[key] = val
    except (FileNotFoundError, OSError):
        pass
    return proj


ENV_PROJECTION: dict[str, str] = _load_env_projection()

# Also block by ref keyword — any ref containing these path segments is admin
ADMIN_PATH_SEGMENTS: frozenset[str] = frozenset(
    {"admin_key", "admin_secret", "admin_token", "service_account_json"}
)


def _is_admin_ref(secret_ref: str) -> bool:
    """Check whether a secret ref is classified as admin based on its path."""
    parts = secret_ref.split("/")
    for segment in parts:
        if segment in ADMIN_PATH_SEGMENTS:
            return True
    return False


from security.fingerprints import secret_fingerprint  # noqa: E402  # pyright: ignore[reportMissingImports]
from security.secret_backend import SecretBackend  # noqa: E402  # pyright: ignore[reportMissingImports]


# Default projection mapping (env var → internal secret ref).
# Override by loading config/env_projection.yaml at runtime.
DEFAULT_PROJECTION: dict[str, str] = {
    # OpenAI
    "OPENAI_API_KEY": "hermes/openai/api_key",
    "OPENAI_PROJECT_ID": "hermes/openai/project_id",
    # Anthropic
    "ANTHROPIC_API_KEY": "hermes/anthropic/api_key",
    # Google / Gemini
    "GEMINI_API_KEY": "hermes/google/gemini_api_key",
    "GOOGLE_APPLICATION_CREDENTIALS_JSON": "hermes/google/application_credentials_json",
    # DeepSeek
    "DEEPSEEK_API_KEY": "hermes/deepseek/api_key",
    "DEEPSEEK_BASE_URL": "hermes/deepseek/base_url",
    "DEEPSEEK_ANTHROPIC_BASE_URL": "hermes/deepseek/anthropic_base_url",
    "DEEPSEEK_DEFAULT_MODEL": "hermes/deepseek/default_model",
    "DEEPSEEK_REASONING_MODEL": "hermes/deepseek/reasoning_model",
    # GitHub
    "GITHUB_APP_ID": "hermes/github/app_id",
    "GITHUB_INSTALLATION_ID": "hermes/github/installation_id",
    "GITHUB_TOKEN": "hermes/github/token",
    "GH_TOKEN": "hermes/github/token",
    # NVIDIA NIM
    "NVIDIA_API_KEY": "hermes/nvidia/api_key",
    "NVIDIA_BASE_URL": "hermes/nvidia/base_url",
}


def render_env_content(
    backend: SecretBackend,
    projection: dict[str, str] | None = None,
) -> str:
    """Read all secrets from *backend* and render .env.generated content.

    Safety gates applied in order:
      1. Hard deny_render list from env_projection.yaml
      2. Path-segment admin classification
      3. Vaultwarden metadata renderable_to_env flag

    Returns the rendered content as a string.  Never prints it.
    """
    mapping = projection or {**DEFAULT_PROJECTION, **ENV_PROJECTION}

    # Best-effort provenance from core's SecretSource (Bitwarden/1Password/…).
    # When core also provides a var, annotate it — core's apply_all runs at Hermes
    # startup and wins over .env.generated at runtime, so this surfaces the
    # split-brain rather than silently duplicating core's read path.
    try:
        from security.secret_source_bridge import core_secret_sources

        _core_sources = core_secret_sources()
    except Exception:
        _core_sources = {}

    lines: list[str] = [
        "# ~/.hermes/.env.generated",
        "# Generated by hermes-key-rotate.py",
        "# Do not edit manually.",
        "",
    ]

    rendered_count = 0
    denied_count = 0

    for env_var, secret_ref in sorted(mapping.items()):
        # ── Gate 1: Hard deny_render list ──
        if secret_ref in DENY_RENDER:
            lines.append(
                f"# {env_var}=<DENIED: {secret_ref} is in deny_render list — not renderable>"
            )
            denied_count += 1
            continue

        # ── Gate 2: Path-segment admin classification ──
        if _is_admin_ref(secret_ref):
            lines.append(
                f"# {env_var}=<DENIED: {secret_ref} classified as admin by path — not renderable>"
            )
            denied_count += 1
            continue

        secret = backend.get_secret(secret_ref)
        if secret and secret.value:
            # ── Gate 3: Vaultwarden metadata classification ──
            meta = backend.get_metadata(secret_ref)
            if meta and not meta.renderable_to_env:
                lines.append(
                    f"# {env_var}=<DENIED: {secret_ref} classified {meta.secret_class} — not renderable>"
                )
                denied_count += 1
                continue

            value = secret.value
            fp, last4 = secret_fingerprint(value)
            src = f"vaultwarden:{secret_ref}"
            core_label = _core_sources.get(env_var)
            if core_label:
                src += f" (also core:{core_label} — core wins at runtime)"
            lines.append(f"# fingerprint={fp} last4={last4} source={src}")
            lines.append(f'{env_var}="{value}"')
            rendered_count += 1
        else:
            lines.append(f"# {env_var}=<NOT FOUND: {secret_ref}>")

    lines.append(f"# ── {rendered_count} rendered, {denied_count} denied ──")
    lines.append("")
    return "\n".join(lines)


def render_env(
    backend: SecretBackend,
    output_path: str | None = None,
    projection: dict[str, str] | None = None,
) -> str:
    """Render .env.generated and atomically write to *output_path*.

    Returns the path written.
    """
    from env.atomic_write import atomic_write  # pyright: ignore[reportMissingImports]

    content = render_env_content(backend, projection)
    target = output_path or os.path.expanduser("~/.hermes/.env.generated")
    atomic_write(target, content)
    return target
