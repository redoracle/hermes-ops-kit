"""Hermes Ops Kit — Assistant Task Store

SQLite-backed task lifecycle tracking.
Stores task metadata — NEVER raw secrets.

Schema (spec section 14):
  task_id, assistant_id, capability, status, created_at, updated_at,
  request_fingerprint, result_fingerprint, duration_ms, error, audit_path
"""

from __future__ import annotations

import os
import sqlite3
import time


DB_PATH = os.path.expanduser("~/.hermes/assistants/tasks.sqlite")


def _ensure_db() -> sqlite3.Connection:
    """Ensure the tasks database and table exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_tasks (
            task_id TEXT PRIMARY KEY,
            assistant_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL DEFAULT '',
            result_fingerprint TEXT,
            duration_ms INTEGER,
            error TEXT,
            audit_path TEXT
        )
    """)
    conn.commit()
    return conn


def create_task(
    task_id: str,
    assistant_id: str,
    capability: str,
    request_fingerprint: str = "",
) -> None:
    """Record a new task in the store."""
    conn = _ensure_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """INSERT OR REPLACE INTO assistant_tasks
           (task_id, assistant_id, capability, status, created_at, updated_at,
            request_fingerprint)
           VALUES (?, ?, ?, 'created', ?, ?, ?)""",
        (task_id, assistant_id, capability, now, now, request_fingerprint),
    )
    conn.commit()
    conn.close()


def update_task(
    task_id: str,
    status: str,
    result_fingerprint: str | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Update task status after completion."""
    conn = _ensure_db()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """UPDATE assistant_tasks
           SET status = ?, updated_at = ?,
               result_fingerprint = COALESCE(?, result_fingerprint),
               duration_ms = COALESCE(?, duration_ms),
               error = COALESCE(?, error)
           WHERE task_id = ?""",
        (status, now, result_fingerprint, duration_ms, error, task_id),
    )
    conn.commit()
    conn.close()


def get_task(task_id: str) -> dict | None:
    """Retrieve a task by ID."""
    conn = _ensure_db()
    row = conn.execute(
        "SELECT * FROM assistant_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    conn.close()
    if row:
        return {
            "task_id": row[0],
            "assistant_id": row[1],
            "capability": row[2],
            "status": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "request_fingerprint": row[6],
            "result_fingerprint": row[7],
            "duration_ms": row[8],
            "error": row[9],
            "audit_path": row[10],
        }
    return None


def list_tasks(limit: int = 20) -> list[dict]:
    """List recent tasks."""
    conn = _ensure_db()
    rows = conn.execute(
        "SELECT * FROM assistant_tasks ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [
        {
            "task_id": r[0],
            "assistant_id": r[1],
            "capability": r[2],
            "status": r[3],
            "created_at": r[4],
            "duration_ms": r[8],
        }
        for r in rows
    ]
