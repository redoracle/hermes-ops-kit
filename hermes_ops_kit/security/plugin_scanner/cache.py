"""Hermes Ops Kit — Plugin Scanner: SHA-256 Cache.

SQLite-backed scan cache for plugin security results.
Computes git_commit_hash + file_tree_sha for cache keying.
Invalidates on: new commit, changed local files, scanner version
change, TTL expiry, or forced rescan.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import sqlite3
import time
from typing import Any
from hermes_ops_kit import ops_config_io  # noqa: E402


# ── Constants ────────────────────────────────────────────────────────

CACHE_DB_PATH = os.path.join(ops_config_io.HERMES_HOME, "ops-kit/plugin_scanner_cache.db")
SCANNER_VERSION = "0.2.2"
DEFAULT_TTL_HOURS = 168  # 7 days


# ── SHA Computation ──────────────────────────────────────────────────


def compute_git_commit_hash(plugin_path: str) -> str:
    """Get the current HEAD commit hash for a git repository.

    Returns empty string if not a git repo or git not available.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=plugin_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def compute_file_tree_sha(plugin_path: str) -> str:
    """Compute a deterministic SHA-256 over all files in the plugin directory.

    Sorts files by path for deterministic ordering, hashes each file's
    content, then hashes the concatenation of all file hashes.
    Skips .git directory, __pycache__, .pyc files, and node_modules.
    """
    if not os.path.isdir(plugin_path):
        return ""

    file_hashes: list[str] = []
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox"}

    for root, dirs, files in os.walk(plugin_path):
        # Prune skip directories
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for fname in sorted(files):
            if fname.endswith((".pyc", ".pyo", ".DS_Store")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "rb") as f:
                    content = f.read()
                rel = os.path.relpath(fpath, plugin_path)
                file_hash = hashlib.sha256(rel.encode() + b"\x00" + content).hexdigest()
                file_hashes.append(file_hash)
            except (OSError, PermissionError):
                continue

    if not file_hashes:
        return hashlib.sha256(b"empty-tree").hexdigest()

    # Sort file hashes for determinism (already sorted by filename, but hash them too)
    combined = "\n".join(sorted(file_hashes))
    return hashlib.sha256(combined.encode()).hexdigest()


def compute_cache_key(plugin_path: str) -> tuple[str, str]:
    """Return (git_commit_hash, file_tree_sha) for a plugin path."""
    return (
        compute_git_commit_hash(plugin_path),
        compute_file_tree_sha(plugin_path),
    )


# ── Database ─────────────────────────────────────────────────────────


