"""Hermes Ops Kit — Assistant Registry

Loads and validates config/assistants.yaml.
Provides lookup by assistant ID.
"""

from __future__ import annotations

import os
from typing import Any

from ..assistants.base import AssistantConfig  # pyright: ignore[reportMissingImports]
from ..ops_config_io import HERMES_HOME  # noqa: E402


def _load_yaml(path: str) -> dict[str, Any]:
    """Load YAML without PyYAML dependency (simple parser for our format)."""
    result: dict[str, Any] = {}
    current_section: str | None = None
    current_assistant: str | None = None
    current_subsection: str | None = None
    list_context: list[Any] | None = None
    current_list_item: dict[str, Any] | None = None

    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())

            # Top-level key
            if indent == 0 and stripped.endswith(":"):
                current_section = stripped[:-1]
                if current_section not in result:
                    result[current_section] = {}
                continue

            # Assistant name under "assistants:"
            if (
                indent == 2
                and current_section == "assistants"
                and stripped.endswith(":")
            ):
                current_assistant = stripped[:-1]
                result[current_section][current_assistant] = {}
                current_subsection = None
                list_context = None
                current_list_item = None
                continue

            # Key-value at assistant level (indent 2)
            if (
                indent == 2
                and current_section
                and current_assistant
                and not stripped.endswith(":")
                and ":" in stripped
            ):
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val == "true":
                    val = True
                elif val == "false":
                    val = False
                elif val.isdigit():
                    val = int(val)
                result[current_section][current_assistant][key] = val
                continue

            # Scalar key-value at indent 4 (e.g. "type: remote_hermes" — has value after colon)
            if (
                indent == 4
                and current_section
                and current_assistant
                and ":" in stripped
                and not stripped.rstrip(":").endswith(" ")
            ):
                after = stripped.split(":", 1)[1].strip()
                if after:
                    key = stripped.split(":", 1)[0].strip()
                    val = after.strip().strip('"').strip("'")
                    if val == "true":
                        val = True
                    elif val == "false":
                        val = False
                    elif val.isdigit():
                        val = int(val)
                    result[current_section][current_assistant][key] = val
                    continue

            # Section header under assistant (key with no value: endpoint:, security:, etc.)
            if (
                indent == 4
                and current_section
                and current_assistant
                and stripped.endswith(":")
            ):
                current_subsection = stripped[:-1]
                if current_subsection in (
                    "capabilities",
                    "blocked_capabilities",
                    "require_approval_for",
                    "data_classification",
                ):
                    result[current_section][current_assistant][current_subsection] = []  # pyright: ignore[reportArgumentType]
                    list_context = result[current_section][current_assistant][
                        current_subsection
                    ]  # pyright: ignore[reportArgumentType]
                    current_list_item = None
                else:
                    result[current_section][current_assistant][current_subsection] = {}  # pyright: ignore[reportArgumentType]
                    list_context = None
                    current_list_item = None
                continue

            # Simple list item: "- value" (string list)
            if (
                indent == 6
                and list_context is not None
                and stripped.startswith("- ")
                and ":" not in stripped[2:]
            ):
                list_context.append(stripped[2:])
                continue

            # Dict list item: "- key: value" starts a new dict entry
            if (
                indent == 6
                and list_context is not None
                and stripped.startswith("- ")
                and ":" in stripped[2:]
            ):
                new_item: dict[str, Any] = {}
                k, _, v = stripped[2:].partition(":")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v == "true":
                    v = True
                elif v == "false":
                    v = False
                new_item[k] = v
                list_context.append(new_item)
                current_list_item = new_item
                continue

            # Continuation key under a list item dict (indent 8)
            if indent == 8 and current_list_item is not None and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val == "true":
                    val = True
                elif val == "false":
                    val = False
                elif val.isdigit():
                    val = int(val)
                current_list_item[key] = val
                continue

            # Key-value under a subsection dict
            if (
                indent == 6
                and current_section
                and current_assistant
                and current_subsection
            ):
                if ":" in stripped and list_context is None:
                    key, _, val = stripped.partition(":")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if val == "true":
                        val = True
                    elif val == "false":
                        val = False
                    elif val.isdigit():
                        val = int(val)
                    subsection_data = result[current_section][current_assistant][
                        current_subsection
                    ]  # pyright: ignore[reportArgumentType]
                    if isinstance(subsection_data, dict):
                        subsection_data[key] = val

    return result


