"""Real inbox/Ticket writers and admitted native receipt custody, without a host.

The existing file proof ends before canonical receipt consumers. This pair covers
that gap and one still-settling dispatch. Core backend-event/answer bindings remain
with the retained-core slice. The recording sink invokes the real native participant.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.support.executor_presence import mac_identity, make_current
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.events import MessageToOwnerEventPayload
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as FixedClock
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.executor import protocol as wire
from planner.executor.store import ExecutorStore
from planner.executor.vps_inbox import MacInbox
from planner.tickets.api import write_create_ticket

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_760_000_000_000
IDENTITY = mac_identity("11111111-1111-4111-8111-111111111111")


def typed(name: str, value: Any) -> Any:
    return wire.type_adapter(getattr(wire, name)).validate_python(value)


async def outcome(work: Any) -> Any:
    try:
        return wire.encode_value(await work)
    except PlannerError as error:
        return error.to_payload()


def rows(path: str) -> dict[str, Any]:
    with closing(connect(path)) as conn:
        positions = [
            dict(row)
            for row in conn.execute(
                "SELECT sender_id,conversation_id,sequence,submission_id,disposition,result_json "
                "FROM mac_inbox_positions ORDER BY sender_id,conversation_id"
            )
        ]
        for row in positions:
            row["result_json"] = json.loads(row["result_json"])
        return {
            "positions": positions,
            "blockers": [
                list(row)
                for row in conn.execute(
                    "SELECT * FROM ticket_blocks ORDER BY blocked_ticket_id,blocking_ticket_id"
                )
            ],
        }


def test_unit22_inbox_canonical_consumers(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    if shutil.which("node") is None:
        pytest.fail("Node is required for the executor consumer comparison")
    homes = create_synthetic_homes(tmp_path)
    started = time.perf_counter()

    async def reference() -> tuple[dict[str, Any], dict[str, Any]]:
        source, candidate = tmp_path / "source.sqlite", tmp_path / "candidate.sqlite"
        clock = FixedClock(datetime.fromtimestamp(NOW / 1000, UTC))
        with closing(connect(str(source))) as conn:
            create_schema(conn)
            ticket = write_create_ticket(
                {
                    "worker_type": "coding",
                    "title": "Inbox fixture",
                    "brief": "Own canonical writes",
                },
                conn,
                RequestContext(Principal(PrincipalKind.owner, "owner")),
                clock,
            )["id"]
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,created_at) "
                "VALUES ('c','codex','fixture','/tmp','full',1760000000)"
            )
            conn.execute("UPDATE tickets SET conversation_id='c' WHERE id=?", (ticket,))
            conn.commit()
        make_current(str(source), IDENTITY, NOW)
        child = typed(
            "ChildReference",
            {
                "executor_incarnation": IDENTITY.executor_incarnation,
                "child_id": "child",
                "conversation_id": "c",
            },
        )
        store = ExecutorStore(str(source))
        store.register_child(child, "ticket", ticket, NOW)
        with sqlite3.connect(source) as left, sqlite3.connect(candidate) as right:
            left.backup(right)
        close_assignment = False

        def authority(reference: Any) -> Principal:
            assignment = store.child_assignment(reference)
            if assignment is None:
                raise PlannerError(ErrorCode.agent_forbidden, "child has no VPS assignment")
            if close_assignment:
                with closing(connect(str(source))) as conn:
                    conn.execute("UPDATE executor_child_assignments SET closed_at_ms=?", (NOW,))
                    conn.commit()
            return Principal(PrincipalKind(assignment[0]), assignment[1])

        runs = SimpleNamespace(authority=authority)
        inbox = MacInbox(str(source), clock, cast(Any, runs))
        gate, entered = asyncio.Event(), asyncio.Event()
        sent: list[str] = []

        async def record(conversation_id: str, content: Any, **options: Any) -> None:
            entered.set()
            await gate.wait()
            with closing(connect(str(source))) as conn:
                conn.execute("BEGIN IMMEDIATE")
                operation = options["recording_receipt"]
                payload = MessageToOwnerEventPayload(
                    content, options["sender"], options["recipient"], options["sender_label"]
                )
                operation.in_commit(conn, conversation_id, payload, None)
                conn.commit()
            sent.append(options["sender_message_id"])

        inbox.agent_writes.conversations = cast(
            Any, SimpleNamespace(record_message_to_owner=record)
        )

        def envelope(
            sequence: int, operation: str, arguments: dict[str, Any], submission: str | None = None
        ) -> dict[str, Any]:
            return {
                "sender_id": "native",
                "sequence": sequence,
                "submission_id": submission or f"item-{sequence}",
                "conversation_id": "c",
                "kind": "cli_write",
                "payload": wire.as_object(wire.CliWrite(operation, arguments, child, None)),
                "file_dependencies": [],
            }

        edits = [
            envelope(1, "ticket.edit", {"ticket_id": ticket, "body": {"recap": "Saved once"}}),
            envelope(2, "ticket.edit", {"ticket_id": ticket, "body": 9007199254740993}),
            envelope(
                3, "ticket.propose", {"ticket_id": ticket, "body": {"body": "A concrete Brief"}}
            ),
            envelope(4, "ticket.block", {"ticket_id": ticket, "blocker_id": ticket, "body": {}}),
            envelope(
                5,
                "ticket.request_help",
                {"ticket_id": ticket, "body": {"message": "help", "mode": "queue"}},
            ),
        ]
        output: dict[str, Any] = {}
        for index, value in enumerate(edits):
            output[f"write-{index}"] = await outcome(
                inbox.accept(typed("QueuedEnvelope", value), IDENTITY)
            )
            if index == 0:
                output["replay"] = await outcome(
                    inbox.accept(typed("QueuedEnvelope", value), IDENTITY)
                )
                conflict = {**value, "submission_id": "conflicting"}
                output["conflict"] = await outcome(
                    inbox.accept(typed("QueuedEnvelope", conflict), IDENTITY)
                )
        bad_batch = {
            "sender_id": "native",
            "items": [
                {"envelope": edits[0], "sender_refusal": None},
                {"envelope": edits[1], "sender_refusal": None},
            ],
        }
        output["batch"] = await outcome(
            inbox.accept_batch("c", typed("ItemBatch", bad_batch), IDENTITY)
        )
        refused = envelope(6, "ticket.edit", {"ticket_id": ticket, "body": {"recap": "never"}})
        output["sender-refusal"] = await outcome(
            inbox.refuse_item(typed("QueuedEnvelope", refused), IDENTITY, "capture failed")
        )
        native = envelope(
            7,
            "message.send",
            {"body": {"target": {"kind": "owner", "id": "owner"}, "message": "One native message"}},
        )
        pending = asyncio.create_task(
            outcome(
                inbox.accept(
                    typed("QueuedEnvelope", native),
                    IDENTITY,
                    retry_later_at=asyncio.get_running_loop().time() + 0.1,
                )
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        output["reservation"] = rows(str(source))
        output["pending"] = await pending
        following = envelope(8, "ticket.edit", {"ticket_id": ticket, "body": {"recap": "overtook"}})
        output["overtake"] = await outcome(
            inbox.accept(
                typed("QueuedEnvelope", following),
                IDENTITY,
                retry_later_at=asyncio.get_running_loop().time(),
            )
        )
        gate.set()
        output["native"] = await outcome(inbox.accept(typed("QueuedEnvelope", native), IDENTITY))
        output["sent"] = sent
        close_assignment = True
        changed = envelope(
            9, "ticket.edit", {"ticket_id": ticket, "body": {"recap": "closed child"}}
        )
        output["assignment"] = await outcome(
            inbox.accept(typed("QueuedEnvelope", changed), IDENTITY)
        )
        output["closed-replay"] = await outcome(
            inbox.accept(typed("QueuedEnvelope", changed), IDENTITY)
        )
        output["final"] = rows(str(source))
        return output, {
            "path": str(candidate),
            "identity": wire.as_object(IDENTITY),
            "child": wire.as_object(child),
            "ticket": ticket,
            "edits": edits,
            "bad_batch": bad_batch,
            "refused": refused,
            "native": native,
            "following": following,
            "changed": changed,
        }

    with (
        synthetic_home_environment(homes),
        patch("planner.executor.vps_inbox.now_ms", return_value=NOW),
        patch("planner.executor.vps_agent_writes.now_ms", return_value=NOW),
    ):
        expected, spec = asyncio.run(asyncio.wait_for(reference(), 10))
    environment = {
        **homes,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/inbox_consumers.ts")],
        input=json.dumps(spec),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    assert actual == expected
    assert expected["sent"] == ["item-7"]
    assert expected["reservation"]["positions"][0]["disposition"] == "dispatching"
    assert expected["native"]["result"]["fate"] == "recorded"
    assert expected["assignment"]["result"]["error"]["message"] == "child assignment changed"
    assert expected["closed-replay"] == expected["assignment"]
    assert (
        expected["pending"]["error"]["code"]
        == expected["overtake"]["error"]["code"]
        == "retry_later"
    )
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        target = Path(directory) / "unit-22-inbox-consumers.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "elapsed_seconds": time.perf_counter() - started,
                    "source": expected,
                    "candidate": actual,
                },
                indent=2,
            )
            + "\n"
        )
