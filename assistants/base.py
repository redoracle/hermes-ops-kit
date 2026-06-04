"""Hermes Ops Kit — Assistant Base Types

AssistantConfig dataclass and AssistantClient Protocol.
Provider rotators and delegation tools depend only on these interfaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AssistantConfig:
    """Configuration for a single remote assistant."""

    id: str
    display_name: str
    type: str  # "remote_hermes"
    role: str  # "remote_worker"
    enabled: bool = True
    environment: str = "lan"
    transport: str = "openai_chat_completions"
    future_transport: str = "a2a"

    # Endpoint
    base_url_env: str = ""
    api_key_env: str = ""
    model_env: str = ""
    default_model: str = "hermes-agent"
    health_url: str = ""

    # Security
    network_zone: str = "vpn"
    require_vpn: bool = True
    require_tls: bool = False
    require_token: bool = True
    token_scope: str = ""
    allow_secret_prompts: bool = False
    allow_env_requests: bool = False
    allow_file_mutation: bool = False
    allow_shell_execution: bool = False
    allow_network_scan: bool = False
    allow_repo_write: bool = False
    sanitize_input: bool = True
    sanitize_output: bool = True

    # Policy
    max_timeout_seconds: int = 180
    max_prompt_bytes: int = 50000
    max_response_bytes: int = 200000
    max_parallel_tasks: int = 2
    max_retries: int = 1
    require_approval_for: list[str] = field(default_factory=list)

    # Capabilities
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    blocked_capabilities: list[str] = field(default_factory=list)

    # System prompt
    orchestrator_name: str = "hermes"
    ping_response_token: str = "OK"

    # Tool registration
    tool_name: str = ""


@dataclass
class AssistantTask:
    """A delegated task to a remote assistant."""

    task_id: str
    assistant_id: str
    capability: str
    task: str
    context: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    status: str = "created"


@dataclass
class AssistantResult:
    """Result from a remote assistant delegation."""

    ok: bool
    assistant: str
    task_id: str
    transport: str
    duration_ms: int
    result: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class AssistantClient(Protocol):
    """Protocol that every assistant client must satisfy."""

    def healthcheck(self) -> dict[str, Any]:
        """Return assistant health status."""
        ...

    def delegate(self, task: AssistantTask) -> AssistantResult:
        """Delegate a task and return the result."""
        ...
