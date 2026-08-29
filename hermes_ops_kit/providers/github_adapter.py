#!/usr/bin/env python3
"""
Hermes Ops Kit — GitHub Provider Adapter

Safe, vault-backed wrapper for GitHub CLI (gh v2.92.0).
Read-only by default. Mutations require explicit human approval.

Usage:
    python3 github_adapter.py --operation pr_list [--repo owner/repo] [--limit 20]
    python3 github_adapter.py --operation pr_diff --pr 42
    python3 github_adapter.py --operation ci_status [--limit 10]
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

# ─── Read-Only Operations (Safe, No Approval) ─────────────────────


def op_pr_list(repo: str | None = None, limit: int = 20, state: str = "open") -> dict:
    """List PRs in a repository."""
    start = time.time()
    args = [
        "pr",
        "list",
        "--json",
        "number,title,author,createdAt,labels,state,headRefName,baseRefName",
        "--limit",
        str(limit),
        "--state",
        state,
    ]
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh(args, "pr_list", start)
    # Parse JSON output from gh
    try:
        result["result"]["structured"] = json.loads(result["result"]["text"])
    except json.JSONDecodeError:
        pass
    return result


def op_pr_view(pr_number: str, repo: str | None = None) -> dict:
    """View a specific PR."""
    start = time.time()
    args = [
        "pr",
        "view",
        pr_number,
        "--json",
        "number,title,author,body,state,createdAt,mergeable,reviews,comments,labels",
    ]
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh(args, "pr_view", start)
    try:
        result["result"]["structured"] = json.loads(result["result"]["text"])
    except json.JSONDecodeError:
        pass
    return result


def op_pr_diff(pr_number: str, repo: str | None = None) -> dict:
    """Get PR diff."""
    start = time.time()
    args = ["pr", "diff", str(pr_number)]
    if repo:
        args.extend(["--repo", repo])

    return _run_gh(args, "pr_diff", start)


def op_issue_list(
    repo: str | None = None, limit: int = 20, state: str = "open"
) -> dict:
    """List issues."""
    start = time.time()
    args = [
        "issue",
        "list",
        "--json",
        "number,title,state,labels,author,createdAt",
        "--limit",
        str(limit),
        "--state",
        state,
    ]
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh(args, "issue_list", start)
    try:
        result["result"]["structured"] = json.loads(result["result"]["text"])
    except json.JSONDecodeError:
        pass
    return result


def op_ci_status(repo: str | None = None, limit: int = 10) -> dict:
    """Check CI run status."""
    start = time.time()
    args = [
        "run",
        "list",
        "--json",
        "name,status,conclusion,headBranch,createdAt,displayTitle",
        "--limit",
        str(limit),
    ]
    if repo:
        args.extend(["--repo", repo])

    result = _run_gh(args, "ci_status", start)
    try:
        result["result"]["structured"] = json.loads(result["result"]["text"])
    except json.JSONDecodeError:
        pass
    return result


def op_search_code(query: str, repo: str | None = None, limit: int = 30) -> dict:
    """Search code on GitHub."""
    start = time.time()
    args = ["search", "code", query, "--limit", str(limit)]
    if repo:
        args.extend(["--repo", repo])
    args.append("--json")
    args.append("path,repository")

    result = _run_gh(args, "search_code", start)
    try:
        result["result"]["structured"] = json.loads(result["result"]["text"])
    except json.JSONDecodeError:
        pass
    return result


def op_read_file(repo: str, path: str, ref: str | None = None) -> dict:
    """Read a file from a GitHub repository."""
    start = time.time()
    endpoint = f"repos/{repo}/contents/{path}"
    args = ["api", endpoint]
    if ref:
        args.extend(["-f", f"ref={ref}"])

    result = _run_gh(args, "read_file", start)
    try:
        result["result"]["structured"] = json.loads(result["result"]["text"])
    except json.JSONDecodeError:
        pass
    return result


# ─── Mutation Operations (Approval Required) ─────────────────────


def op_pr_create(
    title: str,
    body: str,
    base: str = "main",
    head: str | None = None,
    repo: str | None = None,
    require_approval: bool = True,
) -> dict:
    """Create a PR — REQUIRES APPROVAL."""
    if require_approval:
        return {
            "ok": False,
            "provider": "github",
            "operation": "pr_create",
            "error": "PR creation requires explicit human approval. Set require_approval=false only after review.",
            "blocked": True,
        }

    start = time.time()
    args = ["pr", "create", "--title", title, "--body", body, "--base", base]
    if head:
        args.extend(["--head", head])
    if repo:
        args.extend(["--repo", repo])

    return _run_gh(args, "pr_create", start)


# ─── Helpers ─────────────────────────────────────────────────────


def _run_gh(args: list, operation: str, start: float, timeout: int = 60) -> dict:
    """Run gh CLI command and return structured result."""
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "provider": "github",
            "operation": operation,
            "error": f"gh CLI timed out after {timeout}s",
            "duration_ms": timeout * 1000,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "provider": "github",
            "operation": operation,
            "error": "gh CLI not found. Install: brew install gh",
        }

    duration_ms = int((time.time() - start) * 1000)
    stdout_redacted = redact(result.stdout)
    stderr_redacted = redact(result.stderr)

    return {
        "ok": result.returncode == 0,
        "provider": "github",
        "operation": operation,
        "result": {
            "text": stdout_redacted,
            "structured": None,
        },
        "stderr_redacted": stderr_redacted,
        "usage": {"api_calls": 1},
        "warnings": ["gh non-zero exit"] if result.returncode != 0 else [],
        "request_id": str(uuid.uuid4()),
        "duration_ms": duration_ms,
    }


# ─── CLI Entry Point ─────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="GitHub Bridge for Hermes Agent")
    parser.add_argument(
        "--operation",
        required=True,
        choices=[
            "pr_list",
            "pr_view",
            "pr_diff",
            "issue_list",
            "ci_status",
            "search_code",
            "read_file",
            "pr_create",
        ],
    )
    parser.add_argument("--repo", default=None)
    parser.add_argument("--pr", default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--state", default="open")
    parser.add_argument("--query", default="")
    parser.add_argument("--path", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--body", default=None)
    parser.add_argument("--base", default="main")
    parser.add_argument("--head", default=None)
    parser.add_argument(
        "--require-approval",
        type=lambda v: str(v).lower() in ("true", "1", "yes"),
        default=True,
    )
    args = parser.parse_args()

    # Check gh availability
    if not os.path.exists("/opt/homebrew/bin/gh"):
        print(
            json.dumps(
                {"ok": False, "error": "gh CLI not found at /opt/homebrew/bin/gh"}
            )
        )
        sys.exit(1)

    try:
        if args.operation == "pr_list":
            result = op_pr_list(args.repo, args.limit, args.state)
        elif args.operation == "pr_view":
            result = (
                op_pr_view(args.pr, args.repo)
                if args.pr
                else {"ok": False, "error": "--pr required"}
            )
        elif args.operation == "pr_diff":
            result = (
                op_pr_diff(args.pr, args.repo)
                if args.pr
                else {"ok": False, "error": "--pr required"}
            )
        elif args.operation == "issue_list":
            result = op_issue_list(args.repo, args.limit, args.state)
        elif args.operation == "ci_status":
            result = op_ci_status(args.repo, args.limit)
        elif args.operation == "search_code":
            result = (
                op_search_code(args.query, args.repo, args.limit)
                if args.query
                else {"ok": False, "error": "--query required"}
            )
        elif args.operation == "read_file":
            result = (
                op_read_file(args.repo, args.path, args.ref)
                if (args.repo and args.path)
                else {"ok": False, "error": "--repo and --path required"}
            )
        elif args.operation == "pr_create":
            result = (
                op_pr_create(
                    args.title,
                    args.body,
                    args.base,
                    args.head,
                    args.repo,
                    args.require_approval,
                )
                if args.title
                else {"ok": False, "error": "--title required"}
            )
        else:
            result = {"ok": False, "error": f"Unknown operation: {args.operation}"}

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "provider": "github",
                    "operation": args.operation,
                    "error": redact(str(e)),
                    "error_type": type(e).__name__,
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
