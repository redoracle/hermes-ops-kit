"""Hermes Ops Kit — shared OpenAI-compatible provider ops.

Consolidates the ~95% identical logic across the OpenAI-compat adapter/rotator
triplet (deepseek, fireworks, deepinfra) into one tested module:

  - ``OpenAICompatAdapter`` + ``run_cli()`` — the chat/extract/review/models
    operations, output envelope, redaction pass, and the argparse CLI that the
    bridge dispatches as a subprocess.
  - ``OpenAICompatRotator(BaseRotator)`` — the 8-branch openai exception →
    ValidationReason ladder, the two-phase smoke + rotate flow.

Per-provider files become thin subclasses declaring ~6 class attributes (base
URL, env vars, allowed models, api_ref, chat model) plus, for deepseek only, the
reasoner-divergence hooks (``supports_temperature`` / ``extract_model``).

Subprocess boundary preserved: each adapter stays executable via
``python -P -m hermes_ops_kit.providers.<adapter>`` with
``if __name__ == "__main__": run_cli(AdapterCls)``; imports are package-relative
(no sys.path priming). The bridge contract is unchanged — validate_model
failures go to stderr + exit 1; missing-key/success go to stdout as JSON.

WRITES (rotation: set/backup/restore) stay on the SecretBackend (Vaultwarden) —
core's SecretSource has no write API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime

from ..security.redaction import redact  # pyright: ignore[reportMissingImports]


# ─── Adapter ──────────────────────────────────────────────────────────


class OpenAICompatAdapter:
    """Base for OpenAI-compatible provider adapters (subprocess scripts).

    Subclasses set the class attributes below and optionally override the
    reasoner hooks. ``run_cli(Subclass)`` is the entry point.
    """

    provider: str = ""
    provider_label: str = ""  # display name, e.g. "Fireworks AI"
    base_url_default: str = ""
    base_url_env: str = ""  # env var overriding base_url (e.g. FIREWORKS_BASE_URL)
    api_key_env: str = ""  # env var holding the API key
    timeout_env: str = ""  # env var holding the timeout
    allowed_models: list[str] = []
    default_model: str = ""

    # ── Reasoner-divergence hooks (deepseek overrides) ──
    @classmethod
    def supports_temperature(cls, model: str) -> bool:
        """Return False if *model* rejects a temperature argument (e.g. reasoners)."""
        return True

    @classmethod
    def extract_model(cls, model: str) -> str:
        """Return the model to use for JSON-mode extraction (default: *model*)."""
        return model

    @classmethod
    def extract_warning(cls, model: str) -> str | None:
        """Return a warning if extraction was redirected onto another model.

        Derives from ``extract_model`` so the redirect target and the warning
        cannot drift.
        """
        redirected = cls.extract_model(model)
        if redirected != model:
            return f"extract forced onto {redirected} (JSON mode unsupported on this model)"
        return None

    # ── Ops ──

    @classmethod
    def validate_model(cls, model: str) -> str:
        """Validate model is in the allowlist; JSON error to stderr + exit 1 on miss."""
        if model not in cls.allowed_models:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"Model '{model}' not in allowlist. Allowed: {cls.allowed_models}",
                    }
                ),
                file=sys.stderr,
            )
            sys.exit(1)
        return model

    @classmethod
    def _client(cls):
        """Build an OpenAI client pointed at the provider endpoint."""
        import openai  # pyright: ignore[reportMissingImports]

        timeout = int(os.environ.get(cls.timeout_env, "60"))
        return openai.OpenAI(
            api_key=os.environ.get(cls.api_key_env),
            base_url=os.environ.get(cls.base_url_env, cls.base_url_default),
            timeout=timeout,
        )

    @classmethod
    def op_chat(
        cls, prompt: str, model: str, max_tokens: int, system: str | None = None
    ) -> dict:
        """OpenAI-compatible chat completions call."""
        client = cls._client()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        extra = {} if not cls.supports_temperature(model) else {"temperature": 0.3}

        start = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            **extra,
        )
        duration_ms = int((time.time() - start) * 1000)

        usage = response.usage
        return {
            "ok": True,
            "provider": cls.provider,
            "operation": "chat",
            "result": {"text": response.choices[0].message.content, "structured": None},
            "usage": {
                "input_tokens": usage.prompt_tokens if usage else None,
                "output_tokens": usage.completion_tokens if usage else None,
            },
            "warnings": [],
            "request_id": response.id,
            "duration_ms": duration_ms,
        }

    @classmethod
    def op_extract(
        cls, prompt: str, model: str, json_schema: dict, max_tokens: int
    ) -> dict:
        """Structured extraction via JSON Output mode (response_format json_object)."""
        client = cls._client()
        chat_model = cls.extract_model(model)

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
            structured = json.loads(content)  # pyright: ignore[reportArgumentType]
        except (json.JSONDecodeError, TypeError):
            # JSONDecodeError: malformed JSON; TypeError: content is None (server
            # returned null). Either way, surface a parse_error instead of crashing.
            structured = {"raw": content, "parse_error": True}

        warning = cls.extract_warning(model)
        usage = response.usage
        return {
            "ok": True,
            "provider": cls.provider,
            "operation": "extract",
            "result": {"text": content, "structured": structured},
            "usage": {
                "input_tokens": usage.prompt_tokens if usage else None,
                "output_tokens": usage.completion_tokens if usage else None,
            },
            "warnings": [warning] if warning else [],
            "request_id": response.id,
            "duration_ms": duration_ms,
        }

    @classmethod
    def op_review(
        cls, prompt: str, model: str, max_tokens: int, files: list | None = None
    ) -> dict:
        """Code review via the provider API with a review-specific system prompt."""
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
                f"--- {f.get('path', 'unknown')} ---\n{f.get('content', '')}"
                for f in files
            )
            prompt = f"Review these files:\n\n{file_context}\n\nAdditional instructions: {prompt}"
        return cls.op_chat(prompt, model, max_tokens, system)

    @classmethod
    def op_models(cls) -> dict:
        """List available models (local allowlist, no API call)."""
        return {
            "ok": True,
            "provider": cls.provider,
            "operation": "models",
            "result": {
                "text": f"Available models: {', '.join(cls.allowed_models)}",
                "structured": {"models": cls.allowed_models},
            },
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "warnings": [],
            "request_id": str(uuid.uuid4()),
            "duration_ms": 0,
        }


def run_cli(adapter_cls: type[OpenAICompatAdapter]) -> None:
    """Argparse CLI entry point — the bridge dispatches this as a subprocess.

    Contract: validate_model failures → stderr + exit 1; missing-API-key and
    success → single JSON object on stdout; exceptions → JSON error envelope on
    stdout + exit 1. ``bridge.py`` does ``json.loads(stdout)``.
    """
    parser = argparse.ArgumentParser(
        description=f"{adapter_cls.provider_label} Bridge for Hermes Agent"
    )
    parser.add_argument(
        "--operation", required=True, choices=["chat", "extract", "review", "models"]
    )
    parser.add_argument("--prompt", default="")
    parser.add_argument("--model", default=adapter_cls.default_model)
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--system", default=None)
    parser.add_argument(
        "--schema", default=None, help="JSON schema for extract operation"
    )
    parser.add_argument(
        "--files", default=None, help="JSON array of {path, content} for review"
    )
    args = parser.parse_args()

    api_key = os.environ.get(adapter_cls.api_key_env)
    if not api_key and args.operation != "models":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{adapter_cls.api_key_env} not set in environment",
                    "hint": f"Set {adapter_cls.api_key_env} in ~/.hermes/.env or via vault injection",
                }
            )
        )
        sys.exit(1)

    adapter_cls.validate_model(args.model)

    try:
        if args.operation == "chat":
            result = adapter_cls.op_chat(
                args.prompt, args.model, args.max_tokens, args.system
            )
        elif args.operation == "extract":
            schema = (
                json.loads(args.schema)
                if args.schema
                else {"type": "object", "properties": {}}
            )
            result = adapter_cls.op_extract(
                args.prompt, args.model, schema, args.max_tokens
            )
        elif args.operation == "review":
            files = json.loads(args.files) if args.files else None
            result = adapter_cls.op_review(
                args.prompt, args.model, args.max_tokens, files
            )
        elif args.operation == "models":
            result = adapter_cls.op_models()
        else:
            result = {"ok": False, "error": f"Unknown operation: {args.operation}"}

        # Redact any secrets that might have leaked in output — both the raw
        # text and the parsed structured field (same model output; either may
        # echo a secret, e.g. op_extract returning {"api_key": "sk-..."}).
        if "result" in result and "text" in result["result"]:
            result["result"]["text"] = redact(result["result"]["text"])
        if "result" in result and result["result"].get("structured") is not None:
            result["result"]["structured"] = json.loads(
                redact(json.dumps(result["result"]["structured"]))
            )

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "provider": adapter_cls.provider,
                    "operation": args.operation,
                    "error": redact(str(e)),
                    "error_type": type(e).__name__,
                    "request_id": str(uuid.uuid4()),
                    "timestamp": datetime.now(datetime.UTC).isoformat(),  # pyright: ignore[reportAttributeAccessIssue]
                }
            )
        )
        sys.exit(1)


# ─── Rotator ──────────────────────────────────────────────────────────


from ..providers.base import BaseRotator  # noqa: E402  # pyright: ignore[reportMissingImports]
from ..security.fingerprints import secret_fingerprint  # noqa: E402  # pyright: ignore[reportMissingImports]
from ..security.secret_backend import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    SecretWriteFailed,
    ValidationReason,
    ValidationResult,
)


class OpenAICompatRotator(BaseRotator):
    """Base for OpenAI-compatible provider rotators.

    Subclasses set the class attributes and inherit the 8-branch validate
    ladder, smoke test, and two-phase rotate flow. revoke_key /
    cleanup_orphaned_key inherit the BaseRotator default (False — these
    providers have no admin key API).
    """

    provider: str = ""
    provider_label: str = ""
    api_ref: str = ""  # vault path, e.g. hermes/fireworks/api_key
    base_url_default: str = ""
    base_url_env: str = ""
    chat_model: str = ""  # model used for validate + smoke chat
    env_key: str = ""  # env var updated, e.g. FIREWORKS_API_KEY

    def _base_url(self) -> str:
        return os.environ.get(self.base_url_env, self.base_url_default)

    # ── Validation ──

    def validate_new_key(self, key: str) -> ValidationResult:
        """Validate a candidate key: GET /models (auth) + a minimal chat request."""
        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SDK_UNAVAILABLE,
                detail=f"openai SDK not installed (needed for {self.provider_label} API compatibility)",
                retry_recommended=False,
            )

        try:
            client = openai.OpenAI(api_key=key, base_url=self._base_url(), timeout=15)
            models = client.models.list()
            if not models.data:
                return ValidationResult(
                    valid=False,
                    reason_class=ValidationReason.UNKNOWN,
                    detail="/models returned empty data",
                )
            chat = client.chat.completions.create(
                model=self.chat_model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            if chat.choices:
                return ValidationResult(valid=True)
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail="empty chat response",
            )
        except openai.AuthenticationError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.AUTH_DENIED,
                detail=redact(str(e)),
                http_status=401,
                retry_recommended=False,
            )
        except openai.RateLimitError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.RATE_LIMITED,
                detail=redact(str(e)),
                http_status=429,
                retry_recommended=True,
            )
        except openai.APITimeoutError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.TIMEOUT,
                detail=redact(str(e)),
                retry_recommended=True,
            )
        except openai.APIConnectionError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.NETWORK_ERROR,
                detail=redact(str(e)),
                retry_recommended=True,
            )
        except openai.InternalServerError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.SERVER_ERROR,
                detail=redact(str(e)),
                http_status=500,
                retry_recommended=True,
            )
        except openai.PermissionDeniedError as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.QUOTA_OR_BILLING,
                detail=redact(str(e)),
                http_status=403,
                retry_recommended=False,
            )
        except Exception as e:
            return ValidationResult(
                valid=False,
                reason_class=ValidationReason.UNKNOWN,
                detail=redact(str(e)),
            )

    # ── Smoke test ──

    def smoke_test(self) -> tuple[bool, str]:
        """Run a smoke test against the currently active key."""
        secret = self.backend.get_secret(self.api_ref)
        if not secret or not secret.value:
            return False, "No active API key found in Vaultwarden"
        try:
            import openai  # pyright: ignore[reportMissingImports]
        except Exception:
            return False, "openai SDK not available"
        try:
            client = openai.OpenAI(
                api_key=secret.value, base_url=self._base_url(), timeout=15
            )
            models = client.models.list()
            chat = client.chat.completions.create(
                model=self.chat_model,
                messages=[{"role": "user", "content": "Smoke test"}],
                max_tokens=5,
            )
            if models.data and chat.choices:
                return True, "smoke test passed: /models + chat OK"
            return False, "smoke test failed: empty response"
        except Exception as e:
            return False, redact(f"smoke test failed: {e}")

    # ── Rotation (9-step two-phase flow) ──

    def rotate(self, candidate_key: str | None = None) -> dict:
        """Execute the rotation flow: backup → validate → store → smoke → render → audit."""
        from ..audit.audit_log import audit_rotation_attempt  # pyright: ignore[reportMissingImports]

        # 1. Acquire candidate key
        if not candidate_key:
            candidate_key = self._read_key_stdin()
        candidate_key = candidate_key.strip()
        if not candidate_key:
            return {"ok": False, "error": "No candidate key provided"}

        # 2. Current key fingerprint
        old_secret = self.backend.get_secret(self.api_ref)
        old_fp, old_l4 = (
            secret_fingerprint(old_secret.value) if old_secret else (None, None)
        )

        # Backup for rollback
        backup = self.backend.backup_secret(self.api_ref)
        if backup is None:
            return {
                "ok": False,
                "error": "Backup failed: could not read current secret for rollback",
            }

        # 3. Validate with retry
        vr = self.validate_with_retry(candidate_key)
        if not vr.valid:
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING:
                pass  # valid key, account billing issue — store with warning
            else:
                audit_rotation_attempt(
                    provider=self.provider,
                    status="failed",
                    old_fp=old_fp,
                    env_keys_updated=[],
                )
                return {
                    "ok": False,
                    "error": f"Candidate key unusable: {vr.reason_class.value}",
                    "validation": {
                        "reason": vr.reason_class.value,
                        "detail": vr.detail,
                        "http_status": vr.http_status,
                    },
                }

        new_fp, new_l4 = secret_fingerprint(candidate_key)

        # 4. Store candidate
        try:
            self.backend.set_secret(
                self.api_ref,
                candidate_key,
                metadata={
                    "rotation_mode": "manual-new-key",
                    "last_rotated_at": str(int(time.time())),
                    "old_fingerprint": old_fp or "none",
                    "old_last4": old_l4 or "",
                },
            )
        except SecretWriteFailed as e:
            return {"ok": False, "error": f"Failed to store candidate: {e}"}

        # 5. Smoke test (before rendering env, so a bad key never reaches .env.generated)
        passed, detail = self.smoke_test()
        if not passed:
            rollback_error = None
            if backup:
                try:
                    self.backend.restore_secret(self.api_ref, backup)
                except Exception as restore_err:
                    rollback_error = redact(str(restore_err))
            audit_rotation_attempt(
                provider=self.provider,
                status="smoke_test_failed",
                old_fp=old_fp,
                new_fp=new_fp,
                old_revoked=False,
                manual_action=True,
                env_keys_updated=[],
            )
            result = {
                "ok": False,
                "error": f"Smoke test failed: {detail}",
                "smoke_test": detail,
            }
            if rollback_error:
                result["rollback_error"] = rollback_error
            return result

        # 6. Render env (only after smoke passes)
        from ..env.render_env import render_env  # pyright: ignore[reportMissingImports]

        try:
            env_path = render_env(self.backend)
        except Exception as e:
            if backup:
                try:
                    self.backend.restore_secret(self.api_ref, backup)
                    render_env(self.backend)  # re-render with rolled-back key
                except Exception as rollback_err:
                    return {
                        "ok": False,
                        "error": f"Env render failed: {e}",
                        "rollback_error": str(rollback_err),
                    }
            return {"ok": False, "error": f"Env render failed: {e}"}

        # 7. Audit
        audit_rotation_attempt(
            provider=self.provider,
            status="success",
            old_fp=old_fp,
            new_fp=new_fp,
            old_revoked=False,
            manual_action=True,
            env_keys_updated=[self.env_key],
        )

        return {
            "ok": True,
            "provider": self.provider,
            "operation": "rotation",
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "new_last4": new_l4,
            "env_rendered": env_path,
            "smoke_test": detail,
            "warnings": [
                "Account has billing/credit issues — key stored but API calls may fail until resolved"
            ]
            if vr.reason_class == ValidationReason.QUOTA_OR_BILLING
            else [],
        }

    def _read_key_stdin(self) -> str:
        """Read candidate key from stdin with prompt suppression."""
        if sys.stdin.isatty():
            import getpass

            return getpass.getpass(f"Paste new {self.provider_label} API key: ")
        return sys.stdin.readline()
