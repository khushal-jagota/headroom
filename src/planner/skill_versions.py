"""Immutable history of managed skill text saved by the skill editor."""

from __future__ import annotations

import hashlib
import sqlite3

from planner.core.db import commit_without_change_signal


def _version_id(skill_name: str, content_sha256: str) -> str:
    identity = hashlib.sha256(
        skill_name.encode("utf-8") + b"\0" + content_sha256.encode("ascii")
    ).hexdigest()
    return f"skill_version_{identity}"


def _record_skill_version(
    conn: sqlite3.Connection,
    skill_name: str,
    content: bytes,
) -> str:
    content_sha256 = hashlib.sha256(content).hexdigest()
    version_id = _version_id(skill_name, content_sha256)
    conn.execute(
        "INSERT INTO managed_skill_versions (id, skill_name, content_sha256, content) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(skill_name, content_sha256) DO NOTHING",
        (version_id, skill_name, content_sha256, content),
    )
    row = conn.execute(
        "SELECT id, content FROM managed_skill_versions "
        "WHERE skill_name = ? AND content_sha256 = ?",
        (skill_name, content_sha256),
    ).fetchone()
    if row is None or bytes(row["content"]) != content:
        raise RuntimeError(f"managed skill hash collision for {skill_name}")
    return str(row["id"])


def capture_skill_version(
    conn: sqlite3.Connection,
    skill_name: str,
    content: bytes,
) -> str:
    """Record complete skill bytes, or reuse the matching immutable version."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        version_id = _record_skill_version(conn, skill_name, content)
        commit_without_change_signal(conn)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return version_id
