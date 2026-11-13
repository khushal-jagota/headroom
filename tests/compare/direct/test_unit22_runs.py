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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.support.executor_presence import lease_request, mac_identity, make_current
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

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
from planner.conversation.message_content import text_message_content
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as FixedClock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.routing import LocaleBackendChildFactory
from planner.executor.skills import read_canonical_skill_snapshot
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService
from planner.executor.vps_runs import MacRuns
from planner.tickets.api import write_create_ticket

ROOT = Path(__file__).resolve().parents[3]
NOW = 1760000000000


def test_retained_runs_rebuild_drain_and_rewrite(
    tmp_path: Path, request: pytest.FixtureRequest
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
    with sqlite3.connect(path) as source, sqlite3.connect(candidate) as destination:
        source.backup(destination)

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

        class Core:
            async def emit_answer(self, turn: Any, emit: Any) -> None:
                await emit()

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
            await mailbox.put_child_command(steer)
            await mailbox.poll(old, 0, 0)
            await mailbox.put_child_command(prompt)
            await mailbox.put_child_command(answer)
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
    assert actual == expected
    assert actual["interruptions"][0]["recovery_state"] == "closed"
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
