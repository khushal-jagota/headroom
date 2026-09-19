"""Immutable skill versions and their worker-step binding lifecycle."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    PromptDeliveryRefusedEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.core.db import connect, create_schema
from planner.managed_skills import managed_skills_home, read_skill_source, write_skill_source
from planner.skill_versions import (
    bind_worker_step_skills,
    capture_skill_version,
)
from planner.worker_settings import service as worker_settings_service
from planner.worker_types.configuration import configured_worker_type_registry


def _database(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    path = tmp_path / "skills.db"
    conn = connect(str(path))
    create_schema(conn)
    return path, conn


def _binding_rows(conn: sqlite3.Connection, sender_message_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT b.skill_role, b.binding_status, v.skill_name, v.content "
        "FROM worker_step_skill_bindings b JOIN managed_skill_versions v "
        "ON v.id = b.skill_version_id WHERE b.sender_message_id = ? "
        "ORDER BY b.skill_role",
        (sender_message_id,),
    ).fetchall()


def _resolved(conversation_id: str) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id=conversation_id,
        backend_key=ConversationBackendKey.hermes,
        model="model",
        reasoning_effort=None,
        role_materials=None,
        workspace_folder=Path("/tmp/workspace"),
        access=ConversationAccess.full,
    )


def _prompt(sender_message_id: str) -> PromptEventPayload:
    return PromptEventPayload(
        content=text_message_content("step"),
        sender_label="loop",
        mode=PromptDeliveryMode.queue,
        sender_message_id=sender_message_id,
    )


def test_capture_preserves_raw_bytes_and_reuses_the_same_hash(tmp_path: Path) -> None:
    _, conn = _database(tmp_path)
    try:
        content = "# Café\r\n\r\n最後の行\n".encode()
        first = capture_skill_version(conn, "panels-test", content)
        second = capture_skill_version(conn, "panels-test", content)

        assert first == second
        row = conn.execute(
            "SELECT content, content_sha256 FROM managed_skill_versions WHERE id = ?",
            (first,),
        ).fetchone()
        assert bytes(row["content"]) == content
        assert len(str(row["content_sha256"])) == 64
        assert (
            conn.execute(
                "SELECT count(*) FROM managed_skill_versions WHERE skill_name = 'panels-test'"
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()


def test_versions_are_immutable_in_the_database(tmp_path: Path) -> None:
    _, conn = _database(tmp_path)
    try:
        version_id = capture_skill_version(conn, "panels-test", b"one")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE managed_skill_versions SET content = ? WHERE id = ?",
                (b"two", version_id),
            )
    finally:
        conn.close()


def test_each_step_binds_exact_orientation_worker_and_specialist_versions(
    tmp_path: Path,
) -> None:
    _, conn = _database(tmp_path)
    try:
        bind_worker_step_skills(conn, "message-one", "panels-worker-coding")
        first = _binding_rows(conn, "message-one")
        assert [(row["skill_role"], row["skill_name"], row["binding_status"]) for row in first] == [
            ("orientation", "panels", "provisional"),
            ("shared_worker", "panels-worker", "provisional"),
            ("specialist", "panels-worker-coding", "provisional"),
        ]

        # The row is what a bind reads, so the change that must show up is a change to it.
        write_skill_source(
            conn,
            "panels-worker-coding",
            read_skill_source(conn, "panels-worker-coding") + "\nnew stage guidance\n",
            now=1,
        )
        bind_worker_step_skills(conn, "message-two", "panels-worker-coding")
        second = _binding_rows(conn, "message-two")
        first_versions = {row["skill_role"]: bytes(row["content"]) for row in first}
        second_versions = {row["skill_role"]: bytes(row["content"]) for row in second}
        assert first_versions["orientation"] == second_versions["orientation"]
        assert first_versions["shared_worker"] == second_versions["shared_worker"]
        assert first_versions["specialist"] != second_versions["specialist"]
    finally:
        conn.close()


@pytest.mark.parametrize("terminal", ["refused", "discarded"])
def test_queued_binding_is_removed_if_its_later_outcome_did_not_run(
    tmp_path: Path, terminal: str
) -> None:
    db_path, conn = _database(tmp_path)
    try:
        bind_worker_step_skills(conn, "queued-message", "panels-worker-coding")
    finally:
        conn.close()
    store = ConversationStore(str(db_path), integer_now=lambda: 1)

    async def exercise() -> None:
        await store.create_conversation(_resolved("conversation"))
        if terminal == "refused":
            await store.append_event(
                "conversation",
                PromptDeliveryRefusedEventPayload(
                    content=text_message_content("step"),
                    sender_label="loop",
                    mode=PromptDeliveryMode.queue,
                    refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed,
                    sender_message_id="queued-message",
                ),
            )
        else:
            await store.append_event(
                "conversation",
                PromptDiscardedEventPayload(
                    content=text_message_content("step"),
                    sender_label="loop",
                    sender_message_id="queued-message",
                ),
            )

    asyncio.run(exercise())
    with connect(str(db_path)) as check:
        assert _binding_rows(check, "queued-message") == []


def test_each_managed_save_path_captures_the_exact_rendered_bytes(
    tmp_path: Path,
) -> None:
    _, conn = _database(tmp_path)
    registry = configured_worker_type_registry()
    try:
        worker_settings_service.save_skill(
            conn,
            "panels",
            {"description": "Café", "markdown_body": "# One\n\n最後\n"},
            now=1,
            database_parent=tmp_path,
        )
        worker_settings_service.save_chief_skill(
            conn,
            {"description": "Chief café", "markdown_body": "# Two\n"},
            now=1,
            database_parent=tmp_path,
        )
        worker_settings_service.save_specialist_skill(
            conn,
            registry,
            "coding",
            {"description": "Coder café", "markdown_body": "# Three\n\nend\n"},
            now=1,
            database_parent=tmp_path,
        )
        for skill_name in (
            "panels",
            "panels-chief-of-staff",
            "panels-worker-coding",
        ):
            stored = conn.execute(
                "SELECT content FROM managed_skill_versions WHERE skill_name = ?",
                (skill_name,),
            ).fetchone()
            assert (
                bytes(stored["content"])
                == (tmp_path / "skills" / skill_name / "SKILL.md").read_bytes()
            )
    finally:
        conn.close()


def test_owner_edit_stands_in_the_row_and_the_file_when_version_history_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row is the authority, and it commits before the history is recorded.

    So a failure recording the version history cannot take the owner's edit back: the
    edit stands in the row, and in the copy agents read.
    """
    _, conn = _database(tmp_path)
    path = managed_skills_home(tmp_path) / "panels" / "SKILL.md"
    before = path.read_bytes()

    def fail_capture(*args: object, **kwargs: object) -> str:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(worker_settings_service, "capture_skill_version", fail_capture)
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            worker_settings_service.save_skill(
                conn,
                "panels",
                {"description": "changed"},
                now=1,
                database_parent=tmp_path,
            )
        edited = read_skill_source(conn, "panels")
        assert edited != before.decode("utf-8")
        assert 'description: "changed"' in edited
        assert path.read_bytes() == edited.encode("utf-8")
    finally:
        conn.close()
