"""Legacy rotation phase/checkpoint vocabulary retained for import compatibility.

Normal rotations run in the provider rotators. Automatic checkpoint recovery is
unsupported; RotationRunner.execute refuses rather than recording fictitious
credential/deployment effects. Existing checkpoint files are not deleted.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from .. import ops_config_io  # noqa: E402

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = os.path.join(ops_config_io.HERMES_HOME, "rotation_checkpoints")


class RotationPhase(str, Enum):
    """Phases of a key rotation, in sequential order."""

    # Active states
    STARTED = "started"
    LOCK_ACQUIRED = "lock_acquired"
    PREFLIGHT_OK = "preflight_ok"
    OLD_KEY_FINGERPRINTED = "old_key_fingerprinted"
    CANDIDATE_CREATED_OR_RECEIVED = "candidate_created_or_received"
    CANDIDATE_VALIDATED = "candidate_validated"
    SECRET_STAGED = "secret_staged"
    SMOKE_TEST_PASSED = "smoke_test_passed"
    ENV_RENDERED_ATOMICALLY = "env_rendered_atomically"
    DEPLOYMENT_RELOADED = "deployment_reloaded"
    POST_DEPLOY_HEALTH_OK = "post_deploy_health_ok"
    OLD_KEY_REVOKED_OR_DEFERRED = "old_key_revoked_or_deferred"
    AUDIT_WRITTEN = "audit_written"
    COMPLETED = "completed"

    # Terminal failure states
    FAILED_PREFLIGHT = "failed_preflight"
    FAILED_CREATE = "failed_create"
    FAILED_VALIDATE = "failed_validate"
    FAILED_STORE = "failed_store"
    FAILED_SMOKE_TEST = "failed_smoke_test"
    FAILED_ENV_RENDER = "failed_env_render"
    FAILED_RELOAD = "failed_reload"
    FAILED_HEALTH_CHECK = "failed_health_check"
    FAILED_REVOKE = "failed_revoke"
    FAILED_AUDIT = "failed_audit"
    ROLLED_BACK = "rolled_back"
    MANUAL_ACTION_REQUIRED = "manual_action_required"

    @property
    def is_terminal(self) -> bool:
        """True if this phase is a terminal state (success or failure)."""
        return self in (
            RotationPhase.COMPLETED,
            RotationPhase.FAILED_PREFLIGHT,
            RotationPhase.FAILED_CREATE,
            RotationPhase.FAILED_VALIDATE,
            RotationPhase.FAILED_STORE,
            RotationPhase.FAILED_SMOKE_TEST,
            RotationPhase.FAILED_ENV_RENDER,
            RotationPhase.FAILED_RELOAD,
            RotationPhase.FAILED_HEALTH_CHECK,
            RotationPhase.FAILED_REVOKE,
            RotationPhase.FAILED_AUDIT,
            RotationPhase.ROLLED_BACK,
            RotationPhase.MANUAL_ACTION_REQUIRED,
        )


@dataclass
class RotationState:
    """Checkpoint-able rotation state.

    Tracks every phase transition with timestamps.  Checkpoint files are
    written to ~/.hermes/rotation_checkpoints/<provider>.json after each
    phase, enabling crash recovery via `hermes-key-rotate resume`.

    NEVER stores raw key material in checkpoints — only fingerprints.
    """

    provider: str
    mode: str = "manual-new-key"  # manual-new-key, admin-hybrid, bootstrap, emergency
    phase: RotationPhase = RotationPhase.STARTED
    run_id: str = field(default_factory=lambda: str(int(time.time())))
    started_at: float = field(default_factory=time.time)

    # Key fingerprints (safe for logging and checkpoints)
    old_fingerprint: str | None = None
    new_fingerprint: str | None = None
    old_key_id: str | None = None
    new_key_id: str | None = None

    # Phase tracking
    phase_history: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def transition(self, next_phase: RotationPhase) -> None:
        """Record a phase transition with timestamp."""
        entry = {
            "from": self.phase.value,
            "to": next_phase.value,
            "ts": time.time(),
        }
        self.phase = next_phase
        self.phase_history.append(entry)
        self._save_checkpoint()

    def record_error(self, error: str, phase: RotationPhase | None = None) -> None:
        """Record an error and transition to the appropriate failure phase."""
        self.errors.append(error)
        if phase:
            self.phase = phase
        self._save_checkpoint()

    def record_warning(self, warning: str) -> None:
        """Record a non-fatal warning."""
        self.warnings.append(warning)

    def _checkpoint_path(self) -> str:
        os.makedirs(CHECKPOINT_DIR, mode=0o700, exist_ok=True)
        return os.path.join(CHECKPOINT_DIR, f"{self.provider}.json")

    def _save_checkpoint(self) -> None:
        """Persist current state to a checkpoint file (fingerprints only)."""
        try:
            data = {
                "provider": self.provider,
                "mode": self.mode,
                "phase": self.phase.value,
                "run_id": self.run_id,
                "started_at": self.started_at,
                "old_fingerprint": self.old_fingerprint,
                "new_fingerprint": self.new_fingerprint,
                "old_key_id": self.old_key_id,
                "new_key_id": self.new_key_id,
                "phase_history": self.phase_history,
                "errors": self.errors,
                "warnings": self.warnings,
            }
            from ..env.atomic_write import atomic_write_json

            path = self._checkpoint_path()
            atomic_write_json(path, data)
        except OSError as exc:
            logger.error(
                "Failed to save rotation checkpoint for %s at %s: %s",
                self.provider,
                self._checkpoint_path(),
                exc,
            )

    def delete_checkpoint(self) -> None:
        """Remove the checkpoint file after successful completion."""
        try:
            os.remove(self._checkpoint_path())
        except OSError as exc:
            logger.error(
                "Failed to delete rotation checkpoint for %s at %s: %s",
                self.provider,
                self._checkpoint_path(),
                exc,
            )

    @staticmethod
    def load_checkpoint(provider: str) -> RotationState | None:
        """Load a saved checkpoint, or None if no checkpoint exists."""
        path = os.path.join(CHECKPOINT_DIR, f"{provider}.json")
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        state = RotationState(
            provider=data["provider"],
            mode=data.get("mode", "manual-new-key"),
            run_id=data.get("run_id", ""),
            started_at=data.get("started_at", 0),
            old_fingerprint=data.get("old_fingerprint"),
            new_fingerprint=data.get("new_fingerprint"),
            old_key_id=data.get("old_key_id"),
            new_key_id=data.get("new_key_id"),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )
        state.phase = RotationPhase(data["phase"])
        state.phase_history = data.get("phase_history", [])
        return state


class RotationRunner:
    """Reserved recovery interface; execute refuses unsupported recovery."""

    def __init__(self, rotator: Any, backend: Any, state: RotationState) -> None:
        self.rotator = rotator
        self.backend = backend
        self.state = state

    def execute(self, candidate_key: str | None = None) -> dict:
        """Refuse recovery until checkpoints drive real credential operations."""
        return {
            "ok": False, "error_type": "UnsupportedOperation",
            "error": "Rotation recovery is not supported; no credential operations performed",
        }
