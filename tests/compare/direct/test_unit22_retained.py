"""Retained-core joins: initial Signal, saved answers, and closed-run startup recovery."""

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
from tests.compare.direct.test_unit18_adapter import environment, normalize
from tests.support.executor_presence import lease_request, mac_identity, make_current
from tests.unit.test_conversation_system import _Harness, _start
from tests.unit.test_executor_vps_restart import _link_mac_tickets

from planner.conversation.api import delivery_fate_json
from planner.conversation.backend_event_contracts import AcceptedEvent
from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.deploy_interruptions.coordinator import (
    close_run_and_record_mac_process_interruption,
    run_automatic_recovery,
)
from planner.conversation.events import UserInputAnswer
from planner.conversation.lifecycle_storage import validate_runtime_ownership
from planner.conversation.message_content import text_message_content
from planner.conversation.publication.coordinator import SignalPublicationCoordinator
from planner.conversation.runtime_composition import transaction_control
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.store import ExecutorStore
from planner.executor.vps_inbox import InboxStore
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService
from planner.executor.vps_runs import MacRuns

ROOT = Path(__file__).resolve().parents[3]
OLD = mac_identity("11111111-1111-4111-8111-111111111111")
FRESH = mac_identity("22222222-2222-4222-8222-222222222222")


async def seed(path: Path) -> dict[str, str]:
    host = _Harness(path)
    try:
        for name in ("retained", "closed", "stopping"):
            await _start(host, name, backend_key=ConversationBackendKey.codex)
            await host.system.send(name, text_message_content("work"), sender_label="owner")
        permission = await host.raise_permission_ask("retained")
        user_input = await host.request_user_input("retained")
    finally:
        await host.system.shutdown()
    _link_mac_tickets(path, "closed")
    with closing(connect(str(path))) as sql:
        sql.execute(
            "UPDATE conversations SET role_text='role',identity_environment_variables=? "
            "WHERE conversation_id='closed'",
            (json.dumps([["PLAN_ACTOR", "worker"], ["PLAN_TICKET_ID", "t-closed"]]),),
        )
        sql.commit()
    make_current(str(path), OLD, at_ms=1000000)
    store = ExecutorStore(str(path))
    for name in ("retained", "closed", "stopping"):
        child = ChildReference(OLD.executor_incarnation, name, name)
        store.register_child(
            child, "ticket", "t-closed" if name == "closed" else "fixture", 1000000
        )
        if name == "closed":
            close_run_and_record_mac_process_interruption(
                str(path), child, FRESH.executor_incarnation, 1000000, undelivered_message="work"
            )
        if name == "stopping":
            store.prepare_command(
                wire.ChildCommand("stop", child, wire.ChildOperation.stop, None, wire.StopRun())
            )
    make_current(str(path), OLD, at_ms=0)
    return {"permission": permission, "user_input": user_input.request_id}


