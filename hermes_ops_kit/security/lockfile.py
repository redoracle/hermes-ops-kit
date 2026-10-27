"""Hermes Ops Kit — Per-Provider Locking

Provides advisory file locks (fcntl.flock) to prevent concurrent rotations
of the same provider.  Two terminals running `hermes-key-rotate --provider
openai` simultaneously will serialize — the second one blocks until the
first completes or times out.

Locks are stored at ~/.hermes/locks/<provider>.lock with chmod 600.
"""

from __future__ import annotations

import fcntl
import os
import time
import threading
from contextlib import contextmanager
from typing import Iterator
from .. import ops_config_io  # noqa: E402

LOCK_DIR = os.path.join(ops_config_io.HERMES_HOME, "locks")
_commit_owner = threading.local()


class LockTimeoutError(RuntimeError):
    """Raised when a provider lock cannot be acquired within the timeout."""


@contextmanager
def credential_commit_lock(timeout: float = 300.0) -> Iterator[object]:
    """Serialize active-vault transactions and complete env snapshots.

    Provider locks precede this lock. Rendering within a transaction intentionally
    reuses this thread's ownership; another thread/process must acquire the lock.
    """
    if getattr(_commit_owner, "pid", None) == os.getpid():
        yield _commit_owner.snapshot
        return
    with provider_lock("_credential-commit", timeout=timeout):
        _commit_owner.pid = os.getpid()
        _commit_owner.snapshot = object()
        try:
            yield _commit_owner.snapshot
        finally:
            _commit_owner.pid = None
            _commit_owner.snapshot = None


@contextmanager
def provider_lock(provider: str, timeout: float = 30.0) -> Iterator[None]:
    """Acquire an exclusive advisory lock for *provider* rotation.

    Uses fcntl.flock on a lock file at ~/.hermes/locks/<provider>.lock.
    Blocks for up to *timeout* seconds before raising LockTimeoutError.

    Usage:
        with provider_lock("openai"):
            # safe to rotate openai keys
            rotator.rotate(...)
    """
    os.makedirs(LOCK_DIR, mode=0o700, exist_ok=True)
    lock_path = os.path.join(LOCK_DIR, f"{provider}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                time.sleep(min(0.1, remaining))
        if not acquired:
            raise LockTimeoutError(
                f"Could not acquire rotation lock for '{provider}' "
                f"within {timeout}s — is another rotation in progress?"
            )
        yield
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
