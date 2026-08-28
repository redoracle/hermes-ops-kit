#!/usr/bin/env python3
"""
Hermes Ops Kit — NVIDIA NIM Provider Adapter

Safe, vault-backed wrapper for NVIDIA NIM API calls from Hermes Agent.
NVIDIA NIM is OpenAI-compatible, so this uses the `openai` SDK pointed at
https://integrate.api.nvidia.com/v1. Never exposes raw API keys. Enforces
timeouts, redacts secrets, gates mutations.

NVIDIA NIM specifics:
- Reasoning models (Nemotron Ultra) support `reasoning_budget` and
  `chat_template_kwargs.enable_thinking` via `extra_body`.
- Rate limit info is available in response headers (x-ratelimit-*).
- Model list is available via the OpenAI-compatible GET /v1/models.

Usage:
    python3 nvidia_adapter.py --operation chat --prompt "..." [--model nvidia/nemotron-3-ultra-550b-a55b]
    python3 nvidia_adapter.py --operation extract --prompt "..." --schema '{"type":"object",...}'
    python3 nvidia_adapter.py --operation review --prompt "..." --files '[{"path":"src/auth.py","content":"..."}]'
    python3 nvidia_adapter.py --operation models
"""

if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )

import argparse
import json
import os
import sys
import time

# Add parent directory for shared Hermes security module
from ..security.redaction import redact  # pyright: ignore[reportMissingImports]
import uuid
from datetime import datetime

# ─── NVIDIA NIM API config ────────────────────────────────────────

from hermes_ops_kit.provider_catalog import provider_base_url as _cf_base_url  # noqa: E402

# ─── Allowed Models ──────────────────────────────────────────────
# Models available through the NVIDIA NIM serverless API.
# See https://build.nvidia.com/models for the full catalog.

ALLOWED_MODELS = [
    # NVIDIA Nemotron family (confirmed working on this account)
    "nvidia/nemotron-3-ultra-550b-a55b",  # most capable reasoning model
    "nvidia/nemotron-3-super-120b-a12b",  # super tier — balanced
    "nvidia/nemotron-3-nano-30b-a3b",  # nano — fast/cheap (default)
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",  # super v1.5
    "nvidia/nvidia-nemotron-nano-9b-v2",  # nano v2 — ultra-fast
    "nvidia/nemotron-nano-12b-v2-vl",  # vision-language model
    # Other providers on NIM
    "meta/llama-4-maverick-17b-128e-instruct",  # Meta Llama 4 via NIM
    "mistralai/mistral-nemotron",  # Mistral Nemotron via NIM
]


def validate_model(model: str) -> str:
    """Validate model is in allowlist."""
    if model not in ALLOWED_MODELS:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"Model '{model}' not in allowlist. Allowed: {ALLOWED_MODELS}",
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)
    return model


def _client():
    """Build an OpenAI client pointed at the NVIDIA NIM endpoint."""
    import openai  # pyright: ignore[reportMissingImports]

    timeout = int(os.environ.get("NVIDIA_TIMEOUT", "60"))
    return openai.OpenAI(
        api_key=os.environ.get("NVIDIA_API_KEY"),
        base_url=_cf_base_url("nvidia"),
        timeout=timeout,
    )


def _nvidia_extra_body(model: str) -> dict:
    """Build NIM-specific extra_body params for reasoning models.

    Nemotron Ultra supports `reasoning_budget` and
    `chat_template_kwargs.enable_thinking`. Lighter models (70b) don't
    need these — they just add overhead.
    """
    if "ultra" in model.lower():
        return {
            "reasoning_budget": int(os.environ.get("NVIDIA_REASONING_BUDGET", "4096")),
            "chat_template_kwargs": {
                "enable_thinking": os.environ.get(
                    "NVIDIA_ENABLE_THINKING", "true"
                ).lower()
                == "true"
            },
        }
    return {}


# ─── Operations ──────────────────────────────────────────────────


def op_chat(
    prompt: str, model: str, max_tokens: int, system: str | None = None
) -> dict:
    """NVIDIA NIM Chat Completions API call (OpenAI-compatible)."""
    client = _client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Reasoning models (ultra) don't accept temperature — only send it for non-reasoning models.
    create_kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if "ultra" not in model.lower():
        create_kwargs["temperature"] = 0.3
    # Add NIM-specific extra_body (reasoning_budget, enable_thinking)
    create_kwargs["extra_body"] = _nvidia_extra_body(model)

    start = time.time()
    response = client.chat.completions.create(**create_kwargs)  # pyright: ignore[reportArgumentType]
    duration_ms = int((time.time() - start) * 1000)

    usage = response.usage
    return {
        "ok": True,
        "provider": "nvidia",
        "operation": "chat",
        "result": {
            "text": response.choices[0].message.content,
            "structured": None,
        },
        "usage": {
            "input_tokens": usage.prompt_tokens if usage else None,
            "output_tokens": usage.completion_tokens if usage else None,
        },
        "warnings": [],
        "request_id": response.id,
        "duration_ms": duration_ms,
    }