def load_registry(config_path: str | None = None) -> dict[str, AssistantConfig]:
    """Load and validate the assistant registry.

    Returns a dict of assistant_id → AssistantConfig.

    Resolution order:
      1. Explicit *config_path* argument
      2. HERMES_ASSISTANTS_CONFIG env var
      3. ~/.hermes/ops-kit/assistants.yaml   (user's runtime config)
      4. <plugin>/config/assistants.yaml      (bundled fallback)
    """
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runtime_config = os.path.join(HERMES_HOME, "ops-kit", "assistants.yaml")

    path = (
        config_path
        or os.environ.get("HERMES_ASSISTANTS_CONFIG")
        or (runtime_config if os.path.exists(runtime_config) else None)
        or os.path.join(plugin_dir, "config", "assistants.yaml")
    )

    if not os.path.exists(path):
        return {}

    raw = _load_yaml(path)
    assistants_raw = raw.get("assistants", {})
    registry: dict[str, AssistantConfig] = {}

    for aid, adata in assistants_raw.items():
        if not isinstance(adata, dict):
            continue
        if not adata.get("enabled", True):
            continue

        # Defensive: a malformed subsection (e.g. parsed as a scalar) must not
        # crash the whole registry load — fall back to an empty mapping.
        def _section(name: str) -> dict[str, Any]:
            value = adata.get(name, {})
            return value if isinstance(value, dict) else {}

        endpoint = _section("endpoint")
        security = _section("security")
        policy = _section("policy")
        tool = _section("tool")
        system_prompt = _section("system_prompt")

        config = AssistantConfig(
            id=aid,
            display_name=adata.get("display_name", aid),
            type=adata.get("type", "remote_hermes"),
            role=adata.get("role", "remote_worker"),
            enabled=adata.get("enabled", True),
            environment=adata.get("environment", "lan"),
            transport=adata.get("transport", "openai_chat_completions"),
            future_transport=adata.get("future_transport", "a2a"),
            base_url_env=endpoint.get("base_url_env", ""),
            api_key_env=endpoint.get("api_key_env", ""),
            model_env=endpoint.get("model_env", ""),
            default_model=endpoint.get("default_model", "hermes-agent"),
            health_url=endpoint.get("health_url", ""),
            network_zone=security.get("network_zone", "vpn"),
            require_vpn=security.get("require_vpn", True),
            require_tls=security.get("require_tls", False),
            require_token=security.get("require_token", True),
            token_scope=security.get("token_scope", ""),
            allow_secret_prompts=security.get("allow_secret_prompts", False),
            allow_env_requests=security.get("allow_env_requests", False),
            allow_file_mutation=security.get("allow_file_mutation", False),
            allow_shell_execution=security.get("allow_shell_execution", False),
            allow_network_scan=security.get("allow_network_scan", False),
            allow_repo_write=security.get("allow_repo_write", False),
            sanitize_input=security.get("sanitize_input", True),
            sanitize_output=security.get("sanitize_output", True),
            max_timeout_seconds=policy.get("max_timeout_seconds", 180),
            max_prompt_bytes=policy.get("max_prompt_bytes", 50000),
            max_response_bytes=policy.get("max_response_bytes", 200000),
            max_parallel_tasks=policy.get("max_parallel_tasks", 2),
            max_retries=policy.get("max_retries", 1),
            require_approval_for=policy.get("require_approval_for", []),
            capabilities=adata.get("capabilities", []),
            blocked_capabilities=adata.get("blocked_capabilities", []),
            orchestrator_name=system_prompt.get("orchestrator_name", "hermes"),
            ping_response_token=system_prompt.get(
                "ping_response_token", f"{aid.upper()}_OK"
            ),
            tool_name=tool.get("name", f"ai_{aid}_delegate"),
        )
        registry[aid] = config

    # Register aliases so delegation by short name works.
    # The custom YAML parser stores comma-separated strings, not lists.
    for aid, adata in assistants_raw.items():
        if not isinstance(adata, dict) or not adata.get("enabled", True):
            continue
        alias_raw = adata.get("alias", "")
        if not alias_raw:
            continue
        aliases = [a.strip() for a in str(alias_raw).split(",") if a.strip()]
        config = registry.get(aid)
        if config and aliases:
            for alias in aliases:
                if alias not in registry:
                    registry[alias] = config

    return registry


def get_assistant(assistant_id: str) -> AssistantConfig | None:
    """Look up a single assistant by ID."""
    return load_registry().get(assistant_id)


def list_assistants() -> list[str]:
    """List all enabled assistant IDs."""
    return list(load_registry().keys())
