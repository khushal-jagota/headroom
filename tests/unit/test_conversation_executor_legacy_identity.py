"""Feature-head native callbacks retain their original public IDs through upgrade.

Reuse the real historical migration chain, HTTP harness, and Signal composition.
No browser, native host, transport connection, or provider runs.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_conversation_api import _Harness
from tests.unit.test_conversation_executor_publication import _envelope
from tests.unit.test_db import _migration_tree_through
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import PromptDeliveryMode, SignalSnapshot
from planner.conversation.events import (
    PermissionAskedEventPayload,
    PermissionAskOption,
    PromptEventPayload,
    UserInputAnswer,
    UserInputQuestion,
    UserInputRequestedEventPayload,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.conversation.views.api import router as view_router
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AnswerApplication,
    AskKind,
    AskReference,
    ChildCommand,
    ChildInventory,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    ExecutorIdentity,
    QueuedEnvelope,
)
from planner.executor.remote import RemoteBackendChild
from planner.executor.store import ExecutorStore
from planner.executor.vps import ExecutorEndpoint


@pytest.mark.parametrize("kind", list(AskKind))
@pytest.mark.parametrize("saved_before_upgrade", [False, True], ids=["unanswered", "saved"])
def test_feature_head_request_keeps_public_identity_and_original_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: AskKind,
    saved_before_upgrade: bool,
) -> None:
    path = tmp_path / "feature-head.db"
    conn = connect(str(path))
    with monkeypatch.context() as historical:
        _migration_tree_through(tmp_path, historical, "merge_resident_native_usage")
        create_schema(conn)
    assert (
        conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == "merge_resident_native_usage"
    )
    conn.execute(
        "INSERT INTO conversations (conversation_id,backend_key,model,workspace_folder,access,"
        "created_at,latest_sequence) VALUES "
        "('c','codex','scripted','/legacy/mac/project','full',1,3)"
    )
    child, turn = ChildReference("surviving-native", "original-child", "c"), TurnToken("c", 7)
    ask = AskReference(child, turn, "original:provider", kind)
    questions = (UserInputQuestion("q", "Scope", "Choose", (), False, True),)
    permission_request = PermissionAskedEventPayload(
        ask.ask_id, "Run?", None, (PermissionAskOption("allow", "Allow", "allow_once"),)
    )
    input_request = UserInputRequestedEventPayload(ask.ask_id, questions)
    prompt = json.loads(
        conversation_event_payload_to_canonical_json(
            PromptEventPayload(
                text_message_content("Original native work"), "Owner", PromptDeliveryMode.queue
            )
        )
    )
    prompt.pop("recovery_references", None)
    old_events = (
        json.dumps(prompt, sort_keys=True, separators=(",", ":")),
        conversation_event_payload_to_canonical_json(permission_request),
        conversation_event_payload_to_canonical_json(input_request),
    )
    for sequence, event_kind, payload in (
        (1, "prompt", old_events[0]),
        (2, "permission_asked", old_events[1]),
        (3, "user_input_requested", old_events[2]),
    ):
        conn.execute(
            "INSERT INTO conversation_events VALUES (?,?,?,?,?)",
            ("c", sequence, event_kind, payload, 1),
        )
    runtime = {
        "automatic_compaction": False,
        "compaction_confirmed": False,
        "automatic_notice": False,
        "ended": False,
        "callbacks_revoked": False,
        "pending_permission_ask_ids": [ask.ask_id],
        "pending_user_input_questions": {ask.ask_id: encode_value(questions)},
        "prompt_senders": [],
        "explicit_reply_recipients": [],
    }
    conn.execute(
        "INSERT INTO executor_core_turns VALUES (?,?,?,?)",
        ("c", json.dumps(encode_value(child)), json.dumps(encode_value(turn)), json.dumps(runtime)),
    )
    store = ExecutorStore(str(path))
    identity = ExecutorIdentity(
        PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "surviving-native", 1
    )
    store.begin_connection(identity, 100)
    store.register_child(child, "chief", "chief", 100)
    permission_ask = replace(ask, kind=AskKind.permission)
    input_ask = replace(ask, kind=AskKind.user_input)
    store.publish_ask(permission_ask, ("allow",), 100)
    store.publish_user_input_request(input_ask, questions, 100)
    original_answer = None
    if saved_before_upgrade:
        original_answer = (
            store.choose_answer(ask, "allow", 1, 101)
            if kind is AskKind.permission
            else store.choose_user_input_answer(ask, (UserInputAnswer("q", ("answer",)),), 1, 101)
        )
    store.mark_lost(identity, 102)
    conn.commit()
    create_schema(conn)
    conn.close()

    async def exercise() -> None:
        async with AsyncExitStack() as cleanup:
            composition = SignalRuntimeComposition(str(path), 1000)
            cleanup.callback(composition.close)
            composition.bootstrap()
            harness = _Harness(path, signal_composition=composition, cleanup=cleanup)
            endpoint = ExecutorEndpoint(str(path), RealClock())
            runtime = replace(harness.runtime, executor_endpoint=endpoint)
            harness.app.state.conversation = runtime
            endpoint.runtime = runtime
            harness.app.include_router(view_router, prefix="/api/conversation")
            composition.initialize()
            snapshots: list[SignalSnapshot] = []
            attachment = composition.coordinator.attach_signal_publication(
                install=lambda connection, snapshot: snapshots.append(snapshot),
                participate=lambda connection, batch: None,
                unavailable=lambda reason: None,
            )
            cleanup.callback(attachment.close)
            assert snapshots[0].values[0].attention.awaiting_answer
            assert store.public_request_id(ask) == ask.ask_id
            assert [row.payload for row in await harness.store.read_events_after("c", 0)]
            check = connect(str(path))
            try:
                assert (
                    tuple(
                        row[0]
                        for row in check.execute(
                            "SELECT payload FROM conversation_events "
                            "WHERE conversation_id='c' ORDER BY sequence"
                        )
                    )
                    == old_events
                )
            finally:
                check.close()
            native_calls: list[ChildCommand] = []

            async def surviving_callback(command: ChildCommand) -> CommandOutcome:
                native_calls.append(command)
                return CommandOutcome(
                    command.command_id, command.child, CommandDisposition.succeeded, {}
                )

            proxy = RemoteBackendChild(
                child, surviving_callback, lambda start: canonical_snapshot()
            )
            inventory = ChildInventory(
                child,
                turn,
                (permission_ask,),
                (ask.ask_id,),
            )
            await harness.system.adopt_remote_child(inventory, proxy)
            field = "pending_permission_ask" if kind is AskKind.permission else "pending_user_input"
            identity_field = "ask_id" if kind is AskKind.permission else "request_id"
            route = "permission-answers" if kind is AskKind.permission else "user-input-answers"
            body = {
                identity_field: ask.ask_id,
                "executor_ask": encode_value(ask),
                **(
                    {"option_id": "allow"}
                    if kind is AskKind.permission
                    else {"answers": {"q": {"answers": ["answer"]}}}
                ),
            }
            async with harness.client() as client:
                current = (await client.get("/api/conversation/conversations/c")).json()
                assert current[field][identity_field] == ask.ask_id
                assert current[field]["executor_evidence"]["ask"] == encode_value(ask)
                if kind is AskKind.user_input:
                    assert current[field]["questions"][0]["question_id"] == "q"
                projected = (
                    await client.get(
                        "/api/conversation/conversations/c/view", params={"lens": "full"}
                    )
                ).json()
                pending = projected["status"][
                    "pending_permission" if kind is AskKind.permission else "pending_user_input"
                ]
                assert pending["request_id"] == ask.ask_id
                answer_path = f"/api/conversation/conversations/c/{route}"
                forged_alias = {**body, identity_field: f"7:{kind.value}:{ask.ask_id}"}
                assert (await client.post(answer_path, json=forged_alias)).json() == {
                    "landed": False
                }
                assert (await client.post(answer_path, json=body)).json() == {"landed": True}
                answer = store.recorded_answer(ask)
                assert answer is not None
                if original_answer is not None:
                    assert answer == original_answer
                assert (await client.post(answer_path, json=body)).json() == {"landed": True}
                stale = replace(ask, child=replace(child, child_id="replacement"))
                assert (
                    await client.post(
                        answer_path, json={**body, "executor_ask": encode_value(stale)}
                    )
                ).json() == {"landed": False}
                assert native_calls == []
                # The surviving callback receives its original provider identity exactly once.
                if kind is AskKind.permission:
                    await proxy.answer_permission_ask(ask.ask_id, "allow")
                else:
                    await proxy.answer_user_input(ask.ask_id, (UserInputAnswer("q", ("answer",)),))
                assert len(native_calls) == 1
                assert native_calls[0].arguments[identity_field] == ask.ask_id
                application = AnswerApplication(answer.answer_id, ask, 110)
                application_envelope = _envelope(1, EnvelopeKind.answer_application, application)
                renewed = replace(identity, connection_generation=2)
                store.begin_connection(renewed, 109)

                def accept(row: QueuedEnvelope, writer: Any, **publication: Any) -> Any:
                    return store.accept(row, writer, 111, identity=renewed, **publication)

                receipt = await harness.system.receive_remote_answer_application(
                    application,
                    application_envelope,
                    accept,
                    store.record_application_in_transaction,
                )
                assert (
                    await harness.system.receive_remote_answer_application(
                        application,
                        application_envelope,
                        accept,
                        store.record_application_in_transaction,
                    )
                    == receipt
                )
                after_application = (await client.get("/api/conversation/conversations/c")).json()
                assert after_application[field] is None
                other_field = (
                    "pending_user_input" if kind is AskKind.permission else "pending_permission_ask"
                )
                other_identity = "request_id" if kind is AskKind.permission else "ask_id"
                assert after_application[other_field][other_identity] == ask.ask_id
                if kind is AskKind.permission:
                    assert after_application[other_field]["questions"][0]["question_id"] == "q"
                assert store.recorded_answer(ask) == answer
                # A later metadata record cannot erase the original saved-answer identity.
                await harness.store.save_executor_turn(
                    "c",
                    (
                        json.dumps(encode_value(child)),
                        json.dumps(encode_value(TurnToken("c", 8))),
                        json.dumps(
                            {
                                "ended": True,
                                "pending_permission_ask_ids": [],
                                "pending_user_input_questions": {},
                                "permission_provider_ids": {},
                                "user_input_provider_ids": {},
                                "explicit_reply_recipients": [],
                            }
                        ),
                    ),
                )
                assert store.public_request_id(ask) == ask.ask_id
                assert (await client.post(answer_path, json=body)).json() == {"landed": True}
                assert len(native_calls) == 1
                final_events = await harness.store.read_events_after("c", 0)
                assert getattr(final_events[-1].payload, identity_field) == ask.ask_id
        async with AsyncExitStack() as cleanup:
            successor = SignalRuntimeComposition(str(path), 1000)
            cleanup.callback(successor.close)
            successor.bootstrap()
            reopened = _Harness(path, signal_composition=successor, cleanup=cleanup)
            successor.initialize()
            attachment = successor.coordinator.attach_signal_publication(
                install=lambda connection, snapshot: None,
                participate=lambda connection, batch: None,
                unavailable=lambda reason: None,
            )
            cleanup.callback(attachment.close)
            fresh_endpoint = ExecutorEndpoint(str(path), RealClock())
            reopened.app.state.conversation = replace(
                reopened.runtime, executor_endpoint=fresh_endpoint
            )
            assert fresh_endpoint.store.public_request_id(ask) == ask.ask_id
            async with reopened.client() as client:
                assert (await client.post(answer_path, json=body)).json() == {"landed": True}
            assert store.recorded_answer(ask) == answer
            assert len(native_calls) == 1

    asyncio.run(exercise())
