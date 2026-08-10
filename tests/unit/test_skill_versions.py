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
from planner.skill_sources import ensure_managed_panels_skills
from planner.skill_versions import (
    bind_worker_step_skills,
    capture_skill_version,
    reconcile_managed_skill_versions,
    reconcile_provisional_worker_step_bindings,
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
        mode=PromptDeliveryMode.run_when_free,
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


def test_reconciliation_records_every_current_managed_skill(tmp_path: Path) -> None:
    _, conn = _database(tmp_path)
    try:
        root = ensure_managed_panels_skills(tmp_path)
        expected = sorted(
            directory.name
            for directory in root.iterdir()
            if directory.is_dir() and (directory / "SKILL.md").is_file()
        )
        reconcile_managed_skill_versions(conn, tmp_path)
        actual = [
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT skill_name FROM managed_skill_versions ORDER BY skill_name"
            )
        ]
        assert actual == expected
    finally:
        conn.close()


def test_skill_reconciliation_failure_aborts_startup_in_test_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app

    db_path, conn = _database(tmp_path)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )

    def fail_reconciliation(*args: object, **kwargs: object) -> None:
        raise RuntimeError("skill capture failed")

    monkeypatch.setattr(
        "planner.core.server.reconcile_managed_skill_versions", fail_reconciliation
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))

    with pytest.raises(RuntimeError, match="skill capture failed"), TestClient(app):
        pass


def test_each_step_binds_exact_orientation_worker_and_specialist_versions(
    tmp_path: Path,
) -> None:
    _, conn = _database(tmp_path)
    try:
        root = ensure_managed_panels_skills(tmp_path)
        bind_worker_step_skills(conn, tmp_path, "message-one", "panels-worker-coding")
        first = _binding_rows(conn, "message-one")
        assert [(row["skill_role"], row["skill_name"], row["binding_status"]) for row in first] == [
            ("orientation", "panels", "provisional"),
            ("shared_worker", "panels-worker", "provisional"),
            ("specialist", "panels-worker-coding", "provisional"),
        ]

        specialist_path = root / "panels-worker-coding" / "SKILL.md"
        specialist_path.write_bytes(specialist_path.read_bytes() + b"\nnew stage guidance\n")
        bind_worker_step_skills(conn, tmp_path, "message-two", "panels-worker-coding")
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
        bind_worker_step_skills(conn, tmp_path, "queued-message", "panels-worker-coding")
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
                    mode=PromptDeliveryMode.run_when_free,
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


def test_queued_binding_becomes_final_with_its_later_prompt_event(
    tmp_path: Path,
) -> None:
    db_path, conn = _database(tmp_path)
    try:
        bind_worker_step_skills(conn, tmp_path, "queued-message", "panels-worker-coding")
    finally:
        conn.close()
    store = ConversationStore(str(db_path), integer_now=lambda: 1)

    async def exercise() -> None:
        await store.create_conversation(_resolved("conversation"))
        await store.append_delivered_prompt(
            "conversation", prompt=_prompt("queued-message"), model_change=None
        )

    asyncio.run(exercise())
    with connect(str(db_path)) as check:
        assert {row["binding_status"] for row in _binding_rows(check, "queued-message")} == {
            "final"
        }


def test_startup_reconciliation_resolves_or_expires_provisional_bindings(
    tmp_path: Path,
) -> None:
    db_path, conn = _database(tmp_path)
    store = ConversationStore(str(db_path), integer_now=lambda: 1)
    try:
        bind_worker_step_skills(conn, tmp_path, "recorded", "panels-worker-coding")
        bind_worker_step_skills(conn, tmp_path, "lost-queue", "panels-worker-coding")
    finally:
        conn.close()

    async def write_prompt_before_recreating_provisional_state() -> None:
        await store.create_conversation(_resolved("conversation"))
        await store.append_delivered_prompt(
            "conversation", prompt=_prompt("recorded"), model_change=None
        )

    asyncio.run(write_prompt_before_recreating_provisional_state())
    with connect(str(db_path)) as check:
        check.execute(
            "UPDATE worker_step_skill_bindings SET binding_status = 'provisional' "
            "WHERE sender_message_id = 'recorded'"
        )
        reconcile_provisional_worker_step_bindings(check)
        assert {row["binding_status"] for row in _binding_rows(check, "recorded")} == {"final"}
        assert _binding_rows(check, "lost-queue") == []


def test_each_managed_save_path_captures_the_exact_rendered_bytes(
    tmp_path: Path,
) -> None:
    _, conn = _database(tmp_path)
    registry = configured_worker_type_registry()
    try:
        worker_settings_service.save_skill(
            tmp_path,
            "panels",
            {"description": "Café", "markdown_body": "# One\n\n最後\n"},
            version_connection=conn,
        )
        worker_settings_service.save_chief_skill(
            tmp_path,
            {"description": "Chief café", "markdown_body": "# Two\n"},
            version_connection=conn,
        )
        worker_settings_service.save_specialist_skill(
            tmp_path,
            registry,
            "coding",
            {"description": "Coder café", "markdown_body": "# Three\n\nend\n"},
            version_connection=conn,
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


def test_capture_failure_restores_the_previous_skill_bytes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, conn = _database(tmp_path)
    root = ensure_managed_panels_skills(tmp_path)
    path = root / "panels" / "SKILL.md"
    before = path.read_bytes()
    atomic_replacements: list[bytes] = []
    real_atomic_replace = worker_settings_service._atomic_replace_bytes

    def observed_atomic_replace(target: Path, content: bytes) -> None:
        atomic_replacements.append(content)
        real_atomic_replace(target, content)

    def fail_capture(*args: object, **kwargs: object) -> str:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(worker_settings_service, "_atomic_replace_bytes", observed_atomic_replace)
    monkeypatch.setattr(worker_settings_service, "capture_skill_version", fail_capture)
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            worker_settings_service.save_skill(
                tmp_path,
                "panels",
                {"description": "changed"},
                version_connection=conn,
            )
        assert path.read_bytes() == before
        assert atomic_replacements[-1] == before
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("save_kind", "skill_name"),
    [
        ("chief", "panels-chief-of-staff"),
        ("specialist", "panels-worker-coding"),
    ],
)
def test_each_role_save_restores_its_file_if_capture_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    save_kind: str,
    skill_name: str,
) -> None:
    _, conn = _database(tmp_path)
    root = ensure_managed_panels_skills(tmp_path)
    path = root / skill_name / "SKILL.md"
    before = path.read_bytes()

    def fail_capture(*args: object, **kwargs: object) -> str:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(worker_settings_service, "capture_skill_version", fail_capture)
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            if save_kind == "chief":
                worker_settings_service.save_chief_skill(
                    tmp_path,
                    {"description": "changed"},
                    version_connection=conn,
                )
            else:
                worker_settings_service.save_specialist_skill(
                    tmp_path,
                    configured_worker_type_registry(),
                    "coding",
                    {"description": "changed", "markdown_body": "# changed\n"},
                    version_connection=conn,
                )
        assert path.read_bytes() == before
    finally:
        conn.close()
