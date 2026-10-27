"""Failure episodes reach a responsible conversation through existing delivery machinery."""

import asyncio
from pathlib import Path
from sqlite3 import Connection

from tests.support.conversation_system_under_test import open_conversation_system_under_test
from tests.support.measure_sources import NOW, configured_source
from tests.unit.test_manager_wakes import _item_and_ticket
from tests.unit.test_measure_source_lifecycle import METRICS, _run

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.manager_wakes import data as wakes
from planner.manager_wakes.runtime import deliver_batch
from planner.measure_sources import actions, run_actions
from planner.measure_sources.contracts import FailureCode, RunFailure, RunResult


def test_source_failure_reaches_real_prompt_then_reassignment_notifies_new_principal(
    tmp_db: Connection,
    fake_clock: FakeClock,
) -> None:
    manager_id, _ = _item_and_ticket(tmp_db, fake_clock)
    manager = Principal(PrincipalKind.manager, manager_id)
    source, _ = configured_source(tmp_db, responsible=manager)
    run = _run(tmp_db, source.id)
    run_actions.complete_run(
        tmp_db,
        run.id,
        RunResult((), RunFailure(FailureCode.http_error, http_status=401), METRICS),
        now=NOW,
    )
    batch = wakes.claim_next_batch(tmp_db, process_token="source-proof", now=NOW + 1)
    assert batch is not None and batch.target == manager
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    async def exercise() -> None:
        async with open_conversation_system_under_test(database_path=database) as subject:
            assert await deliver_batch(
                batch,
                connect_database=lambda: connect(str(database)),
                conversation_system=subject.system,
                process_token="source-proof",
                now=lambda: NOW + 2,
            )
            await subject.settle()
            conversation = tmp_db.execute(
                "SELECT conversation_id FROM manager_wake_batches WHERE id=?",
                (batch.id,),
            ).fetchone()[0]
            writes = await subject.backend_writes(conversation)
            assert len(writes) == 1
            actual_prompt = writes[0].text
            assert source.id in actual_prompt and run.id in actual_prompt
            assert "Synthetic source" in actual_prompt and "fixture" in actual_prompt
            assert (
                "HTTP status: 401" in actual_prompt
                and "Affected dates: 2026-10-27" in actual_prompt
            )
            assert "Last successful collection" in actual_prompt
            actions.set_responsible(
                tmp_db,
                source.id,
                CHIEF_PRINCIPAL,
                expected_responsibility_revision=1,
                principal=OWNER_PRINCIPAL,
                now=NOW + 3,
            )
            new_batch = wakes.claim_next_batch(tmp_db, process_token="chief-proof", now=NOW + 4)
            assert new_batch is not None and new_batch.target == CHIEF_PRINCIPAL
            assert await deliver_batch(
                new_batch,
                connect_database=lambda: connect(str(database)),
                conversation_system=subject.system,
                process_token="chief-proof",
                now=lambda: NOW + 5,
            )
            await subject.settle()
            chief_conversation = tmp_db.execute(
                "SELECT conversation_id FROM manager_wake_batches WHERE id=?",
                (new_batch.id,),
            ).fetchone()[0]
            chief_writes = await subject.backend_writes(chief_conversation)
            assert len(chief_writes) == 1 and source.id in chief_writes[0].text
            assert "HTTP status: 401" in chief_writes[0].text
            rows = tmp_db.execute(
                "SELECT closed_at FROM manager_wakes WHERE source_kind='source_failure'"
            ).fetchall()
            assert len(rows) == 2 and all(row[0] is not None for row in rows)

    asyncio.run(exercise())


def test_reassignment_before_delivery_discards_old_responsibility_batch(
    tmp_db: Connection,
    fake_clock: FakeClock,
) -> None:
    manager_id, _ = _item_and_ticket(tmp_db, fake_clock)
    source, _ = configured_source(tmp_db, responsible=Principal(PrincipalKind.manager, manager_id))
    run = _run(tmp_db, source.id)
    run_actions.complete_run(
        tmp_db, run.id, RunResult((), RunFailure(FailureCode.runtime_failure), METRICS), now=NOW
    )
    old = wakes.claim_next_batch(tmp_db, process_token="stale", now=NOW)
    assert old is not None
    actions.set_responsible(
        tmp_db,
        source.id,
        CHIEF_PRINCIPAL,
        expected_responsibility_revision=1,
        principal=OWNER_PRINCIPAL,
        now=NOW + 1,
    )
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    from planner.conversation.events import PromptDiscardedEventPayload
    from planner.conversation.message_content import text_message_content
    from planner.conversation.storage import ConversationStore

    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('source-admit','codex','model','/tmp','full',1)"
    )
    wakes.record_batch_offering(
        tmp_db, old.id, conversation_id="source-admit", process_token="stale", now=NOW + 2
    )
    store = ConversationStore(str(database), integer_now=lambda: NOW + 2)
    decisions = asyncio.run(
        store.admit_sender_messages(
            "source-admit",
            (
                PromptDiscardedEventPayload(
                    content=text_message_content(old.message),
                    sender_label="Panels",
                    sender_message_id=old.sender_message_id,
                ),
            ),
        )
    )
    assert decisions[0].is_manager_notice
    assert decisions[0].suppression_reason == "source_failure_changed"
    assert (
        tmp_db.execute("SELECT COUNT(*) FROM conversation_events WHERE kind='prompt'").fetchone()[0]
        == 0
    )
    assert (
        tmp_db.execute(
            "SELECT COUNT(*) FROM conversation_events WHERE kind='prompt_discarded'"
        ).fetchone()[0]
        == 1
    )
    latest = wakes.claim_next_batch(tmp_db, process_token="current", now=NOW + 3)
    assert latest is not None and latest.target == CHIEF_PRINCIPAL
