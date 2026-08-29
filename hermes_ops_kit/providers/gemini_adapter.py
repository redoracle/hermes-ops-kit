#!/usr/bin/env python3
"""
Hermes Ops Kit — Google Gemini Provider Adapter

Safe, vault-backed wrapper for Gemini API and Gemini CLI.
Supports long-context reasoning (1M+ tokens), multimodal, and grounded search.

Usage:
    python3 gemini_adapter.py --operation generate --prompt "..."
    python3 gemini_adapter.py --operation grounded --prompt "..."
    python3 gemini_adapter.py --operation cli_plan --prompt "..." --workdir /path/to/repo
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
import subprocess
import sys
import time

# Add parent directory for shared Hermes security module
from ..security.redaction import redact  # pyright: ignore[reportMissingImports]
import uuid
from ..provider_catalog import first_available_key, key_envs_for  # noqa: E402

ALLOWED_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3-pro-preview",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]


def validate_model(model: str) -> str:
    if model not in ALLOWED_MODELS:
        print(
            json.dumps({"ok": False, "error": f"Model '{model}' not in allowlist."}),
            file=sys.stderr,
        )
        sys.exit(1)
    return model


# ─── Gemini API Operations ──────────────────────────────────────


def op_generate(
    prompt: str, model: str, max_tokens: int, system: str | None = None
) -> dict:
    """Gemini API generate content."""
    from google import genai  # pyright: ignore[reportAttributeAccessIssue]

    api_key = os.environ.get(first_available_key("gemini") or "")
    if not api_key:
        return {"ok": False, "error": f"No gemini credential set (any of: {', '.join(key_envs_for('gemini'))})"}

    client = genai.Client(api_key=api_key)

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": f"[SYSTEM] {system}"}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    start = time.time()
    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config={"max_output_tokens": max_tokens, "temperature": 0.3},
        )
    except Exception as e:
        return {
            "ok": False,
            "provider": "gemini",
            "operation": "generate",
            "error": redact(str(e)),
            "error_type": type(e).__name__,
        }

    duration_ms = int((time.time() - start) * 1000)
    text = response.text if response.text else ""

    return {
        "ok": True,
        "provider": "gemini",
        "operation": "generate",
        "result": {"text": redact(text), "structured": None},
        "usage": {
            "input_tokens": response.usage_metadata.prompt_token_count
            if response.usage_metadata
            else None,
            "output_tokens": response.usage_metadata.candidates_token_count
            if response.usage_metadata
            else None,
        },
        "warnings": [],
        "request_id": str(uuid.uuid4()),
        "duration_ms": duration_ms,
    }


def op_grounded(
    prompt: str, model: str = "gemini-2.5-flash", max_tokens: int = 2000
) -> dict:
    """Gemini API with Google Search grounding."""
    from google import genai  # pyright: ignore[reportAttributeAccessIssue]

    api_key = os.environ.get(first_available_key("gemini") or "")
    if not api_key:
        return {"ok": False, "error": f"No gemini credential set (any of: {', '.join(key_envs_for('gemini'))})"}

    client = genai.Client(api_key=api_key)

    start = time.time()
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "max_output_tokens": max_tokens,
                "tools": [{"google_search": {}}],
            },
        )
    except Exception as e:
        return {
            "ok": False,
            "provider": "gemini",
            "operation": "grounded",
            "error": redact(str(e)),
            "error_type": type(e).__name__,
        }

    duration_ms = int((time.time() - start) * 1000)
    text = response.text if response.text else ""

    # Extract grounding sources if available
    sources = []
    if response.candidates and response.candidates[0].grounding_metadata:
        for chunk in response.candidates[0].grounding_metadata.grounding_chunks or []:
            if hasattr(chunk, "web") and chunk.web:
                sources.append(
                    {
                        "title": getattr(chunk.web, "title", ""),
                        "uri": getattr(chunk.web, "uri", ""),
                    }
                )

    return {
        "ok": True,
        "provider": "gemini",
        "operation": "grounded",
        "result": {
            "text": redact(text),
            "structured": {"sources": sources} if sources else None,
        },
        "usage": {
            "input_tokens": response.usage_metadata.prompt_token_count
            if response.usage_metadata
            else None,
            "output_tokens": response.usage_metadata.candidates_token_count
            if response.usage_metadata
            else None,
        },
        "warnings": [],
        "request_id": str(uuid.uuid4()),
        "duration_ms": duration_ms,
    }


# ─── Gemini CLI Operations ──────────────────────────────────────


def op_cli_plan(prompt: str, workdir: str = ".", timeout: int = 300) -> dict:
    """Run Gemini CLI in plan mode (read-only)."""
    gemini_bin = "/opt/homebrew/bin/gemini"

    if not os.path.exists(gemini_bin):
        return {
            "ok": False,
            "error": "gemini CLI not found at /opt/homebrew/bin/gemini",
        }

    start = time.time()
    cmd = [gemini_bin, "-p", prompt, "--approval-mode", "plan", "-o", "json"]

    try:
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "provider": "gemini",
            "operation": "cli_plan",
            "error": f"Gemini CLI timed out after {timeout}s",
            "duration_ms": timeout * 1000,
        }

    duration_ms = int((time.time() - start) * 1000)
    stdout_redacted = redact(result.stdout)
    stderr_redacted = redact(result.stderr)

    try:
        parsed = json.loads(stdout_redacted)
        return {
            "ok": result.returncode == 0,
            "provider": "gemini",
            "operation": "cli_plan",
            "result": {
                "text": parsed.get("result", stdout_redacted),
                "structured": parsed,
            },
            "stderr_redacted": stderr_redacted,
            "usage": {},
            "warnings": [],
            "duration_ms": duration_ms,
        }
    except json.JSONDecodeError:
        return {
            "ok": result.returncode == 0,
            "provider": "gemini",
            "operation": "cli_plan",
            "result": {"text": stdout_redacted, "structured": None},
            "stderr_redacted": stderr_redacted,
            "usage": {},
            "warnings": ["Non-JSON output"] if result.returncode == 0 else [],
            "duration_ms": duration_ms,
        }


def op_models() -> dict:
    """List available Gemini models (from allowlist)."""
    return {
        "ok": True,
        "provider": "gemini",
        "operation": "models",
        "result": {
            "text": f"Available: {', '.join(ALLOWED_MODELS)}",
            "structured": {"models": ALLOWED_MODELS},
        },
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "warnings": [],
        "request_id": str(uuid.uuid4()),
        "duration_ms": 0,
    }


# ─── CLI Entry Point ─────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Gemini Bridge for Hermes Agent")
    parser.add_argument(
        "--operation",
        required=True,
        choices=["generate", "grounded", "cli_plan", "models"],
    )
    parser.add_argument("--prompt", default="")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--system", default=None)
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    if args.operation != "models":
        validate_model(args.model)

    try:
        if args.operation == "generate":
            result = op_generate(args.prompt, args.model, args.max_tokens, args.system)
        elif args.operation == "grounded":
            result = op_grounded(args.prompt, args.model, args.max_tokens)
        elif args.operation == "cli_plan":
            result = op_cli_plan(args.prompt, args.workdir, args.timeout)
        elif args.operation == "models":
            result = op_models()
        else:
            result = {"ok": False, "error": f"Unknown operation: {args.operation}"}

        if "result" in result and "text" in result["result"]:
            result["result"]["text"] = redact(result["result"]["text"] or "")
        if "stderr_redacted" in result:
            result["stderr_redacted"] = redact(result["stderr_redacted"])

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "provider": "gemini",
                    "operation": args.operation,
                    "error": redact(str(e)),
                    "error_type": type(e).__name__,
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
