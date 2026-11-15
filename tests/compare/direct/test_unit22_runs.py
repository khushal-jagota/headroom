"""Real run owners reconstitute replacement drains from populated durable state.

Mailbox checks prove row mechanics but do not bind run retirement, resumed prompt
custody, canonical skill reads, or locale routing. This one exchange reuses the
schema, lease fixture, native command codec and private homes without a listener.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID

import pytest
from tests.support.executor_presence import lease_request, mac_identity, make_current
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_conversation_system import _Harness, _start

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.deploy_interruptions.coordinator import (
    close_run_and_record_mac_process_interruption,
)
from planner.conversation.events import (
    PermissionAskedEventPayload,
    PermissionAskOption,
    PromptDeliveryUncertainEventPayload,
    UserInputAnswer,
    UserInputQuestion,
    UserInputRequestedEventPayload,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.lifecycle_storage import apply_facts, read_facts
from planner.conversation.logic.retained_turn import adopted_running_turn
from planner.conversation.message_content import text_message_content
from planner.conversation.start_admission import write_bindings
from planner.conversation.start_admission.contracts import StartRequest
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as FixedClock
from planner.core.contracts import PlannerError, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.registry import VpsExecutorRegistry
from planner.executor.routing import LocaleBackendChildFactory
from planner.executor.skills import read_canonical_skill_snapshot
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService
from planner.executor.vps_presence_store import accept_lease_in_transaction
from planner.executor.vps_runs import MacRuns
from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID
from planner.ticket_workspaces.contracts import RETENTION_SECONDS, WorkspaceIdentity, WorkspaceState
from planner.tickets.api import write_create_ticket

ROOT = Path(__file__).resolve().parents[3]
NOW = 1760000000000


@pytest.mark.xfail(
    run=False,
    reason=(
        "expected-open P2: #916 protocol24 replacement lease is refused by TS22, "
        "so the retained-run drain remains unsettled at exit13"
    ),
)
def test_retained_runs_rebuild_drain_and_rewrite(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    _runs_exchange(tmp_path, request)


def test_machine_run_custody_and_retained_recovery(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    _runs_exchange(tmp_path, request, machine_scope=True)


def _runs_exchange(
    tmp_path: Path, request: pytest.FixtureRequest, *, machine_scope: bool = False
) -> None:
    began = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    path, candidate = tmp_path / "source.sqlite", tmp_path / "candidate.sqlite"
    old = mac_identity("11111111-1111-4111-8111-111111111111")
    fresh = mac_identity("22222222-2222-4222-8222-222222222222")
    children = [ChildReference(old.executor_incarnation, name, name) for name in ("drain", "late")]
    with synthetic_home_environment(homes), closing(connect(str(path))) as conn:
        create_schema(conn)
        conn.execute("INSERT OR REPLACE INTO conversation_runtime_epoch VALUES (1,'epoch')")
        clock = FixedClock(datetime.fromtimestamp(NOW // 1000, UTC))

        def ticket(locale: str) -> str:
            return str(
                write_create_ticket(
                    {
                        "worker_type": "coding",
                        "title": f"Run fixture {locale}",
                        "brief": "Own run custody",
                        "locale": locale,
                    },
                    conn,
                    RequestContext(Principal(PrincipalKind.owner, "owner")),
                    clock,
                )["id"]
            )

        remote_ticket, local_ticket = ticket("macos"), ticket("vps")
        for child in children:
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
                "access,created_at) VALUES (?,'codex','fixture','/tmp','full',?)",
                (child.conversation_id, NOW // 1000),
            )
            conn.execute(
                "INSERT INTO conversation_lifecycle_facts "
                "VALUES (?,'turn','3',3,'turn','running',NULL)",
                (child.conversation_id,),
            )
        conn.execute(
            "UPDATE tickets SET conversation_id='drain',worker_step_claim='out',"
            "worker_step_claim_revision=4 WHERE id=?",
            (remote_ticket,),
        )
        asks: tuple[PermissionAskedEventPayload | UserInputRequestedEventPayload, ...] = (
            PermissionAskedEventPayload(
                "3:permission:ask",
                "Write file?",
                None,
                (PermissionAskOption("allow", "Allow once", "allow"),),
            ),
            UserInputRequestedEventPayload(
                "3:user_input:input",
                (UserInputQuestion("q", "Choice", "Which mode?", (), False, True),),
            ),
        )
        for sequence, ask in enumerate(asks, 1):
            conn.execute(
                "INSERT INTO conversation_events VALUES ('drain',?,?,?,?)",
                (
                    sequence,
                    ask.kind.value,
                    conversation_event_payload_to_canonical_json(ask),
                    NOW // 1000,
                ),
            )
        apply_facts(
            conn,
            "drain",
            (
                LifecycleFact(
                    LifecycleKind.request,
                    "3:permission:ask",
                    3,
                    LifecycleOperation.permission,
                    LifecycleStatus.answering,
                    "ask",
                ),
            ),
        )
        payload = PromptDeliveryUncertainEventPayload(
            text_message_content("steer"),
            "owner",
            PromptDeliveryMode.steer,
            sender_message_id="sender",
        )
        conn.execute(
            "INSERT INTO conversation_events VALUES ('late',1,'prompt_delivery_uncertain',?,?)",
            (conversation_event_payload_to_canonical_json(payload), NOW // 1000),
        )
        conn.execute(
            "INSERT INTO deploy_interruption_accounts "
            "VALUES ('capture','mac-process:old',?,'captured','fixture')",
            (NOW // 1000,),
        )
        conn.execute(
            "INSERT INTO deploy_interruptions VALUES "
            "('interruption','capture','mac-process:old','drain',3,NULL,NULL,'Fixture',"
            "'unknown',NULL,NULL,'running','awaiting_action','Original.',1)"
        )
        conn.commit()
    with synthetic_home_environment(homes):
        make_current(str(path), old, NOW)
    if machine_scope:
        other = "66666666-6666-4666-8666-666666666666"
        with closing(connect(str(path))) as conn:
            conn.execute(
                "INSERT INTO machines(machine_id,name,icon,color,is_home) "
                "VALUES (?,'Other','server','cyan',0)",
                (other,),
            )
            conn.execute(
                "INSERT INTO machine_executor_credentials(machine_id,credential_sha256) "
                "VALUES (?,?)",
                (other, "a" * 64),
            )
            accept_lease_in_transaction(conn, lease_request(fresh), NOW, other)
            conn.commit()
    with sqlite3.connect(path) as source, sqlite3.connect(candidate) as destination:
        source.backup(destination)
    if machine_scope:
        _machine_runs_exchange(
            path, candidate, homes, remote_ticket, local_ticket,
            [ORIGINAL_MAC_MACHINE_ID, other],
            [children[0], replace(children[1], executor_incarnation=fresh.executor_incarnation)],
            lease_request(
                replace(fresh, executor_incarnation="33333333-3333-4333-8333-333333333333")
            ),
            request, began,
        )
        return

    def command(child: ChildReference, name: str, operation: str) -> Any:
        arguments: Any = (
            wire.Steer(content=text_message_content("steer"), sender_label="owner")
            if operation == "steer"
            else wire.WritePrompt(
                content=text_message_content("prompt"),
                sender_content=text_message_content("sender"),
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
            )
        )
        return wire.ChildCommand(
            name,
            child,
            wire.ChildOperation(operation),
            TurnToken(child.conversation_id, 3),
            arguments,
        )

    steer, prompt = (
        command(children[1], "steer", "steer"),
        command(children[0], "prompt", "write_prompt"),
    )
    answer = wire.ChildCommand(
        "answer",
        children[0],
        wire.ChildOperation.answer_permission_ask,
        TurnToken("drain", 3),
        wire.AnswerPermission(ask_id="ask", option_id="allow"),
    )
    input_answer = wire.ChildCommand(
        "input-answer",
        children[0],
        wire.ChildOperation.answer_user_input,
        TurnToken("drain", 3),
        wire.AnswerUserInput(request_id="input", answers=(UserInputAnswer("q", ("fast",)),)),
    )
    spec = {
        "path": str(candidate),
        "now": NOW,
        "ticket": remote_ticket,
        "local_ticket": local_ticket,
        "old": wire.as_object(old),
        "fresh": wire.as_object(fresh),
        "children": [wire.as_object(child) for child in children],
        "steer": wire.as_object(steer),
        "prompt": wire.as_object(prompt),
        "answer": wire.as_object(answer),
        "input_answer": wire.as_object(input_answer),
    }

    async def reference() -> dict[str, Any]:
        mailbox = SqliteCommandMailbox(str(path), clock=lambda: NOW)
        presence = MacPresenceService(str(path), mailbox, clock=lambda: NOW)
        runs = MacRuns(str(path), presence, mailbox)
        trace: list[Any] = []
        output: dict[str, Any] = {}
        gate_path = tmp_path / "answer-gate.sqlite"
        with closing(connect(str(gate_path))) as conn:
            create_schema(conn)
        gate = _Harness(gate_path)
        await _start(gate, "drain", backend_key=ConversationBackendKey.codex)
        state = await gate.system._conversation_state("drain")
        assert state is not None
        with closing(connect(str(path))) as conn:
            state.running_turn = adopted_running_turn(
                TurnToken("drain", 3),
                read_facts(conn, "drain"),
                {},
                executor_process_current=True,
            )
        assert state.running_turn is not None

        class Core:
            async def emit_answer(self, turn: Any, emit: Any) -> None:
                await gate.system.emit_answer(turn, emit)

            async def retire_replaced_remote_child(self, child: Any, **options: Any) -> None:
                from dataclasses import asdict

                trace.append(
                    {
                        "retire": wire.as_object(child),
                        "options": {
                            key: asdict(value)
                            if value is not None and key == "undelivered_prompt"
                            else value
                            for key, value in options.items()
                        },
                    }
                )
                close_run_and_record_mac_process_interruption(
                    str(path),
                    child,
                    options["replaced_by"],
                    NOW,
                    undelivered_message=None
                    if options.get("undelivered_prompt") is None
                    else "prompt",
                )

            async def resume_undelivered_prompt(
                self, child: Any, prompt: Any, **options: Any
            ) -> None:
                from dataclasses import asdict

                trace.append(
                    {"resume": wire.as_object(child), "prompt": asdict(prompt), "options": options}
                )

            async def await_late_steer(
                self, conversation_id: str, sequence: int, verdict: Any
            ) -> None:
                trace.append({"watch": conversation_id, "sequence": sequence})
                verdict.add_done_callback(
                    lambda value: trace.append(
                        {"verdict": conversation_id, "not_delivered": value.result()}
                    )
                )

            async def confirm_remote_child_termination(self, child: Any) -> None:
                trace.append({"terminated": wire.as_object(child)})

        def snapshot() -> dict[str, Any]:
            from dataclasses import asdict

            with closing(connect(str(path))) as conn:
                commands = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT command_id,state,outcome_json FROM executor_commands "
                        "ORDER BY command_sequence"
                    )
                ]
                for row in commands:
                    row["outcome_json"] = (
                        None if row["outcome_json"] is None else json.loads(row["outcome_json"])
                    )
                return {
                    "commands": commands,
                    "runs": [asdict(run) for run in runs.store.retained_runs()],
                    "assignments": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM executor_child_assignments "
                            "ORDER BY executor_incarnation,child_id"
                        )
                    ],
                    "stalls": [
                        asdict(wait) for wait in runs.stall_source().tracked_waits(conn, NOW)
                    ],
                    "claims": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT id,worker_step_claim,worker_step_claim_revision "
                            "FROM tickets ORDER BY id"
                        )
                    ],
                }

        async def error(work: Any) -> Any:
            try:
                work()
                return None
            except Exception as error:
                return {"type": type(error).__name__, "message": str(error)}

        try:
            for child in children:
                runs.store.register_child(child, "ticket", remote_ticket, NOW)
            runs.conversations = cast(Any, Core())
            await mailbox.put_child_command(steer)
            # A finished answer lacks its application receipt, so recovery must not
            # claim definite non-delivery as it does for the other, withdrawn answer.
            output["answer_gate"] = [
                await runs.save_answer(
                    replace(
                        answer,
                        command_id="wrong-turn",
                        turn=TurnToken("drain", 4),
                    )
                )
            ]
            state.running_turn.callbacks_revoked = True
            output["answer_gate"].append(
                await runs.save_answer(
                    replace(
                        answer,
                        command_id="revoked-answer",
                    )
                )
            )
            state.running_turn.callbacks_revoked = False
            await runs.save_answer(
                replace(
                    answer,
                    command_id="wrong-ask",
                    arguments=wire.AnswerPermission(ask_id="closed-ask", option_id="allow"),
                )
            )
            output["answer_gate"].append(await runs.save_answer(answer))
            delivered = await mailbox.poll(old, 0, 0)
            assert all(row.command_id != "wrong-ask" for row in delivered.commands)
            wrong_ask = mailbox.command_row("wrong-ask")
            assert wrong_ask is not None and wrong_ask.result is not None
            assert wrong_ask.result.child_outcome is not None
            output["closed_ask"] = [
                wrong_ask.state.value,
                wrong_ask.result.child_outcome.result["reason"],
            ]
            recorded = await mailbox.record_results(
                old,
                wire.CommandOutcomeReport(
                    (
                        wire.CommandResult(
                            answer.command_id,
                            wire.CommandOutcome(
                                answer.command_id,
                                answer.child,
                                wire.CommandDisposition.succeeded,
                                {},
                            ),
                        ),
                    )
                ),
            )
            assert recorded.recorded == (answer.command_id,)
            await mailbox.put_child_command(prompt)
            await mailbox.put_child_command(input_answer)
            output["before"] = snapshot()
            await presence.accept_lease(
                lease_request(
                    fresh,
                    waiting=tuple(wire.WaitingConversation(name, 1) for name in ("drain", "late")),
                )
            )
            await runs.close()
            runs = MacRuns(str(path), presence, mailbox)
            runs.conversations = cast(Any, Core())
            await runs.resume_replacement_drains()
            output["waiting"] = snapshot()
            await mailbox.poll(fresh, 0, 0)
            await presence.accept_lease(lease_request(fresh))
            await asyncio.gather(*tuple(runs._tasks))
            output["drained"] = snapshot()
            output["retained_recovery"] = runs.recover(children[0]) is None
            next_child = ChildReference(fresh.executor_incarnation, "next", "drain")
            runs.store.register_child(next_child, "ticket", remote_ticket, NOW)
            runs.store.close_child(children[0], NOW + 1)
            output["historical_authority"] = await error(lambda: runs.authority(children[0]))
            output["next_authority"] = {
                "kind": runs.authority(next_child).kind.value,
                "id": runs.authority(next_child).id,
            }
            output["exact_stop_refusal"] = await error(
                lambda: runs.validate_stop(
                    wire.CommandOutcome("prompt", next_child, wire.CommandDisposition.succeeded, {})
                )
            )
            recovered = runs.recover(next_child)
            assert recovered is not None
            output["recovered_history"] = recovered.initial_history_complete
            await runs.resume_replacement_drains()
            await asyncio.gather(*tuple(runs._tasks))
            await mailbox.put_child_command(command(next_child, "rewritten", "write_prompt"))
            await runs.resume_replacement_drains()
            await asyncio.gather(*tuple(runs._tasks))
            with closing(connect(str(path))) as conn:
                output["interruptions"] = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT interruption_id,recovery_state,detail FROM deploy_interruptions "
                        "ORDER BY interruption_id"
                    )
                ]
                output["skills"] = wire.as_object(
                    read_canonical_skill_snapshot(conn, remote_ticket)
                )
            output["after_rewrite"] = snapshot()
            choices: list[str] = []

            def local(**options: Any) -> None:
                choices.append("local")

            class Remote:
                def __call__(self, **options: Any) -> None:
                    choices.append("remote")

                def recover(self, reference: Any, **options: Any) -> None:
                    choices.append("recover")

            # These record only selection. No backend method or file service runs.
            router = LocaleBackendChildFactory(str(path), cast(Any, local), cast(Any, Remote()))
            for ticket_id in (remote_ticket, local_ticket, None):
                start = ResolvedConversationStart(
                    "routing",
                    ConversationBackendKey.codex,
                    "fixture",
                    None,
                    None
                    if ticket_id is None
                    else ConversationRoleMaterials(
                        "fixture", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket_id))
                    ),
                    Path("/tmp"),
                    ConversationAccess.full,
                )
                router(
                    resolved_start=start, event_sink=cast(Any, None), message_files=cast(Any, None)
                )
            router.recover(next_child)
            output["choices"] = choices
            output["trace"] = trace
            return output
        finally:
            await runs.close()
            await presence.close()
            await mailbox.close()
            await gate.system.shutdown()
            assert not gate.spawned_conversation_ids

    with (
        synthetic_home_environment(homes),
        patch("planner.executor.vps_runs.now_ms", return_value=NOW),
        patch("planner.executor.vps_mailbox.now_ms", return_value=NOW),
    ):
        expected = json.loads(json.dumps(asyncio.run(asyncio.wait_for(reference(), 15))))
    env = {
        **homes,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/runs.ts")],
        input=json.dumps(spec),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    assert actual.pop("sealed")["type"] == "ServerLifecycleError"
    assert actual.pop("pending_shutdown") == {
        "unrelated_survives": True,
        "unrelated_drained": True,
    }
    assert actual.pop("retry_shutdown") == 1
    for side in (actual, expected):
        side["trace"].sort(key=lambda value: json.dumps(value, sort_keys=True))
    # The exact-stop refusal retains its message and meaning through the native
    # domain error class. All run custody and durable state still compare exactly.
    assert expected["exact_stop_refusal"]["type"] == "ValueError"
    comparable_expected = {
        **expected,
        "exact_stop_refusal": {
            **expected["exact_stop_refusal"],
            "type": "ExecutorValidationError",
        },
    }
    assert actual == comparable_expected
    assert actual["answer_gate"] == [False, False, True]
    assert actual["closed_ask"] == ["expired", "answered_request_not_open"]
    assert actual["interruptions"][0]["recovery_state"] == "closed"
    detail = actual["interruptions"][0]["detail"]
    assert (
        "The answer was: Allow once. That answer may already have reached you before the restart."
        in detail
    )
    assert "The answer was: fast. That answer never reached you." in detail
    report = request.config.getoption("--compare-report-dir", default=None)
    if report:
        target = Path(report)
        target.mkdir(parents=True, exist_ok=True)
        (target / "unit22-runs.json").write_text(
            json.dumps(
                {"seconds": time.perf_counter() - began, "expected": expected, "actual": actual},
                indent=2,
            )
        )


def _machine_runs_exchange(
    path: Path,
    candidate: Path,
    homes: dict[str, str],
    ticket: str,
    local_ticket: str,
    machines: list[str],
    children: list[ChildReference],
    replacement: wire.LeaseRequest,
    request: pytest.FixtureRequest,
    began: float,
) -> None:
    def rows() -> dict[str, Any]:
        with closing(connect(str(path))) as conn:
            return {
                key: [dict(row) for row in conn.execute(query)]
                for key, query in {
                    "runs": "SELECT * FROM mac_runs ORDER BY conversation_id",
                    "assignments": (
                        "SELECT * FROM executor_child_assignments "
                        "ORDER BY executor_incarnation,child_id"
                    ),
                    "commands": "SELECT * FROM executor_commands ORDER BY command_sequence",
                }.items()
            }

    async def reference() -> dict[str, Any]:
        clock = FixedClock(datetime.fromtimestamp(NOW // 1000, UTC))
        registry = VpsExecutorRegistry(str(path), clock, original_enabled=True)
        with closing(connect(str(path))) as conn:
            registry.load(conn)
        endpoints = [registry.get(machine) for machine in machines]
        assert all(endpoint is not None for endpoint in endpoints)
        owners = [cast(Any, endpoint).runs for endpoint in endpoints]

        def snapshot() -> list[dict[str, Any]]:
            with closing(connect(str(path))) as conn:
                return [
                    dict(
                        machine_id=owner.machine_id,
                        retained=owner.retained_runs(),
                        unresolved=owner.store.unresolved_runs_of_replaced_incarnations(),
                        owns=[
                            owner.owns_conversation(child.conversation_id) for child in children
                        ],
                        stalls=owner.stall_source().tracked_waits(conn, NOW),
                    )
                    for owner in owners
                ]

        def attempt(work: Any) -> str | None:
            try:
                work()
                return None
            except Exception as error:
                return error.code.value if isinstance(error, PlannerError) else type(error).__name__

        def foreign_refusals(owner: MacRuns, foreign: ChildReference) -> list[str | None]:
            return [
                attempt(lambda: owner.recover(foreign)),
                attempt(lambda: owner.authority(foreign)),
                attempt(lambda: owner.store.has_closed_child_assignment(foreign)),
                attempt(lambda: owner.store.register_child(foreign, "ticket", ticket, NOW)),
            ]

        try:
            named_registry = VpsExecutorRegistry(str(path), clock)
            try:
                with closing(connect(str(path))) as conn:
                    named_registry.load(conn)
                named_only = [
                    named_registry.get(machines[0]) is None,
                    named_registry.get(machines[1]) is not None,
                ]
            finally:
                await named_registry.close()
            loaded = [registry.get(machine) is not None for machine in machines]
            unknown = "77777777-7777-4777-8777-777777777777"
            unknown_present = registry.is_present(unknown)
            unknown_allocated = registry.get(unknown) is not None
            refused_destinations = []
            for machine in (unknown, HOME_MACHINE_ID):
                try:
                    await registry.ensure(machine)
                    refused_destinations.append(False)
                except ValueError:
                    refused_destinations.append(True)
            catalogs = [
                registry.catalog_for(machine) is cast(Any, endpoint).catalog
                for machine, endpoint in zip(machines, endpoints, strict=True)
            ]
            for owner, child in zip(owners, children, strict=True):
                owner.store.register_child(child, "ticket", ticket, NOW)
            before, original = snapshot(), rows()
            refused = [
                foreign_refusals(owner, children[1 - index])
                for index, owner in enumerate(owners)
            ]
            unchanged = rows() == original
            choices: list[str] = []

            def selected(owner: MacRuns, **options: Any) -> Any:
                choices.append(owner.machine_id)
                raise RuntimeError("selection only")

            def local(**options: Any) -> Any:
                choices.append("local")

            router = LocaleBackendChildFactory(str(path), cast(Any, local), registry)
            start = ResolvedConversationStart(
                "routing", ConversationBackendKey.codex, "fixture", None,
                ConversationRoleMaterials(
                    "fixture", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket))
                ),
                Path("/tmp"), ConversationAccess.full,
            )
            with patch.object(MacRuns, "__call__", selected):
                for machine in (*machines, HOME_MACHINE_ID):
                    with closing(connect(str(path))) as conn:
                        conn.execute(
                            "UPDATE tickets SET machine_id=? WHERE id=?", (machine, ticket)
                        )
                        conn.commit()
                    try:
                        router(
                            resolved_start=start,
                            event_sink=cast(Any, None),
                            message_files=cast(Any, None),
                        )
                    except RuntimeError as error:
                        assert str(error) == "selection only"
            recovered = []
            for child in children:
                adopted = registry.recover(child)
                assert adopted is not None
                recovered.append(adopted.initial_history_complete)
            acknowledgement = await _prompt_acknowledgement_exchange(
                path, registry, machines[0], children[0]
            )
            with closing(connect(str(path))) as conn:
                accept_lease_in_transaction(conn, replacement, NOW + 1, machines[1])
                conn.commit()
            replaced = snapshot()
            with closing(connect(str(path))) as conn:
                tagged_stalls = [
                    wait for source in registry.stall_sources()
                    for wait in source.tracked_waits(conn, NOW)
                    if wait.kind == "mac_replacement_pending"
                ]
            retained = owners[1].recover(children[1])
            assert retained is not None
            owners[0].store.close_child(children[1], NOW + 2)
            cleanup = await _cleanup_recheck_exchange(
                path, registry, machines, children[1], ticket
            )
            workspaces = await _workspace_authority_exchange(
                path, registry, machines, children, ticket, local_ticket
            )
            return dict(
                before=before,
                acknowledgement=acknowledgement,
                cleanup=cleanup,
                workspaces=workspaces,
                loaded=loaded,
                named_only=named_only,
                unknown_present=unknown_present,
                unknown_allocated=unknown_allocated,
                refused_destinations=refused_destinations,
                catalogs=catalogs,
                choices=choices,
                tagged_stalls=tagged_stalls,
                refused=refused,
                unchanged=unchanged,
                recovered=recovered,
                replaced=replaced,
                retained_recovery=retained.initial_history_complete,
                closed=owners[1].store.has_closed_child_assignment(children[1]),
                closed_recovery=owners[1].recover(children[1]) is None,
                rows=rows(),
            )
        finally:
            await registry.close()

    with synthetic_home_environment(homes):
        expected = wire.encode_value(asyncio.run(reference()))
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/runs.ts")],
        cwd=ROOT,
        input=json.dumps(dict(
            mode="machines",
            path=str(candidate),
            now=NOW,
            ticket=ticket,
            local_ticket=local_ticket,
            machines=machines,
            children=[wire.as_object(child) for child in children],
            replacement=wire.as_object(replacement),
        )),
        env={
            **homes,
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TMPDIR": str(path.parent),
            "PLAN_TEST_MODE": "1",
            "PLAN_SERVER_URL": "http://127.0.0.1:1",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    assert actual == expected
    assert actual["unchanged"] and actual["recovered"] == [False, False]
    assert actual["refused"] == [["agent_forbidden"] * 3 + ["BackendSpawnFailed"]] * 2
    assert actual["replaced"][0]["unresolved"] == []
    assert actual["replaced"][1]["unresolved"] == [wire.as_object(children[1])]
    assert actual["loaded"] == actual["catalogs"] == [True, True]
    assert actual["named_only"] == [True, True]
    assert not actual["unknown_present"] and not actual["unknown_allocated"]
    assert actual["refused_destinations"] == [True, True]
    assert actual["choices"] == [*machines, "local"]
    acknowledgement = actual["acknowledgement"]
    assert acknowledgement["before"] == [{
        "identity": "ack-proof", "work_class": "worker", "destination": machines[0],
        "conversation_id": children[0].conversation_id, "turn_number": 3,
        "executor_incarnation": None, "child_id": None,
    }]
    assert acknowledgement["after_filters"] == acknowledgement["before"]
    assert acknowledgement["calls_after_filters"] == 0
    assert acknowledgement["after_proven"] == []
    assert len(acknowledgement["confirmations"]) == 1
    assert acknowledgement["confirmations"][0]["committed"]["state"] == "finished"
    workspaces = actual["workspaces"]
    assert workspaces["historical"] == [workspaces["states"]["historical"]] * 2
    assert workspaces["states"]["historical"]["states"] == [{
        "ticket_id": ticket, "ended_at": None, "deleted": False,
    }]
    assert workspaces["states"]["current"]["states"] == [{
        "ticket_id": local_ticket, "ended_at": None, "deleted": False,
    }]
    assert all(refusal["unchanged"] for refusal in workspaces["refused"])
    assert [refusal["error"]["error"]["code"] for refusal in workspaces["refused"]] == [
        "agent_forbidden", "executor_incarnation_not_current", "agent_forbidden",
        "agent_forbidden", "agent_forbidden", "agent_forbidden",
    ]
    assert workspaces["registered"] == [{
        "conversation_id": children[0].conversation_id, "ticket_id": ticket,
        **workspaces["physical"],
    }]
    assert workspaces["physical"]["machine_id"] not in machines
    assert workspaces["retirement"] == [False, False, False, True, False, True]
    assert workspaces["grants"] == [{
        "ticket_id": ticket, **workspaces["physical"], "authorized_at": NOW // 1000,
    }]
    assert [wait["machine_id"] for wait in actual["tagged_stalls"]] == [machines[1]]
    cleanup = actual["cleanup"]
    assert cleanup["held"] == [True] * 5 and all(cleanup["budgets"])
    assert cleanup["blocked"]["complete"] and cleanup["recent"]["complete"]
    assert not any(cleanup[key]["complete"] for key in (
        "wrong_ticket", "missing_source", "mailbox_refused", "refused", "incomplete",
        "no_result", "expired", "unreachable",
    ))
    assert cleanup["wrong_machine"] == "agent_forbidden"
    for command in cleanup["requests"]:
        assert command["child"] == {
            **wire.as_object(children[1]),
            "executor_incarnation": replacement.identity.executor_incarnation,
        }
        assert command["arguments"] == {
            "kind": "recheck_cleanup", "source": wire.as_object(children[1]), "ticket_id": ticket,
        }
    report = request.config.getoption("--compare-report-dir", default=None)
    if report:
        target = Path(report) / "unit22-machine-runs.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(
            dict(seconds=time.perf_counter() - began, expected=expected, actual=actual),
            indent=2,
        ))


async def _workspace_authority_exchange(
    path: Path, registry: VpsExecutorRegistry, machines: list[str],
    children: list[ChildReference], ticket: str, local_ticket: str,
) -> dict[str, Any]:
    """Separate historical executor custody from an exact physical retirement grant."""
    first, second = (registry.get(machine) for machine in machines)
    assert first is not None and second is not None
    current = first.presence.current_identity()
    other = second.presence.current_identity()
    assert current is not None and other is not None
    physical = WorkspaceIdentity(
        "88888888-8888-4888-8888-888888888888", "99999999-9999-4999-8999-999999999999"
    )
    registration = wire.WorkspaceRegistrationRequest(children[0], physical)

    def rows() -> dict[str, Any]:
        with closing(connect(str(path))) as conn:
            return {
                name: [dict(row) for row in conn.execute(query)]
                for name, query in {
                    "registered": (
                        "SELECT * FROM ticket_workspace_conversations ORDER BY conversation_id"
                    ),
                    "grants": (
                        "SELECT * FROM ticket_workspace_retirements "
                        "ORDER BY ticket_id,machine_id,workspace_id"
                    ),
                    "lifecycle": (
                        "SELECT * FROM ticket_workspace_lifecycle ORDER BY ticket_id"
                    ),
                }.items()
            }

    async def refused(work: Any) -> dict[str, Any]:
        before = rows()
        try:
            await work
        except PlannerError as error:
            return {"error": error.to_payload(), "unchanged": rows() == before}
        raise AssertionError("Workspace authority unexpectedly accepted the request")

    with closing(connect(str(path))) as conn:
        conn.execute("UPDATE tickets SET machine_id=? WHERE id=?", (machines[1], local_ticket))
        conn.commit()
    historical = wire.WorkspaceStatesRequest((ticket, "t_zzzzzzzz"))
    current_request = wire.WorkspaceStatesRequest((local_ticket,))
    states = {
        "historical": await first.workspace_states(current, historical),
        "current": await second.workspace_states(other, current_request),
    }
    result: dict[str, Any] = {
        "states": states,
        "historical": [states["historical"], await second.workspace_states(other, historical)],
        "physical": physical,
        "refused": [
            await refused(first.workspace_states(current, current_request)),
            await refused(second.register_workspace(
                replace(other, executor_incarnation=children[1].executor_incarnation),
                replace(registration, child=children[1]),
            )),
            await refused(first.register_workspace(current, replace(
                registration, child=replace(children[0], child_id="missing-child")
            ))),
            await refused(first.register_workspace(
                current, replace(registration, child=children[1])
            )),
        ],
    }
    await first.register_workspace(current, registration)
    result["registered"] = rows()["registered"]
    ended = NOW // 1000 - RETENTION_SECONDS - 1
    state = WorkspaceState(ticket, ended, False)
    with closing(connect(str(path))) as conn:
        conn.execute(
            "UPDATE ticket_workspace_lifecycle SET ended_at=? WHERE ticket_id=?", (ended, ticket)
        )
        conn.commit()
    result["refused"].extend([
        await refused(second.retire_workspace(other, state, physical)),
        await refused(first.retire_workspace(
            current, state,
            replace(physical, workspace_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        )),
    ])
    retirement = [
        (await first.retire_workspace(current, state, physical, replay=True)).authorized,
        (await first.retire_workspace(
            current, replace(state, ended_at=ended - 1), physical
        )).authorized,
        (await first.retire_workspace(current, state, physical)).authorized,
    ]
    with closing(connect(str(path))) as conn:
        conn.execute("UPDATE tickets SET worker_step_claim='none' WHERE id=?", (ticket,))
        conn.commit()
    retirement.append((await first.retire_workspace(current, state, physical)).authorized)
    with closing(connect(str(path))) as conn:
        conn.execute(
            "UPDATE ticket_workspace_lifecycle SET ended_at=NULL WHERE ticket_id=?", (ticket,)
        )
        conn.commit()
    retirement.extend([
        (await first.retire_workspace(current, state, physical)).authorized,
        (await first.retire_workspace(current, state, physical, replay=True)).authorized,
    ])
    result.update(retirement=retirement, grants=rows()["grants"])
    return result


async def _cleanup_recheck_exchange(
    path: Path, registry: VpsExecutorRegistry, machines: list[str],
    source: ChildReference, ticket: str,
) -> dict[str, Any]:
    endpoint = registry.get(machines[1])
    other = registry.get(machines[0])
    assert endpoint is not None and other is not None
    presence, mailbox = endpoint.presence, endpoint.mailbox
    presence._clock = other.presence._clock = mailbox._clock = lambda: NOW
    base = presence.record()
    assert base is not None and base.lease is not None
    requests: list[dict[str, Any]] = []
    ids: list[str] = []
    budgets: list[bool] = []
    current = presence.current_identity()
    assert current is not None
    result: dict[str, Any] = {}
    result["wrong_ticket"] = await registry.recheck_cleanup(source, "missing-ticket")
    result["missing_source"] = await registry.recheck_cleanup(
        replace(source, child_id="missing-child"), ticket
    )
    try:
        await other.runs.recheck_cleanup(source, ticket)
    except PlannerError as error:
        result["wrong_machine"] = error.code.value
    result["mailbox_refused"] = await endpoint.runs.recheck_cleanup(source, "missing-ticket")
    presence._clock = lambda: NOW + 61_000
    try:
        result["unreachable"] = await registry.recheck_cleanup(source, ticket)
    finally:
        presence._clock = lambda: NOW
    original_put, original_wait, original_drain = (
        mailbox.put_child_command, mailbox.wait_result, presence.wait_until_drained
    )

    async def execute(mode: str) -> Any:
        entered = asyncio.Event()
        reply: wire.CommandResult | None = None

        async def put(command: wire.ChildCommand) -> Any:
            nonlocal reply
            placed = await original_put(command)
            assert isinstance(placed, wire.MailboxCommand)
            assert str(UUID(command.command_id)) == command.command_id
            assert command.command_id not in ids
            ids.append(command.command_id)
            requests.append({
                **wire.as_object(command), "command_id": f"cleanup-command-{len(ids)}",
            })
            await mailbox.poll(current, 0, 0)
            outcome = wire.CommandOutcome(
                command.command_id, command.child,
                wire.CommandDisposition.refused if mode == "refused"
                else wire.CommandDisposition.succeeded,
                {"complete": mode != "incomplete", "reason": "fixture cleanup"},
            )
            reply = wire.CommandResult(command.command_id, outcome)
            await mailbox.record_results(
                current, wire.CommandOutcomeReport((reply,))
            )
            row = mailbox.command_row(command.command_id)
            assert row is not None and row.result is not None
            assert row.command.command_id == row.result.command_id == command.command_id
            assert row.result.child_outcome is not None
            assert row.result.child_outcome.command_id == command.command_id
            return placed

        async def wait(command_id: str, timeout_seconds: float | None = None) -> Any:
            assert timeout_seconds is not None
            budgets.append(0 <= timeout_seconds <= 30)
            if mode == "no_result":
                return None
            if mode == "expired":
                return reply
            return await original_wait(command_id, timeout_seconds)

        async def drain(conversation_id: str) -> None:
            entered.set()
            await original_drain(conversation_id)

        with (
            patch.object(mailbox, "put_child_command", put),
            patch.object(mailbox, "wait_result", wait),
            patch.object(presence, "wait_until_drained", drain),
            patch("planner.executor.vps_runs.CLEANUP_RECHECK_TIMEOUT_SECONDS",
                  -1 if mode == "expired" else 30),
        ):
            if mode == "blocked":
                presence._record = replace(base, last_lease_at_ms=NOW - 26_000)
            task = asyncio.create_task(registry.recheck_cleanup(source, ticket))
            if mode == "blocked":
                await entered.wait()
                held = [not task.done()]
                lease = base.lease
                assert lease is not None
                for changed in (
                    replace(lease, waiting_conversations_truncated=True),
                    replace(lease, waiting_conversations=(wire.WaitingConversation(
                        source.conversation_id, 1),)),
                    replace(lease, cleanup_uncertainties=(wire.WorkerCleanupUncertainty(
                        ticket, source.conversation_id, "still pending"),)),
                    replace(lease, identity=replace(lease.identity, executor_incarnation=
                            "99999999-9999-4999-8999-999999999999")),
                ):
                    presence._record = replace(base, last_lease_at_ms=NOW, lease=changed)
                    presence._wake_drains()
                    await asyncio.sleep(0)
                    held.append(not task.done())
                presence._record = base
                presence._wake_drains()
                result["held"] = held
            return await task

    try:
        for mode in ("refused", "incomplete", "no_result", "blocked", "recent", "expired"):
            result[mode] = await execute(mode)
        result["requests"], result["budgets"] = requests, budgets
        return result
    finally:
        presence._record = base
        with closing(connect(str(path))) as conn:
            conn.executemany(
                "DELETE FROM executor_commands WHERE command_id=?", [(key,) for key in ids]
            )
            conn.commit()


async def _prompt_acknowledgement_exchange(
    path: Path, registry: VpsExecutorRegistry, machine: str, source: ChildReference
) -> dict[str, Any]:
    endpoint = registry.get(machine)
    assert endpoint is not None
    owner, mailbox = endpoint.runs, endpoint.mailbox
    turn = TurnToken(source.conversation_id, 3)
    with closing(connect(str(path))) as conn:
        write_bindings(conn, turn, (StartRequest("ack-proof", "worker", machine),))
        conn.commit()
    harness = _Harness(path, factory_wrapper=lambda _: registry)
    core = harness.system
    previous = getattr(owner, "conversations", None)
    owner.conversations = core
    confirmations: list[dict[str, Any]] = []
    original = core.confirm_remote_prompt_accepted

    def bindings() -> list[dict[str, Any]]:
        with closing(connect(str(path))) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM automatic_start_bindings ORDER BY identity"
            )]

    async def observed(reference: ChildReference, token: TurnToken) -> None:
        with closing(connect(str(path))) as fresh:
            row = fresh.execute(
                "SELECT state,outcome_json FROM executor_commands WHERE command_id='ack-proven'"
            ).fetchone()
        confirmations.append({
            "child": wire.encode_value(reference), "turn": wire.encode_value(token),
            "committed": None if row is None else {
                "state": row["state"], "outcome": json.loads(row["outcome_json"]),
            },
        })
        await original(reference, token)

    prompt = wire.ChildCommand(
        "ack-generic", source, wire.ChildOperation.write_prompt, turn,
        wire.WritePrompt(
            content=text_message_content("ack"), sender_content=text_message_content("ack"),
            sender_label="fixture", mode=PromptDeliveryMode.queue,
        ),
    )
    current = endpoint.presence.current_identity()
    assert current is not None

    async def settle(command: wire.ChildCommand, payload: dict[str, Any]) -> None:
        placed = await mailbox.put_child_command(command)
        assert isinstance(placed, wire.MailboxCommand)
        await mailbox.poll(current, 0, 0)
        await mailbox.record_results(current, wire.CommandOutcomeReport((wire.CommandResult(
            command.command_id, wire.CommandOutcome(
                command.command_id, command.child, wire.CommandDisposition.succeeded, payload
            ),
        ),)))

    try:
        with patch.object(core, "confirm_remote_prompt_accepted", observed):
            before = bindings()
            await settle(prompt, {"kind": "prompt_accepted", "composed_content_delivered": True})
            for command, outcome in (
                (prompt, wire.CommandOutcome("ack-generic", source,
                    wire.CommandDisposition.succeeded, {"agent_acceptance_proven": False})),
                (prompt, wire.CommandOutcome("ack-generic", replace(source, child_id="wrong"),
                    wire.CommandDisposition.succeeded, {"agent_acceptance_proven": True})),
                (prompt, wire.CommandOutcome("ack-generic", source,
                    wire.CommandDisposition.refused, {"agent_acceptance_proven": True})),
            ):
                owner._command_settled(
                    wire.MailboxCommand(1, command.command_id, NOW, child_command=command),
                    wire.CommandResult(command.command_id, outcome),
                )
            await asyncio.sleep(0)
            after_filters, calls_after_filters = bindings(), len(confirmations)
            await settle(replace(prompt, command_id="ack-proven"), {
                "kind": "prompt_accepted", "composed_content_delivered": True,
                "agent_acceptance_proven": True,
            })
            await asyncio.gather(*tuple(owner._tasks))
            return {
                "before": before, "after_filters": after_filters,
                "calls_after_filters": calls_after_filters,
                "confirmations": confirmations, "after_proven": bindings(),
            }
    finally:
        await core.shutdown()
        if previous is None:
            del owner.conversations
        else:
            owner.conversations = previous
        assert not harness.spawned_conversation_ids
        with closing(connect(str(path))) as conn:
            conn.execute(
                "DELETE FROM executor_commands WHERE command_id IN ('ack-generic','ack-proven')"
            )
            conn.commit()
