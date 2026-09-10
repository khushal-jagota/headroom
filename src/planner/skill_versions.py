"""Durable versions of managed skills and the worker steps that selected them.

Nothing here is on a screen: these rows are the history behind what a worker was sent,
kept for later reading rather than watched as they land. So every commit here is a quiet
one. What a reader does see already announces itself — a skill editor announces its file
change, and the conversation event that settles a queued binding is written and announced
by the conversation record, not here.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Final

from planner.core.db import commit_without_change_signal
from planner.skill_sources import ensure_managed_panels_skills, panels_skill_root

SKILL_FILE_NAME: Final = "SKILL.md"
ORIENTATION_SKILL_NAME: Final = "panels"
SHARED_WORKER_SKILL_NAME: Final = "panels-worker"

ORIENTATION_ROLE: Final = "orientation"
SHARED_WORKER_ROLE: Final = "shared_worker"
SPECIALIST_ROLE: Final = "specialist"


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


def reconcile_managed_skill_versions(
    conn: sqlite3.Connection,
    configured_database_parent: Path | str,
) -> None:
    """Record every current managed skill before worker loops can send a step."""
    root = ensure_managed_panels_skills(
        configured_database_parent,
        packaged_skill_root=panels_skill_root(),
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        for directory in sorted(root.iterdir(), key=lambda path: path.name):
            skill_path = directory / SKILL_FILE_NAME
            if directory.is_dir() and not directory.name.startswith(".") and skill_path.is_file():
                _record_skill_version(conn, directory.name, skill_path.read_bytes())
        commit_without_change_signal(conn)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def bind_worker_step_skills(
    conn: sqlite3.Connection,
    configured_database_parent: Path | str,
    sender_message_id: str,
    specialist_skill_name: str,
) -> None:
    """Bind one worker-step message to its exact three managed skill versions."""
    root = ensure_managed_panels_skills(
        configured_database_parent,
        packaged_skill_root=panels_skill_root(),
    )
    selected = (
        (ORIENTATION_ROLE, ORIENTATION_SKILL_NAME),
        (SHARED_WORKER_ROLE, SHARED_WORKER_SKILL_NAME),
        (SPECIALIST_ROLE, specialist_skill_name),
    )
    contents = tuple(
        (role, skill_name, (root / skill_name / SKILL_FILE_NAME).read_bytes())
        for role, skill_name in selected
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        for role, skill_name, content in contents:
            version_id = _record_skill_version(conn, skill_name, content)
            conn.execute(
                "INSERT INTO worker_step_skill_bindings "
                "(sender_message_id, skill_role, skill_version_id, binding_status) "
                "VALUES (?, ?, ?, 'provisional')",
                (sender_message_id, role, version_id),
            )
        commit_without_change_signal(conn)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def delete_worker_step_skill_bindings(
    conn: sqlite3.Connection,
    sender_message_id: str,
) -> None:
    """Remove the tentative bindings for a worker step that reached no worker."""
    conn.execute(
        "DELETE FROM worker_step_skill_bindings WHERE sender_message_id = ?",
        (sender_message_id,),
    )


def settle_worker_step_skill_bindings(
    conn: sqlite3.Connection,
    sender_message_id: str,
    *,
    delivered: bool,
) -> None:
    """Settle tentative bindings inside the conversation event transaction."""
    if delivered:
        conn.execute(
            "UPDATE worker_step_skill_bindings SET binding_status = 'final' "
            "WHERE sender_message_id = ? AND binding_status = 'provisional'",
            (sender_message_id,),
        )
        return
    delete_worker_step_skill_bindings(conn, sender_message_id)


def reconcile_provisional_worker_step_bindings(conn: sqlite3.Connection) -> None:
    """Resolve bindings left provisional when the previous process stopped."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        sender_message_ids = tuple(
            str(row["sender_message_id"])
            for row in conn.execute(
                "SELECT DISTINCT sender_message_id FROM worker_step_skill_bindings "
                "WHERE binding_status = 'provisional'"
            ).fetchall()
        )
        for sender_message_id in sender_message_ids:
            terminal = conn.execute(
                "SELECT kind, payload FROM conversation_events "
                "WHERE kind IN "
                "('prompt', 'prompt_delivery_refused', 'prompt_delivery_uncertain', "
                "'prompt_discarded') "
                "AND json_extract(payload, '$.sender_message_id') = ? LIMIT 1",
                (sender_message_id,),
            ).fetchone()
            if terminal is not None and str(terminal["kind"]) == "prompt":
                settle_worker_step_skill_bindings(conn, sender_message_id, delivered=True)
            else:
                # Held prompts live only in the old process. With no prompt row there is
                # no durable proof that this step ran, so an unresolved binding expires.
                settle_worker_step_skill_bindings(conn, sender_message_id, delivered=False)
        commit_without_change_signal(conn)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
