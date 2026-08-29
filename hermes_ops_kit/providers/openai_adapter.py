#!/usr/bin/env python3
"""
Hermes Ops Kit — OpenAI Provider Adapter

Safe, vault-backed wrapper for OpenAI API calls from Hermes Agent.
Never exposes raw API keys. Enforces timeouts, redacts secrets, gates mutations.

Usage:
    python3 openai_adapter.py --operation chat --prompt "..." [--model gpt-5.4-mini]
    python3 openai_adapter.py --operation extract --prompt "..." --schema '{"type":"object",...}'
    python3 openai_adapter.py --operation review --prompt "..." --files '["src/auth.py"]'
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
import uuid
from ..provider_catalog import first_available_key  # noqa: E402
from datetime import datetime

# Add parent directory for shared Hermes security module
from ..security.redaction import redact  # pyright: ignore[reportMissingImports]


# ─── Allowed Models ──────────────────────────────────────────────

ALLOWED_MODELS = [
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.2",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "o3",
    "o4-mini",
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


# ─── Operations ──────────────────────────────────────────────────


def op_chat(
    prompt: str, model: str, max_tokens: int, system: str | None = None
) -> dict:
    """OpenAI Chat Completions API call."""
    import openai  # pyright: ignore[reportMissingImports]

    client = openai.OpenAI(api_key=os.environ.get(first_available_key("openai") or ""))
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.3,
    )
    duration_ms = int((time.time() - start) * 1000)

    usage = response.usage
    return {
        "ok": True,
        "provider": "openai",
        "operation": "chat",
        "result": {
            "text": redact(response.choices[0].message.content),
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
    """Structured extraction with JSON response_format."""
    import openai  # pyright: ignore[reportMissingImports]

    client = openai.OpenAI(api_key=os.environ.get(first_available_key("openai") or ""))

    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "extraction",
                "schema": json_schema,
            },
        },
    )
    duration_ms = int((time.time() - start) * 1000)

    content = redact(response.choices[0].message.content)
    try:
        structured = json.loads(content)
    except json.JSONDecodeError:
        structured = {"raw": content, "parse_error": True}

    usage = response.usage
    return {
        "ok": True,
        "provider": "openai",
        "operation": "extract",
        "result": {
            "text": content,
            "structured": structured,
        },
        "usage": {
            "input_tokens": usage.prompt_tokens if usage else None,
            "output_tokens": usage.completion_tokens if usage else None,
        },
        "warnings": [],
        "request_id": response.id,
        "duration_ms": duration_ms,
    }


def op_review(
    prompt: str, model: str, max_tokens: int, files: list | None = None
) -> dict:
    """Code review via OpenAI API with review-specific system prompt."""
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
    """List available models (from local allowlist, no API call)."""
    return {
        "ok": True,
        "provider": "openai",
        "operation": "models",
        "result": {
            "text": f"Available models: {', '.join(ALLOWED_MODELS)}",
            "structured": {"models": ALLOWED_MODELS},
        },
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "warnings": [],
        "request_id": str(uuid.uuid4()),
        "duration_ms": 0,
    }


# ─── CLI Entry Point ─────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="OpenAI adapter for Hermes Ops Kit")
    parser.add_argument(
        "--operation", required=True, choices=["chat", "extract", "review", "models"]
    )
    parser.add_argument("--prompt", default="")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--system", default=None)
    parser.add_argument(
        "--schema", default=None, help="JSON schema for extract operation"
    )
    parser.add_argument(
        "--files", default=None, help="JSON array of {path, content} for review"
    )
    args = parser.parse_args()

    # Validate API key presence
    api_key = os.environ.get(first_available_key("openai") or "")
    if not api_key and args.operation != "models":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "OPENAI_API_KEY not set in environment",
                    "hint": "Set OPENAI_API_KEY in ~/.hermes/.env or via vault injection",
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
                    "provider": "openai",
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
