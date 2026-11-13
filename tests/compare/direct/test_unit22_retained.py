"""Actual retained adoption, saved answers, crash recovery and exact retirement.

The run-store proof cannot prove in-memory asks or callback revocation. This
continuation uses the existing executor turn fixture and a copied populated DB.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit18_adapter import environment, normalize, snapshot
from tests.support.executor_presence import lease_request, mac_identity, make_current
from tests.unit.test_conversation_system import _Harness, _start
from tests.unit.test_executor_vps_restart import _link_mac_tickets

from planner.conversation.backend_event_contracts import AcceptedEvent
from planner.conversation.backends.contracts import (
    BackendSteerUncertain,
    BackendUserInputRequest,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
    UserInputRequested,
)
from planner.conversation.contracts import ConversationBackendKey, PromptDeliveryMode
from planner.conversation.deploy_interruptions.coordinator import (
    close_run_and_record_mac_process_interruption,
    run_automatic_recovery,
)
from planner.conversation.events import ConversationTurnEnding, UserInputAnswer, UserInputQuestion
from planner.conversation.lifecycle import LifecycleOperation
from planner.conversation.lifecycle_storage import validate_runtime_ownership
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.publication.coordinator import SignalPublicationCoordinator
from planner.conversation.runtime_composition import transaction_control
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.store import ExecutorStore
from planner.executor.vps_inbox import InboxStore
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService
from planner.executor.vps_runs import MacRuns
from planner.runtime.worker_memory import MEMORY_NOTICE_MESSAGE_PREFIX

ROOT = Path(__file__).resolve().parents[3]
OLD = mac_identity("11111111-1111-4111-8111-111111111111")
FRESH = mac_identity("22222222-2222-4222-8222-222222222222")
CHILD = ChildReference(OLD.executor_incarnation, "child", "retained")
CANCELLED = ChildReference(OLD.executor_incarnation, "cancelled", "cancelled")
STOPPING = ChildReference(OLD.executor_incarnation, "stopping", "stopping")
REWRITE = ChildReference(OLD.executor_incarnation, "fresh", "rewrite")


async def seed(path: Path) -> dict[str, Any]:
    harness = _Harness(path)
    asks = {}
    try:
        for identity in ("retained", "cancelled"):
            await _start(harness, identity, backend_key=ConversationBackendKey.codex)
            await harness.system.send(
                identity, text_message_content("incumbent"), sender_label="owner"
            )
            permission = await harness.raise_permission_ask(identity)
            user_input = await harness.request_user_input(identity)
            asks[identity] = {"permission": permission, "user_input": user_input.request_id}
        turn = harness.backend("retained").live_turn_token
        assert turn is not None
        for identity in ("notice", "notice-steer", "rewrite", "closed-never-ran", "stopping"):
            await _start(harness, identity, backend_key=ConversationBackendKey.codex)
            await harness.system.send(
                identity,
                text_message_content("work"),
                sender_label="owner",
                sender_message_id=MEMORY_NOTICE_MESSAGE_PREFIX + identity
                if identity.startswith("notice")
                else None,
            )
        harness.backend("notice-steer").steer_outcome = BackendSteerUncertain()
        with patch("planner.conversation.system.backend_supports_steer", return_value=True):
            await harness.system.send(
                "notice-steer",
                text_message_content("owner"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
        rewrite_record = await harness.store.read_conversation("rewrite")
        assert rewrite_record is not None
    finally:
        await harness.system.shutdown()
    _link_mac_tickets(path, "closed-never-ran")
    with closing(connect(str(path))) as connection:
        connection.execute(
            "UPDATE conversations SET role_text='role',identity_environment_variables=? "
            "WHERE conversation_id='closed-never-ran'",
            (json.dumps([["PLAN_ACTOR", "worker"], ["PLAN_TICKET_ID", "t-closed-never-ran"]]),),
        )
        connection.commit()
    prior = mac_identity("33333333-3333-4333-8333-333333333333")
    make_current(str(path), prior, at_ms=1000000)
    store = ExecutorStore(str(path))
    old_rewrite = ChildReference(prior.executor_incarnation, "old", "rewrite")
    store.register_child(old_rewrite, "ticket", "test-ticket", 1000000)
    closed = ChildReference(prior.executor_incarnation, "closed-old", "closed-never-ran")
    store.register_child(closed, "ticket", "t-closed-never-ran", 1000000)
    close_run_and_record_mac_process_interruption(
        str(path), closed, OLD.executor_incarnation, 1000000, undelivered_message="work"
    )
    mailbox = SqliteCommandMailbox(str(path), clock=lambda: 1000000)
    presence = MacPresenceService(str(path), mailbox, clock=lambda: 1000000)
    try:
        await mailbox.put_child_command(
            wire.ChildCommand(
                "never-ran",
                old_rewrite,
                wire.ChildOperation.write_prompt,
                TurnToken("rewrite", 1),
                wire.WritePrompt(
                    content=text_message_content("role\n\nwork"),
                    sender_content=text_message_content("work"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.queue,
                ),
            )
        )
        await presence.accept_lease(lease_request(OLD))
        close_run_and_record_mac_process_interruption(
            str(path), old_rewrite, OLD.executor_incarnation, 1000000, undelivered_message="work"
        )
        store.register_child(REWRITE, "ticket", "test-ticket", 1000000)
        snapshot = wire.CanonicalSkillSnapshot(
            (wire.CanonicalSkillSource("panels", "fixture"),), ("panels",)
        )
        await mailbox.put_child_command(
            wire.ChildCommand(
                "fresh-start",
                REWRITE,
                wire.ChildOperation.start,
                None,
                wire.StartRun(
                    resolved_start=rewrite_record.resolved_start(),
                    canonical_skill_snapshot=snapshot,
                ),
            )
        )
    finally:
        await presence.close()
        await mailbox.close()
    for child in (
        CHILD,
        CANCELLED,
        STOPPING,
        *(
            ChildReference(OLD.executor_incarnation, name, name)
            for name in ("notice", "notice-steer")
        ),
    ):
        store.register_child(child, "ticket", "test-ticket", 1000000)
    store.prepare_command(
        wire.ChildCommand("stopping-stop", STOPPING, wire.ChildOperation.stop, None, wire.StopRun())
    )
    make_current(str(path), OLD, at_ms=0)
    return {"turn": wire.encode_value(turn), "asks": asks}


async def source(path: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    mailbox: SqliteCommandMailbox
    presence: MacPresenceService
    runs: MacRuns
    initial_awaiting: dict[str, bool]

    def cycle() -> SqliteProcessConversationSystem:
        nonlocal mailbox, presence, runs, initial_awaiting
        mailbox = SqliteCommandMailbox(str(path), clock=lambda: 1000000)
        presence = MacPresenceService(str(path), mailbox, clock=lambda: 1000000)
        runs = MacRuns(str(path), presence, mailbox)
        system = SqliteProcessConversationSystem(
            store=ConversationStore(str(path), integer_now=lambda: 1000),
            message_files=ConversationMessageFiles(str(path)),
            backend_child_factories={key: runs for key in ConversationBackendKey},
            unix_time_now=lambda: 1000,
            monotonic_now=lambda: 1000,
        )
        runs.conversations = system
        publication = SignalPublicationCoordinator(
            str(path),
            system._runtime_ownership,
            connect=lambda: connect(str(path)),
            authority_check=validate_runtime_ownership,
            transaction_control=transaction_control,
        )
        publication.bind_runtime(system._runtime_turn)
        with closing(connect(str(path))) as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication.initialize(connection)
            connection.commit()
        initial_awaiting = {}
        attachment = publication.attach_signal_publication(
            install=lambda connection, snapshot: initial_awaiting.update(
                (value.conversation_id, value.attention.awaiting_answer)
                for value in snapshot.values
            ),
            participate=lambda connection, batch: None,
            unavailable=lambda reason: None,
        )
        attachment.close()
        return system

    async def close() -> None:
        await core.shutdown()
        await runs.close()
        await presence.close()
        await mailbox.close()

    core = cycle()
    trace: list[dict[str, Any]] = []

    async def capture(name: str, reply: Any = None, identity: str = "retained") -> None:
        state = core._conversations[identity]
        running = state.running_turn
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            custody = {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} ORDER BY "
                        + (
                            "conversation_id,executor_incarnation,child_id"
                            if table == "executor_child_assignments"
                            else "1,2"
                        )
                    )
                ]
                for table in (
                    "mac_runs",
                    "executor_child_assignments",
                    "mac_inbox_positions",
                    "deploy_interruptions",
                )
            }
            custody["commands"] = [
                {**dict(row), "arguments": json.loads(row["arguments"])}
                for row in connection.execute(
                    "SELECT child_id,operation,turn_number,state,"
                    "json_extract(command_json,'$.child_command.arguments') AS arguments "
                    "FROM executor_commands ORDER BY command_sequence"
                )
            ]
            allocated_children = [
                dict(row)
                for row in connection.execute(
                    "SELECT conversation_id,child_id AS identity FROM executor_child_assignments "
                    "WHERE conversation_id='closed-never-ran' AND child_id<>'closed-old' "
                    "ORDER BY rowid"
                )
            ]
        trace.append(
            {
                "name": name,
                "reply": reply,
                "rows": snapshot(str(path)),
                "custody": custody,
                "initial_awaiting": initial_awaiting,
                "allocated_children": allocated_children,
                "runtime": {
                    "running": running is not None,
                    "token": None if running is None else wire.encode_value(running.token),
                    "callbacks_revoked": None if running is None else running.callbacks_revoked,
                    "memory_notice_only": None if running is None else running.memory_notice_only,
                    "permission_ids": []
                    if running is None
                    else sorted(running.pending_permission_ask_ids),
                    "input_ids": []
                    if running is None
                    else sorted(running.pending_user_input_questions),
                    "questions": {}
                    if running is None
                    else {
                        key: wire.encode_value(value)
                        for key, value in running.pending_user_input_questions.items()
                    },
                    "quarantined": state.child_is_quarantined,
                    "child": state.child is not None,
                    "reference": None
                    if state.retained_child_reference is None
                    else wire.encode_value(state.retained_child_reference),
                },
            }
        )

    try:
        await capture("adopted-asks")
        await capture("stopping-run-quarantined", identity="stopping")
        assert core._conversations["stopping"].running_turn is None
        assert core._conversations["stopping"].child_is_quarantined
        await capture("closed-run-before-recovery", identity="closed-never-ran")
        assert not any(
            run.reference.conversation_id == "closed-never-ran"
            for run in runs.store.retained_runs()
            if not run.retirement_confirmed
        )
        await run_automatic_recovery(core, core._store)
        await capture("closed-run-message-recovered", identity="closed-never-ran")
        await run_automatic_recovery(core, core._store)
        await capture("closed-run-recovery-not-repeated", identity="closed-never-ran")
        with sqlite3.connect(path) as connection:
            recovered = connection.execute(
                "SELECT command_json FROM executor_commands "
                "WHERE conversation_id='closed-never-ran' AND operation='write_prompt'"
            ).fetchall()
            assert len(recovered) == 1
            text = "".join(
                piece["text"]
                for piece in json.loads(recovered[0][0])["child_command"]["arguments"]["content"]
            )
            assert (
                "Inspect the interrupted work" in text and "A Mac restart ended this turn." in text
            )
            assert "never reached you" in text and "The message was: work" in text
            assert connection.execute(
                "SELECT worker_step_claim,worker_step_claim_revision FROM tickets "
                "WHERE id='t-closed-never-ran'"
            ).fetchone() == ("out", 9)
            assert connection.execute(
                "SELECT recovery_state FROM deploy_interruptions "
                "WHERE source_conversation_id='closed-never-ran'"
            ).fetchone() == ("resumed",)
        assert initial_awaiting["retained"] and initial_awaiting["cancelled"]
        answers = (UserInputAnswer("timing", ("Now",)), UserInputAnswer("scope", ("Backend",)))

        async def answer_both(identity: str) -> list[str]:
            return [
                (
                    await core.answer_permission_ask(
                        identity, spec["asks"][identity]["permission"], "allow-once"
                    )
                ).value,
                (
                    await core.answer_user_input(
                        identity, spec["asks"][identity]["user_input"], answers
                    )
                ).value,
            ]

        assert not presence.is_present()
        for identity in ("retained", "cancelled"):
            assert await answer_both(identity) == ["saved", "saved"]
        await capture("away-answers-saved")
        state = core._conversations["cancelled"]
        assert state.running_turn is not None
        # The exact crash point: revocation and local attempt committed, before native dispatch.
        async with state.lock:
            await core._revoke_turn_requests(state, state.running_turn)
            await core._begin_attempt(state, state.running_turn.token, LifecycleOperation.cancel)
        await close()
        core = cycle()
        await capture("cancel-attempt-adopted", identity="cancelled")
        assert core._conversations["cancelled"].running_turn.callbacks_revoked  # type: ignore[union-attr]
        assert await answer_both("cancelled") == ["refused", "refused"]
        late = await core.take_backend_event(
            TurnToken("cancelled", 1),
            UserInputRequested(
                request=BackendUserInputRequest(
                    "late", (UserInputQuestion("late", "Late", "Late?", (), False, True),)
                )
            ),
        )
        assert not late.taken
        await capture("cancelled-late-ask-refused", identity="cancelled")
        await mailbox.poll(OLD, 0, 0)
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT state FROM executor_commands WHERE conversation_id='cancelled'"
            ).fetchall() == [("expired",), ("expired",)]
        await capture("cancelled-answers-expired", identity="cancelled")
        for identity in ("notice", "notice-steer"):
            await capture(identity + "-adopted", identity=identity)
            await core.take_backend_event(
                TurnToken(identity, 1),
                ToolCallStarted(tool_call_id="tool", title="ls", tool_kind="execute"),
            )
            await core.take_backend_event(
                TurnToken(identity, 1),
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            record = await core._store.read_conversation(identity)
            assert record is not None
            assert record.latest_agent_activity_sequence == (
                record.latest_sequence if identity == "notice-steer" else 0
            )
            await capture(identity + "-ended", identity=identity)
        await runs.resume_replacement_drains()
        await asyncio.gather(*tuple(runs._tasks))
        await capture("fresh-registered-resumed", identity="rewrite")
        await runs.resume_replacement_drains()
        await asyncio.gather(*tuple(runs._tasks))
        await capture("fresh-registered-no-repeat", identity="rewrite")
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM executor_commands WHERE child_id='fresh' "
                    "AND operation='write_prompt'"
                ).fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT recovery_state FROM deploy_interruptions "
                    "WHERE source_conversation_id='rewrite'"
                ).fetchone()[0]
                == "closed"
            )
        turn = TurnToken(**spec["turn"])
        inbox = InboxStore(str(path))
        with sqlite3.connect(path) as connection:
            answer_ids = connection.execute(
                "SELECT command_id,operation FROM executor_commands "
                "WHERE conversation_id='retained' ORDER BY command_sequence"
            ).fetchall()
        await mailbox.record_results(
            OLD,
            wire.CommandOutcomeReport(
                tuple(
                    wire.CommandResult(
                        answer_id,
                        wire.CommandOutcome(
                            answer_id, CHILD, wire.CommandDisposition.succeeded, {}
                        ),
                    )
                    for answer_id, operation in answer_ids
                )
            ),
        )
        for sequence, (answer_id, operation) in enumerate(answer_ids, 1):
            kind = "permission" if operation == "answer_permission_ask" else "user_input"
            envelope = wire.QueuedEnvelope(
                "sender",
                "retained",
                sequence,
                "saved-" + kind,
                wire.EnvelopeKind.answer_application,
                {},
            )

            def participant(
                connection: sqlite3.Connection, take: Any, envelope: wire.QueuedEnvelope = envelope
            ) -> AcceptedEvent:
                receipt, taken = inbox.accept_in_transaction(
                    connection,
                    envelope,
                    lambda connection, envelope: cast(wire.JsonObject, take()),
                    1000000,
                    identity=OLD,
                )
                return AcceptedEvent(cast(wire.JsonObject, wire.encode_value(receipt)), taken)

            provider = "ask-1" if kind == "permission" else "input-1"
            accepted = await core.take_answer_application(
                turn, answer_id, provider, kind, participant
            )
            await capture(
                "saved-" + kind + "-applied", {"result": accepted.result, "taken": accepted.taken}
            )
            replay = await core.take_answer_application(
                turn, answer_id, provider, kind, participant
            )
            await capture(
                "saved-" + kind + "-replay", {"result": replay.result, "taken": replay.taken}
            )
        refused = await answer_both("retained")
        assert refused == ["refused", "refused"]
        await capture("applied-answers-refuse-reanswer", refused)
        await close()
        make_current(str(path), FRESH, at_ms=1000000)
        core = cycle()
        fate = await core.send(
            "retained",
            text_message_content("wait behind stale child"),
            sender_label="owner",
            sender_message_id="queued",
        )
        from planner.conversation.api import delivery_fate_json

        await capture("stale-fenced", delivery_fate_json(fate))
        await core.confirm_remote_child_termination(
            ChildReference(CHILD.executor_incarnation, "foreign", CHILD.conversation_id)
        )
        await capture("foreign-proof-ignored")
        ExecutorStore(str(path)).close_child(CHILD, 1000000)
        await core.confirm_remote_child_termination(CHILD)
        await capture("exact-retirement")
    finally:
        await close()
    return trace


def test_retained_core_matches_fresh_python(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    began = time.perf_counter()
    selected = environment(tmp_path)
    selected["PLAN_TEST_MODE"] = "1"
    py, ts = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    with patch.dict(os.environ, selected, clear=True):
        connection = connect(str(py))
        try:
            create_schema(connection)
            connection.commit()
        finally:
            connection.close()
        spec = asyncio.run(seed(py))
        with sqlite3.connect(py) as left, sqlite3.connect(ts) as right:
            left.backup(right)
        with (
            patch("planner.executor.vps_runs.now_ms", return_value=1000000),
            patch("planner.executor.vps_presence.now_ms", return_value=1000000),
            patch("planner.executor.vps_remote_child.now_ms", return_value=1000000),
        ):
            expected = asyncio.run(asyncio.wait_for(source(py, spec), 20))
    completed = subprocess.run(
        ["node", str(ROOT / "server/conversation/testing/unit22_retained.ts")],
        input=json.dumps(
            {
                **spec,
                "path": str(ts),
                "old": wire.encode_value(OLD),
                "fresh": wire.encode_value(FRESH),
                "child": wire.encode_value(CHILD),
            }
        ),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=selected,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    expected, actual = normalize(expected), normalize(actual)
    destination = Path(request.config.getoption("--compare-report-dir") or tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "unit22-retained.json").write_text(
        json.dumps(
            {"seconds": time.perf_counter() - began, "source": expected, "candidate": actual},
            indent=2,
        )
        + "\n"
    )
    assert actual == expected
    assert actual[0]["runtime"]["running"] and actual[0]["runtime"]["input_ids"]
    assert actual[-1]["runtime"]["running"] is False
    assert actual[-1]["runtime"]["reference"] is None