def _ensure_db(db_path: str = CACHE_DB_PATH) -> sqlite3.Connection:
    """Open (or create) the cache database and ensure schema exists."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        os.chmod(db_path, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Create or migrate the cache table schema."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_scans (
            plugin_name     TEXT PRIMARY KEY,
            plugin_path     TEXT NOT NULL,
            git_remote      TEXT,
            git_commit_hash TEXT,
            file_tree_sha   TEXT,
            scan_result     TEXT NOT NULL DEFAULT 'clean',
            risk_level      TEXT,
            findings        TEXT DEFAULT '[]',
            score           REAL DEFAULT 0.0,
            scanner_version TEXT,
            scanned_at      TEXT,
            expires_at      TEXT,
            scan_context    TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_plugin_scans_expires
        ON plugin_scans(expires_at)
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(plugin_scans)")}
    if "scan_context" not in columns:
        conn.execute(
            "ALTER TABLE plugin_scans ADD COLUMN scan_context TEXT NOT NULL DEFAULT ''"
        )
    conn.commit()


# ── Cache Operations ─────────────────────────────────────────────────


def cache_lookup(
    plugin_name: str,
    plugin_path: str,
    *,
    force: bool = False,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    scan_context: str = "",
) -> dict[str, Any] | None:
    """Look up a plugin in the cache.

    Returns the cached scan result dict if valid, or None if cache miss.
    A cache hit requires: matching commit hash, matching file tree SHA,
    non-expired TTL, matching scanner version (unless forced).
    """
    if force:
        return None
    if ttl_hours <= 0:
        return None

    git_hash, tree_sha = compute_cache_key(plugin_path)
    if not git_hash and not tree_sha:
        return None

    conn = _ensure_db()
    try:
        row = conn.execute(
            "SELECT * FROM plugin_scans WHERE plugin_name = ?",
            (plugin_name,),
        ).fetchone()

        if row is None:
            return None

        # Check scanner version
        if row["scanner_version"] != SCANNER_VERSION:
            return None
        if row["scan_context"] != scan_context:
            return None

        # Check commit hash changed
        if git_hash and row["git_commit_hash"] != git_hash:
            return None

        # Check file tree changed
        if tree_sha and row["file_tree_sha"] != tree_sha:
            return None

        # Check TTL expiry (both stored expiry and caller-requested TTL)
        if row["expires_at"]:
            try:
                expires = calendar.timegm(
                    time.strptime(row["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
                )
                if time.time() > expires:
                    return None
            except (ValueError, OverflowError):
                pass

        # Also check if entry is older than requested TTL
        if row["scanned_at"] and ttl_hours > 0:
            try:
                scanned = calendar.timegm(
                    time.strptime(row["scanned_at"], "%Y-%m-%dT%H:%M:%SZ")
                )
                max_age = ttl_hours * 3600
                if time.time() > scanned + max_age:
                    return None
            except (ValueError, OverflowError):
                pass

        # Cache hit — return stored result
        findings_raw = row["findings"] or "[]"
        try:
            findings = json.loads(findings_raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(findings, list) or not all(
            isinstance(finding, dict) for finding in findings
        ):
            return None

        return {
            "plugin_name": row["plugin_name"],
            "plugin_path": row["plugin_path"],
            "git_remote": row["git_remote"],
            "git_commit_hash": row["git_commit_hash"],
            "file_tree_sha": row["file_tree_sha"],
            "scan_result": row["scan_result"],
            "risk_level": row["risk_level"],
            "findings": findings,
            "score": row["score"],
            "scanner_version": row["scanner_version"],
            "scanned_at": row["scanned_at"],
            "expires_at": row["expires_at"],
            "cache_hit": True,
            "scan_context": row["scan_context"],
        }
    finally:
        conn.close()


def cache_store(
    plugin_name: str,
    plugin_path: str,
    scan_result: str,
    risk_level: str,
    findings: list[dict[str, Any]],
    score: float = 0.0,
    git_remote: str = "",
    ttl_hours: int = DEFAULT_TTL_HOURS,
    scan_context: str = "",
) -> None:
    """Store a scan result in the cache."""
    git_hash, tree_sha = compute_cache_key(plugin_path)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    expires = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + ttl_hours * 3600),
    )

    conn = _ensure_db()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO plugin_scans
                (plugin_name, plugin_path, git_remote, git_commit_hash,
                 file_tree_sha, scan_result, risk_level, findings, score,
                 scanner_version, scanned_at, expires_at, scan_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plugin_name,
                plugin_path,
                git_remote,
                git_hash,
                tree_sha,
                scan_result,
                risk_level,
                json.dumps(findings, ensure_ascii=False),
                score,
                SCANNER_VERSION,
                now,
                expires,
                scan_context,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def cache_clear(plugin_name: str | None = None) -> int:
    """Clear cache entries. If plugin_name is None, clear all. Returns count."""
    conn = _ensure_db()
    try:
        if plugin_name:
            cur = conn.execute(
                "DELETE FROM plugin_scans WHERE plugin_name = ?",
                (plugin_name,),
            )
        else:
            cur = conn.execute("DELETE FROM plugin_scans")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def cache_stats() -> dict[str, Any]:
    """Return cache statistics."""
    conn = _ensure_db()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM plugin_scans").fetchone()
        expired = conn.execute(
            "SELECT COUNT(*) as c FROM plugin_scans WHERE expires_at < datetime('now')"
        ).fetchone()
        return {
            "db_path": CACHE_DB_PATH,
            "total_entries": total["c"] if total else 0,
            "expired_entries": expired["c"] if expired else 0,
            "scanner_version": SCANNER_VERSION,
        }
    finally:
        conn.close()


def cache_list() -> list[dict[str, Any]]:
    """List all cache entries."""
    conn = _ensure_db()
    try:
        rows = conn.execute(
            "SELECT plugin_name, plugin_path, scan_result, risk_level, "
            "scanned_at, expires_at FROM plugin_scans ORDER BY scanned_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