def op_extract(prompt: str, model: str, json_schema: dict, max_tokens: int) -> dict:
    """Structured extraction via NVIDIA NIM JSON Output mode.

    Uses response_format={"type": "json_object"} (OpenAI-compatible).
    Requires the prompt to mention JSON. JSON mode may not be supported
    on reasoning models, so extraction is forced onto the 70b instruct
    model if an ultra model is selected.
    """
    client = _client()
    chat_model = "nvidia/nemotron-3-nano-30b-a3b" if "ultra" in model.lower() else model

    system = (
        "You are a precise data extractor. Respond with a single valid JSON "
        "object that conforms to this JSON schema:\n"
        f"{json.dumps(json_schema)}\n"
        "Output JSON only — no prose, no code fences."
    )

    start = time.time()
    response = client.chat.completions.create(
        model=chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    duration_ms = int((time.time() - start) * 1000)

    content = response.choices[0].message.content
    try:
        structured = json.loads(content)
    except json.JSONDecodeError:
        structured = {"raw": content, "parse_error": True}

    usage = response.usage
    return {
        "ok": True,
        "provider": "nvidia",
        "operation": "extract",
        "result": {
            "text": content,
            "structured": structured,
        },
        "usage": {
            "input_tokens": usage.prompt_tokens if usage else None,
            "output_tokens": usage.completion_tokens if usage else None,
        },
        "warnings": []
        if chat_model == model
        else [f"extract forced onto {chat_model} (JSON mode unsupported on ultra)"],
        "request_id": response.id,
        "duration_ms": duration_ms,
    }


def op_review(
    prompt: str, model: str, max_tokens: int, files: list | None = None
) -> dict:
    """Code review via NVIDIA NIM API with review-specific system prompt."""
    system = (
        "You are a senior code reviewer. Review the provided code for: "
        "1) Security vulnerabilities (SQL injection, XSS, auth bypass, secrets in code) "
        "2) Bugs and logic errors "
        "3) Performance issues "
        "4) Best practice violations. "
        "Be specific — reference exact line numbers where possible. "
        "If the code is safe, say so clearly."
    )
    if files:
        file_context = "\n".join(
            f"--- {f.get('path', 'unknown')} ---\n{f.get('content', '')}" for f in files
        )
        prompt = f"Review these files:\n\n{file_context}\n\nAdditional instructions: {prompt}"

    return op_chat(prompt, model, max_tokens, system)


def op_models() -> dict:
    """List available models from NVIDIA NIM API (dynamic, not allowlist).

    Queries GET /v1/models so the full catalog is visible (like OpenWebUI).
    Falls back to ALLOWED_MODELS if the API call fails.
    """
    model_ids = list(ALLOWED_MODELS)
    try:
        client = _client()
        # Iterate over the SyncPage directly — auto-paginates through
        # every page. Using .data would only return the first page
        # (default 50 items), hiding most models from the catalog.
        model_ids = sorted(m.id for m in client.models.list())
    except Exception:
        pass  # fall back to allowlist

    return {
        "ok": True,
        "provider": "nvidia",
        "operation": "models",
        "result": {
            "text": f"Available models: {', '.join(model_ids)}",
            "structured": {"models": model_ids},
        },
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "warnings": [],
        "request_id": str(uuid.uuid4()),
        "duration_ms": 0,
    }


# ─── CLI Entry Point ─────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="NVIDIA NIM Bridge for Hermes Agent")
    parser.add_argument(
        "--operation",
        required=True,
        choices=["chat", "extract", "review", "models"],
    )
    parser.add_argument("--prompt", default="")
    parser.add_argument("--model", default="nvidia/nemotron-3-nano-30b-a3b")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--system", default=None)
    parser.add_argument(
        "--schema", default=None, help="JSON schema for extract operation"
    )
    parser.add_argument(
        "--files",
        default=None,
        help="JSON array of {path, content} for review",
    )
    args = parser.parse_args()

    # Validate API key presence
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key and args.operation != "models":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "NVIDIA_API_KEY not set in environment",
                    "hint": "Set NVIDIA_API_KEY in ~/.hermes/.env or via vault injection",
                }
            )
        )
        sys.exit(1)

    validate_model(args.model)

    try:
        if args.operation == "chat":
            result = op_chat(args.prompt, args.model, args.max_tokens, args.system)
        elif args.operation == "extract":
            schema = (
                json.loads(args.schema)
                if args.schema
                else {"type": "object", "properties": {}}
            )
            result = op_extract(args.prompt, args.model, schema, args.max_tokens)
        elif args.operation == "review":
            files = json.loads(args.files) if args.files else None
            result = op_review(args.prompt, args.model, args.max_tokens, files)
        elif args.operation == "models":
            result = op_models()
        else:
            result = {"ok": False, "error": f"Unknown operation: {args.operation}"}

        # Redact any secrets that might have leaked in output
        if "result" in result and "text" in result["result"]:
            result["result"]["text"] = redact(result["result"]["text"])

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "provider": "nvidia",
                    "operation": args.operation,
                    "error": redact(str(e)),
                    "error_type": type(e).__name__,
                    "request_id": str(uuid.uuid4()),
                    "timestamp": datetime.now(datetime.UTC).isoformat(),  # pyright: ignore[reportAttributeAccessIssue]
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
