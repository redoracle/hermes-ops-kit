#!/Users/tesla/miniconda3/bin/python3
"""
Hermes Ops Kit — Anthropic Claude Provider Adapter

Safe, vault-backed wrapper for Anthropic API and Claude Code CLI.
Never exposes raw API keys. Enforces timeouts, redacts secrets, gates mutations.

Usage:
    python3 claude_adapter.py --operation api_chat --prompt "..." [--model claude-sonnet-4-6]
    python3 claude_adapter.py --operation review --prompt "..." --workdir /path/to/repo
    python3 claude_adapter.py --operation analyze --prompt "..."
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

# Add parent directory for shared Hermes security module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from security.redaction import redact  # pyright: ignore[reportMissingImports]

ALLOWED_MODELS = [
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",
    "claude-opus-4-7",
    "claude-sonnet-4-5",
    "sonnet",  # alias → latest Sonnet
    "opus",  # alias → latest Opus
    "haiku",  # alias → latest Haiku
]

MODEL_MAP = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-opus-4-8": "claude-opus-4-8",
    "claude-opus-4-7": "claude-opus-4-7",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4-5": "claude-sonnet-4-5",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5-20251001",
}


def validate_model(model: str) -> str:
    if model not in ALLOWED_MODELS:
        print(
            json.dumps({"ok": False, "error": f"Model '{model}' not in allowlist."}),
            file=sys.stderr,
        )
        sys.exit(1)
    return model


# ─── API Operations ─────────────────────────────────────────────


def op_api_chat(
    prompt: str, model: str, max_tokens: int, system: str | None = None
) -> dict:
    """Anthropic Messages API call."""
    import anthropic  # pyright: ignore[reportMissingImports]

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    api_model = MODEL_MAP.get(model, model)

    messages = [{"role": "user", "content": prompt}]

    start = time.time()
    response = client.messages.create(
        model=api_model,
        max_tokens=max_tokens,
        system=system or "",
        messages=messages,
    )
    duration_ms = int((time.time() - start) * 1000)

    response_text = response.content[0].text if response.content else ""
    return {
        "ok": True,
        "provider": "anthropic",
        "operation": "api_chat",
        "result": {
            "text": redact(response_text),
            "structured": None,
        },
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "warnings": [],
        "request_id": response.id,
        "duration_ms": duration_ms,
    }


def op_api_extract(prompt: str, model: str, json_schema: dict, max_tokens: int) -> dict:
    """Structured extraction with tool use forcing JSON output."""
    import anthropic  # pyright: ignore[reportMissingImports]

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    api_model = MODEL_MAP.get(model, model)

    start = time.time()
    response = client.messages.create(
        model=api_model,
        max_tokens=max_tokens,
        system=f"You must respond with valid JSON matching this schema. Return ONLY the JSON object, no other text.\nSchema: {json.dumps(json_schema)}",
        messages=[{"role": "user", "content": prompt}],
    )
    duration_ms = int((time.time() - start) * 1000)

    content = redact(response.content[0].text if response.content else "")
    try:
        structured = json.loads(content)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code block
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
        if match:
            try:
                structured = json.loads(match.group(1))
            except json.JSONDecodeError:
                structured = {"raw": content, "parse_error": True}
        else:
            structured = {"raw": content, "parse_error": True}

    return {
        "ok": True,
        "provider": "anthropic",
        "operation": "extract",
        "result": {"text": content, "structured": structured},
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "warnings": [],
        "request_id": response.id,
        "duration_ms": duration_ms,
    }


# ─── Claude Code Operations ─────────────────────────────────────


def op_claude_code(
    operation: str,
    prompt: str,
    workdir: str = ".",
    model: str = "sonnet",
    allowed_tools: str = "Read,Grep,Glob",
    max_budget: float = 1.00,
    timeout: int = 300,
) -> dict:
    """Run Claude Code in non-interactive print mode with safety constraints."""
    claude_bin = os.path.expanduser("~/.local/bin/claude")

    cmd = [
        claude_bin,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--allowedTools",
        allowed_tools,
        "--max-budget-usd",
        str(max_budget),
        "--max-turns",
        "10",
    ]

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CLAUDE_CODE_SIMPLE": "1"},
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "provider": "anthropic",
            "operation": operation,
            "error": f"Claude Code timed out after {timeout}s",
            "duration_ms": timeout * 1000,
        }

    duration_ms = int((time.time() - start) * 1000)
    stdout_redacted = redact(result.stdout)
    stderr_redacted = redact(result.stderr)

    # Try to parse JSON output
    try:
        parsed = json.loads(stdout_redacted)
        return {
            "ok": result.returncode == 0,
            "provider": "anthropic",
            "operation": operation,
            "result": {
                "text": parsed.get("result", stdout_redacted),
                "structured": parsed,
            },
            "stderr_redacted": stderr_redacted,
            "usage": {},
            "warnings": ["Claude Code non-zero exit"] if result.returncode != 0 else [],
            "duration_ms": duration_ms,
        }
    except json.JSONDecodeError:
        return {
            "ok": result.returncode == 0,
            "provider": "anthropic",
            "operation": operation,
            "result": {"text": stdout_redacted, "structured": None},
            "stderr_redacted": stderr_redacted,
            "usage": {},
            "warnings": ["Non-JSON output from Claude Code"]
            if result.returncode == 0
            else [],
            "duration_ms": duration_ms,
        }


# ─── CLI Entry Point ─────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Anthropic Claude Bridge for Hermes Agent"
    )
    parser.add_argument(
        "--operation",
        required=True,
        choices=["api_chat", "api_extract", "review", "analyze", "readonly"],
    )
    parser.add_argument("--prompt", default="")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--system", default=None)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    validate_model(args.model)

    try:
        if args.operation in ("api_chat",):
            result = op_api_chat(args.prompt, args.model, args.max_tokens, args.system)
        elif args.operation == "api_extract":
            schema = json.loads(args.schema) if args.schema else {"type": "object"}
            result = op_api_extract(args.prompt, args.model, schema, args.max_tokens)
        elif args.operation in ("review", "analyze"):
            result = op_claude_code(
                args.operation,
                args.prompt,
                args.workdir,
                args.model,
                "Read,Grep,Glob",
                1.00,
                args.timeout,
            )
        elif args.operation == "readonly":
            result = op_claude_code(
                "readonly",
                args.prompt,
                args.workdir,
                args.model,
                "Read,Grep,Glob",
                0.50,
                args.timeout,
            )
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
                    "provider": "anthropic",
                    "operation": args.operation,
                    "error": redact(str(e)),
                    "error_type": type(e).__name__,
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