async def source(path: Path, spec: dict[str, str]) -> dict[str, Any]:
    mailbox: SqliteCommandMailbox
    presence: MacPresenceService
    runs: MacRuns

    def cycle() -> _Harness:
        nonlocal mailbox, presence, runs
        mailbox = SqliteCommandMailbox(str(path), clock=lambda: 1000000)
        presence = MacPresenceService(str(path), mailbox, clock=lambda: 1000000)
        runs = MacRuns(str(path), presence, mailbox)
        host = _Harness(path, factory_wrapper=lambda local: runs)
        runs.conversations = host.system
        return host

    async def close() -> None:
        await host.system.shutdown()
        await runs.close()
        await presence.close()
        await mailbox.close()
        assert not host.spawned_conversation_ids

    host = cycle()
    result: dict[str, Any] = {}
    try:
        core = host.system
        running = core._conversations["retained"].running_turn
        assert running is not None
        result["adopted"] = [
            running.token.turn_number,
            sorted(running.pending_user_input_questions),
        ]
        stopped = core._conversations["stopping"]
        result["stopping"] = [stopped.running_turn is None, stopped.child_is_quarantined]
        publication = SignalPublicationCoordinator(
            str(path),
            core._runtime_ownership,
            connect=lambda: connect(str(path)),
            authority_check=validate_runtime_ownership,
            transaction_control=transaction_control,
        )
        publication.bind_runtime(core._runtime_turn)
        with closing(connect(str(path))) as sql:
            sql.execute("BEGIN IMMEDIATE")
            publication.initialize(sql)
            sql.commit()
        attention: dict[str, bool] = {}
        attachment = publication.attach_signal_publication(
            install=lambda sql, snapshot: attention.update(
                (value.conversation_id, value.attention.awaiting_answer)
                for value in snapshot.values
            ),
            participate=lambda sql, batch: None,
            unavailable=lambda reason: None,
        )
        attachment.close()
        result["first_signal"] = attention["retained"]
        result["saved"] = [
            (await core.answer_permission_ask("retained", spec["permission"], "allow-once")).value,
            (
                await core.answer_user_input(
                    "retained",
                    spec["user_input"],
                    (UserInputAnswer("timing", ("Now",)), UserInputAnswer("scope", ("Backend",))),
                )
            ).value,
        ]
        await run_automatic_recovery(core, host.store)
        await run_automatic_recovery(core, host.store)
        with closing(connect(str(path))) as sql:
            result["recovery"] = list(
                sql.execute(
                    "SELECT recovery_state FROM deploy_interruptions "
                    "WHERE source_conversation_id='closed'"
                ).fetchone()
            )
            result["claim"] = list(
                sql.execute(
                    "SELECT worker_step_claim,worker_step_claim_revision FROM tickets "
                    "WHERE id='t-closed'"
                ).fetchone()
            )
            result["prompts"] = [
                json.loads(row[0])["child_command"]["arguments"]["content"]
                for row in sql.execute(
                    "SELECT command_json FROM executor_commands WHERE conversation_id='closed' "
                    "AND operation='write_prompt'"
                )
            ]
        await close()
        host = cycle()
        core = host.system
        inbox = InboxStore(str(path))
        result["applications"] = []
        with closing(connect(str(path))) as sql:
            answers = sql.execute(
                "SELECT command_id,operation FROM executor_commands "
                "WHERE conversation_id='retained' ORDER BY command_sequence"
            ).fetchall()
        for sequence, (answer_id, operation) in enumerate(answers, 1):
            kind = "permission" if operation == "answer_permission_ask" else "user_input"
            envelope = wire.QueuedEnvelope(
                "sender", "retained", sequence, kind, wire.EnvelopeKind.answer_application, {}
            )

            def participant(
                sql: sqlite3.Connection, take: Any, envelope: wire.QueuedEnvelope = envelope
            ) -> AcceptedEvent:
                receipt, taken = inbox.accept_in_transaction(
                    sql,
                    envelope,
                    lambda sql, item: cast(wire.JsonObject, take()),
                    1000000,
                    identity=OLD,
                )
                return AcceptedEvent(cast(wire.JsonObject, wire.encode_value(receipt)), taken)

            provider = "ask-1" if kind == "permission" else "input-1"
            for _ in range(2):
                accepted = await core.take_answer_application(
                    TurnToken("retained", 1), answer_id, provider, kind, participant
                )
                result["applications"].append(
                    [kind, accepted.taken, accepted.result["disposition"]]
                )
        await close()
        make_current(str(path), FRESH, at_ms=1000000)
        host = cycle()
        result["stale"] = delivery_fate_json(
            await host.system.send("retained", text_message_content("wait"), sender_label="owner")
        )
    finally:
        await close()
    return result


def test_retained_core_matches_fresh_python(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    began = time.perf_counter()
    selected = {**environment(tmp_path), "PLAN_TEST_MODE": "1"}
    py, ts = tmp_path / "source.sqlite", tmp_path / "candidate.sqlite"
    with patch.dict(os.environ, selected, clear=True):
        with closing(connect(str(py))) as sql:
            create_schema(sql)
            sql.commit()
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
                "fresh_lease": wire.encode_value(lease_request(FRESH)),
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
    actual, expected = normalize(json.loads(completed.stdout)), normalize(expected)
    assert actual == expected
    assert actual["first_signal"] and actual["saved"] == ["saved", "saved"]
    assert actual["stopping"] == [True, True] and actual["claim"] == ["out", 9]
    assert actual["recovery"] == ["resumed"] and len(actual["prompts"]) == 1
    destination = Path(request.config.getoption("--compare-report-dir") or tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "unit22-retained.json").write_text(
        json.dumps(
            {"seconds": time.perf_counter() - began, "source": expected, "candidate": actual},
            indent=2,
        )
        + "\n"
    )
