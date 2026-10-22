"""Deployment evidence must reach the backend before recovery counts as resumed."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.test_conversation_system import _Harness, _run, _start

from planner.conversation.contracts import PromptDeliveryQueued
from planner.conversation.deploy_interruptions.contracts import CapturedTurn, CaptureManifest
from planner.conversation.deploy_interruptions.data import read_summary
from planner.conversation.deploy_interruptions.files import write_manifest
from planner.conversation.message_content import text_message_content
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.runtime import conversation_start
from planner.runtime.worker_step_readiness_loop import (
    WorkerStepStartResult,
    start_ready_worker_step,
)
from planner.tickets.worker_restart import restart_worker
from planner.worker_types.configuration import configured_worker_type_registry


def _capture(
    path: Path,
    *,
    occurrence: str | None = None,
    epoch: str | None = None,
    owner_id: str | None = None,
) -> CaptureManifest:
    with connect(str(path)) as conn:
        row = conn.execute(
            "SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences "
            "WHERE conversation_id='c' AND turn_number=1"
        ).fetchone()
    return CaptureManifest(
        "release",
        "stop",
        123,
        "captured",
        None,
        (
            CapturedTurn(
                "c",
                1,
                occurrence or str(row[0]),
                epoch or str(row[1]),
                "intent",
                "Interrupted worker",
                "ticket" if owner_id else "unknown",
                owner_id,
                7 if owner_id else None,
                "out" if owner_id else None,
            ),
        ),
    )


def _entry(path: Path) -> dict[str, object]:
    with connect(str(path)) as conn:
        return dict(read_summary(conn)["accounts"][0]["entries"][0])


def _ticket(path: Path) -> None:
    with connect(str(path)) as conn:
        conn.execute(
            "INSERT INTO "
            "tickets(id,title,worker_type,employee_backend,stage,ceiling,field_values,cr"
            "eated_at,updated_at,conversation_id,worker_step_claim,worker_step_claim_rev"
            "ision) VALUES "
            "('t','Worker','coding','hermes','needs_implementation','needs_implementatio"
            "n','{}',0,0,'c','out',7)"
        )
        conn.commit()


@pytest.mark.parametrize(
    "target,delivery",
    [
        ("c", "started"),
        ("fresh", "started"),
        ("fresh", "refused"),
        ("fresh", "unknown"),
        ("fresh", "context_missing"),
    ],
)
def test_notice_actual_wire_receipt_and_fresh_worker_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, delivery: str
) -> None:
    path = tmp_path / "db.sqlite"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    original = _Harness(path)

    async def exercise() -> None:
        await _start(original)
        await original.store.reserve_turn("c")
        _ticket(path)
        manifest = _capture(path, owner_id="t")
        write_manifest(path, manifest)
        await original.system.shutdown()
        restarted = _Harness(path)
        try:
            assert restarted.spawned_conversation_ids == []
            assert _entry(path)["recovery_state"] == "awaiting_action"
            actual_restart = target == "fresh" and delivery == "started"
            with connect(str(path)) as conn:
                assert tuple(
                    conn.execute(
                        "SELECT worker_step_claim,worker_step_claim_revision FROM tickets WHERE "
                        "id='t'"
                    ).fetchone()
                ) == ("errored", 8)
                # Ordinary authorized Restart Worker produces a fresh current link.
                if not actual_restart:
                    conn.execute("UPDATE tickets SET conversation_id=? WHERE id='t'", (target,))
                conn.execute("UPDATE tickets SET employee_launch_model='a-model' WHERE id='t'")
                conn.commit()
            if target == "fresh" and not actual_restart:
                await _start(restarted, target)
            backend = restarted.backend(target)
            backend.spawn_fails = delivery == "refused"
            backend.write_fails = delivery == "unknown"
            backend.composed_content_delivered = delivery != "context_missing"
            if actual_restart:
                monkeypatch.setattr(conversation_start, "new_conversation_id", lambda: target)

                async def start_step() -> WorkerStepStartResult:
                    return await start_ready_worker_step(
                        "t",
                        connect_database=lambda: connect(str(path)),
                        conversation_system=restarted.system,
                        worker_type_registry=configured_worker_type_registry(),
                        now=lambda: 10,
                    )

                conn = connect(str(path))
                try:
                    result = await restart_worker(
                        restarted.system,
                        conn,
                        OWNER_PRINCIPAL,
                        "t",
                        write_employee_configuration=None,
                        start_worker_step=start_step,
                        now=lambda: 10,
                    )
                    assert result["started"] is True
                    assert result["conversation_id"] == target
                    assert result["killed_conversation_id"] == "c"
                finally:
                    conn.close()
            else:
                await restarted.system.send(
                    target,
                    text_message_content("Inspect current work"),
                    sender_label="Chief",
                    sender=CHIEF_PRINCIPAL,
                    recipient=Principal(PrincipalKind.ticket, "t"),
                )
            entry = _entry(path)
            assert entry["recovery_state"] == (
                "resumed" if delivery == "started" else "unable_to_resume"
            )
            if delivery == "started":
                wire = backend.written_texts()[0]
                assert "[Panels deployment interruption context]" in wire
                if not actual_restart:
                    assert wire.startswith("[Authenticated Panels reply requirement]")
                    assert wire.index("[Panels deployment interruption context]") < wire.index(
                        "Inspect current work"
                    )
                    assert backend.writes[0].sender_content == text_message_content(
                        "Inspect current work"
                    )
                else:
                    assert "Work ticket" in wire
                    assert "Panels deployment interruption context" not in str(
                        backend.writes[0].sender_content
                    )
            with connect(str(path)) as conn:
                receipt = conn.execute(
                    "SELECT conversation_id,turn_number,status FROM deploy_interruption_deliveries"
                ).fetchone()
                assert tuple(receipt) == (
                    target,
                    2 if target == "c" else 1,
                    "context_not_delivered" if delivery == "context_missing" else delivery,
                )
            if delivery == "context_missing":
                backend.composed_content_delivered = True
                queued = await restarted.system.send(
                    target, text_message_content("next authorized action"), sender_label="owner"
                )
                assert isinstance(queued, PromptDeliveryQueued)
                assert _entry(path)["recovery_state"] == "unable_to_resume"
                await restarted.complete_turn(target)
                assert _entry(path)["recovery_state"] == "resumed"
            if delivery == "unknown":
                with connect(str(path)) as conn:
                    conn.execute("UPDATE tickets SET stage='done' WHERE id='t'")
                    conn.commit()
                assert _entry(path)["recovery_state"] == "closed"
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_occurrence_mismatch_and_completed_turn_stay_historical(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    original = _Harness(path)

    async def exercise() -> None:
        await _start(original)
        await original.system.send("c", text_message_content("work"), sender_label="owner")
        manifest = _capture(path)
        await original.complete_turn("c")
        write_manifest(path, manifest)
        await original.system.shutdown()
        first = _Harness(path)
        try:
            assert _entry(path)["recovery_state"] == "historical"
            assert any(str(f.status) == "completed" for f in await first.store.lifecycle_facts("c"))
            assert first.spawned_conversation_ids == []
        finally:
            await first.system.shutdown()
        # Snapshot reuse of the same numerical turn cannot attribute another occurrence.
        with connect(str(path)) as conn:
            conn.execute(
                "UPDATE conversation_lifecycle_facts SET status='intent' WHERE kind='turn'"
            )
            conn.execute("DELETE FROM deploy_interruptions")
            conn.execute("UPDATE conversation_turn_occurrences SET occurrence_id='new-occurrence'")
            conn.commit()
        second = _Harness(path)
        try:
            assert _entry(path)["recovery_state"] == "historical"
            assert second.spawned_conversation_ids == []
        finally:
            await second.system.shutdown()

    _run(exercise)


def test_legacy_epoch_and_erased_candidate_keep_exact_historical_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil
    from dataclasses import replace

    from tests.unit.test_db import _migration_tree_through

    from planner.core import db

    path = tmp_path / "legacy.db"
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "rename_managers")
    conn = connect(str(path))
    create_schema(conn)
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
        "created_at,model) "
        "VALUES ('c','hermes','/synthetic','full',0,'a-model')"
    )
    conn.execute(
        "INSERT INTO conversation_lifecycle_facts VALUES ('c','turn','1',1,'turn','intent',NULL)"
    )
    conn.execute("CREATE TABLE signal_publication_meta(singleton INTEGER, runtime_epoch TEXT)")
    conn.execute("INSERT INTO signal_publication_meta VALUES (1,'legacy-epoch')")
    conn.commit()
    legacy = CapturedTurn(
        "c", 1, None, "legacy-epoch", "intent", "Interrupted worker", "unknown", None, None, None
    )
    captured = CaptureManifest(
        "release",
        "stop",
        123,
        "captured",
        None,
        (
            legacy,
            replace(legacy, runtime_epoch="restored-other-epoch", display_name="Reused legacy"),
            replace(legacy, conversation_id="candidate-erased", display_name="Candidate"),
        ),
    )
    write_manifest(path, captured)
    write_manifest(
        path,
        replace(
            captured,
            capture_id="rollback-stop",
            state="unavailable",
            detail="Rollback capture timed out",
            turns=(),
        ),
    )
    shutil.copy(
        shipped / "versions/deploy_interruption_accounts.py",
        tree / "versions/deploy_interruption_accounts.py",
    )
    create_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM conversation_turn_occurrences").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM conversation_lifecycle_facts").fetchone()[0] == "intent"
    conn.close()

    async def exercise() -> None:
        for boot in range(2):
            current = _Harness(path)
            try:
                assert current.spawned_conversation_ids == []
                with connect(str(path)) as conn:
                    accounts = read_summary(conn)["accounts"]
                    assert len(accounts) == 1
                    assert accounts[0]["capture_state"] == "unavailable"
                    assert accounts[0]["detail"] == "Rollback capture timed out"
                    entries = accounts[0]["entries"]
                by_name = {entry["display_name"]: entry for entry in entries}
                assert len(entries) == 3
                assert by_name["Reused legacy"]["recovery_state"] == "historical"
                assert by_name["Candidate"]["conversation_id"] is None
                assert by_name["Candidate"]["recovery_state"] == "historical"
                assert by_name["Interrupted worker"]["recovery_state"] == (
                    "awaiting_action" if boot == 0 else "resumed"
                )
                await current.system.send(
                    "c", text_message_content("continue"), sender_label="owner"
                )
                wire = current.backend("c").written_texts()[0]
                assert ("[Panels deployment interruption context]" in wire) == (boot == 0)
            finally:
                await current.system.shutdown()

    _run(exercise)


@pytest.mark.parametrize("rebind", [False, True])
def test_suppressed_review_preserves_interruption_context_for_next_actual_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rebind: bool,
) -> None:
    import asyncio

    from tests.unit.test_conversation_system import (
        _FakeBackendChild,
        _resolve_review,
        _review_notice,
    )

    from planner.conversation.contracts import PromptDeliveryStarted, PromptDeliverySuppressed

    path = tmp_path / "db.sqlite"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    original = _Harness(path)

    async def exercise() -> None:
        await _start(original)
        await original.store.reserve_turn("c")
        write_manifest(path, _capture(path))
        await original.system.shutdown()
        restarted = _Harness(path)
        try:
            before = _entry(path)
            assert before["recovery_state"] == "awaiting_action"
            ticket_id, sender_id, message = _review_notice(path)
            entered, release = asyncio.Event(), asyncio.Event()
            original_start = _FakeBackendChild.start

            async def delayed_start(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                await original_start(self, *args, **kwargs)
                if restarted.backend("c").session_starts == (2 if rebind else 1):
                    entered.set()
                    await release.wait()

            monkeypatch.setattr(_FakeBackendChild, "start", delayed_start)
            restarted.backend("c").needs_rebind_once = rebind
            sending = asyncio.create_task(restarted.system.send(
                "c", text_message_content(message), sender_label="Chief",
                sender_message_id=sender_id,
            ))
            await entered.wait()
            _resolve_review(path, ticket_id)
            release.set()
            assert isinstance(await sending, PromptDeliverySuppressed)
            assert restarted.backend("c").writes == []
            assert not await restarted.system.is_running("c")
            assert _entry(path) == before
            with connect(str(path)) as conn:
                assert conn.execute(
                    "SELECT count(*) FROM deploy_interruption_deliveries"
                ).fetchone()[0] == 0
        finally:
            await restarted.system.shutdown()

        monkeypatch.setattr(_FakeBackendChild, "start", original_start)
        # A further boot must not misclassify a pre-wire suppression as unknown recovery.
        final = _Harness(path)
        try:
            assert _entry(path) == before
            fate = await final.system.send(
                "c", text_message_content("Inspect current work"), sender_label="Chief",
                sender=CHIEF_PRINCIPAL, recipient=Principal(PrincipalKind.ticket, ticket_id),
            )
            assert isinstance(fate, PromptDeliveryStarted)
            wire = final.backend("c").written_texts()[0]
            assert wire.startswith("[Authenticated Panels reply requirement]")
            assert wire.count("[Panels deployment interruption context]") == 1
            assert wire.index("[Panels deployment interruption context]") < wire.index(
                "Inspect current work"
            )
            assert _entry(path)["recovery_state"] == "resumed"
            with connect(str(path)) as conn:
                assert [row[0] for row in conn.execute(
                    "SELECT status FROM deploy_interruption_deliveries"
                )] == ["started"]
        finally:
            await final.system.shutdown()

    _run(exercise)


def test_rebind_preserves_interruption_context_in_the_actual_backend_prompt(tmp_path: Path) -> None:
    from planner.conversation.contracts import PromptDeliveryStarted

    path = tmp_path / "db.sqlite"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    original = _Harness(path)

    async def exercise() -> None:
        await _start(original)
        await original.store.reserve_turn("c")
        write_manifest(path, _capture(path))
        await original.system.shutdown()
        restarted = _Harness(path)
        try:
            backend = restarted.backend("c")
            backend.needs_rebind_once = True
            fate = await restarted.system.send(
                "c", text_message_content("Inspect after deploy"), sender_label="Chief",
                sender=CHIEF_PRINCIPAL, recipient=Principal(PrincipalKind.ticket, "reviewer"),
            )
            assert isinstance(fate, PromptDeliveryStarted)
            assert backend.session_starts == 2
            assert len(backend.writes) == 1
            wire = backend.writes[0].text
            assert wire.startswith("[Authenticated Panels reply requirement]")
            assert wire.count("[Panels deployment interruption context]") == 1
            assert wire.index("[Panels deployment interruption context]") < wire.index(
                "Inspect after deploy"
            )
            assert backend.writes[0].sender_content == text_message_content("Inspect after deploy")
            assert _entry(path)["recovery_state"] == "resumed"
            with connect(str(path)) as conn:
                assert [row[0] for row in conn.execute(
                    "SELECT status FROM deploy_interruption_deliveries"
                )] == ["started"]
        finally:
            await restarted.system.shutdown()

    _run(exercise)
