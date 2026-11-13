"""One paired real-wire executor case using the existing owned TLS proxy.

Raw framing, streaming/disconnect custody, and ingress cannot be proved by injection.
This reuses the existing transport app and TLS proxy, with one Node companion host.
Update service policy remains its owning unit's proof: these recording services test
only the route contract. No URL outside the exact fixture-owned origin can connect.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from tests.integration.test_executor_app_transport import _reads_app, _transport_app
from tests.integration.test_executor_end_to_end import ExecutorHarness
from tests.integration.test_executor_isolation import held_mac_writes, writer_lock_is_free
from tests.support.executor_https import HttpsProxy, isolated_https_proxy, serve_executor_app
from tests.support.executor_presence import lease_request, mac_identity, make_current
from tests.support.stall_alarm_fakes import FakeChief, FakeWebPush
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import MessageFile, text_message_content
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as FixedClock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import UpdateConflict
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.update_contracts import MacUpdateCheck, MacUpdateReport, MacUpdateStatus
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_transport import VpsExecutor
from planner.managers.data import create_manager
from planner.notifications import data as notifications_data
from planner.stalls.checker import StallChecker
from planner.stalls.registry import WAITS
from planner.stalls.watchdog import run_watchdog
from planner.tickets.api import write_create_ticket
from planner.tickets.data import (
    accept_proposal,
    claim_ticket_for_worker_step,
    write_ticket_conversation_start,
)

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_760_000_000_000
IDENTITY = mac_identity("11111111-1111-4111-8111-111111111111")
OWNED_ORIGINS: set[str] = set()


def owned_url(origin: str, path: str) -> str:
    if origin not in OWNED_ORIGINS:
        raise ValueError("the origin is not a listener owned by this fixture")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise ValueError("only the fixture-owned TLS listener is allowed")
    result = origin + path
    actual = urlsplit(result)
    if (
        (actual.scheme, actual.netloc) != (parsed.scheme, parsed.netloc)
        or not path.startswith("/")
        or path.startswith("//")
    ):
        raise ValueError("executor request left its owned listener")
    return result


class RecordingUpdates(MacUpdateService):
    def __init__(self, archive: Path) -> None:
        self.archive_file = archive
        self.reports: list[Any] = []

    def check(self) -> MacUpdateCheck:
        return MacUpdateCheck(1, None)

    def archive(self, request_id: str) -> Path:
        if request_id != "fixture":
            raise UpdateConflict("unknown fixture archive")
        return self.archive_file

    def prefetch_archive(self, sha256: str) -> Path:
        if sha256 != hashlib.sha256(self.archive_file.read_bytes()).hexdigest():
            raise UpdateConflict("unknown fixture archive")
        return self.archive_file

    def report(self, report: MacUpdateReport, health: Any) -> MacUpdateStatus:
        self.reports.append([wire.encode_value(report), wire.encode_value(health)])
        return MacUpdateStatus(report.request_id, report.application_revision, report.phase)


async def transcript(proxy: HttpsProxy, spec: dict[str, Any]) -> list[Any]:
    only_id = {"X-Panels-Executor-ID": "test-executor"}
    work = {
        **only_id,
        "X-Panels-Executor-Incarnation": IDENTITY.executor_incarnation,
        "X-Panels-Executor-Protocol": "22",
    }
    result: list[Any] = []
    async with proxy.session_factory() as client:

        async def call(
            method: str, path: str, data: bytes | None = None, headers: Any = None
        ) -> None:
            chosen = work if headers is None else headers
            request_headers = (
                {"Accept-Encoding": "identity", **chosen}
                if isinstance(chosen, dict)
                else [("Accept-Encoding", "identity"), *chosen]
            )
            async with client.request(
                method,
                owned_url(proxy.origin, path),
                headers=request_headers,
                data=data,
                allow_redirects=False,
            ) as response:
                body = await response.read()
                content = (
                    json.loads(body)
                    if response.headers.get("content-type") == "application/json"
                    else {"length": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                )
                selected: dict[str, str | None] = {
                    name: response.headers[name]
                    for name in (
                        "Content-Type",
                        "Content-Encoding",
                        "X-Panels-File-Sha256",
                        "Accept-Ranges",
                    )
                    if name in response.headers
                }
                if response.headers.get("content-type") != "application/json":
                    selected["Content-Length"] = response.headers.get("Content-Length")
                result.append([method, path, response.status, content, selected])

        lease = json.dumps(spec["lease"]).encode()
        for actor in (
            {},
            {"X-Plan-Actor": "owner"},
            {"X-Plan-Actor": "chief"},
            {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": spec["manager"]},
            {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": spec["ticket"]},
        ):
            await call("POST", "/api/executor/lease", lease, actor)
        for refused in ({**only_id, "Origin": proxy.origin}, [*only_id.items(), *only_id.items()]):
            await call("POST", "/api/executor/lease", lease, refused)
        await call("POST", "/api/executor/lease", b"{}", only_id)
        try:
            async with client.ws_connect(
                owned_url(proxy.origin, "/api/executor/connect").replace("https:", "wss:"),
                headers=only_id,
            ):
                raise AssertionError("old WebSocket was accepted")
        except Exception as error:
            if getattr(error, "status", None) != 403:
                raise
            result.append(["WS", "/api/executor/connect", 403])
        await call("GET", "/api/executor/update", headers={**only_id, "Accept-Encoding": "gzip"})
        await call("POST", "/api/executor/lease", lease, only_id)
        for headers in (
            only_id,
            {**work, "X-Panels-Executor-Incarnation": "22222222-2222-4222-8222-222222222222"},
            {**work, "X-Panels-Executor-Protocol": "21"},
        ):
            await call("GET", "/api/executor/commands?after=0&wait=0", headers=headers)
        await call("GET", "/api/executor/commands?after=0&wait=0")
        await call("GET", "/api/executor/commands?after=9007199254740993&wait=0")
        await call("GET", "/api/executor/commands?after=bad&wait=0")
        await call("GET", "/api/executor/commands?after=0&wait=nan")
        file_path = f"/api/executor/commands/prompt/files/{spec['file']}"
        await call("GET", file_path, headers={**work, "Accept-Encoding": "gzip"})
        async with client.get(owned_url(proxy.origin, file_path), headers=work) as response:
            assert response.status == 200
            await response.content.readexactly(64 * 1024)
        await call("GET", "/api/executor/commands/prompt/files/missing")
        report = {
            "outcomes": [
                {
                    "command_id": "prompt",
                    "child_outcome": {
                        "command_id": "prompt",
                        "child": spec["child"],
                        "disposition": "succeeded",
                        "result": {"kind": "prompt"},
                    },
                },
                {"command_id": "never-put", "child_outcome": None},
            ]
        }
        await call("POST", "/api/executor/commands/outcomes", json.dumps(report).encode())
        item = {
            "sender_id": "mac-sender",
            "conversation_id": "c",
            "sequence": 1,
            "submission_id": "submission",
            "kind": "backend_event",
            "payload": {"child": spec["child"]},
            "file_dependencies": [],
        }
        batch = {
            "sender_id": "mac-sender",
            "items": [{"envelope": item, "sender_refusal": "capture refused"}],
        }
        await call("POST", "/api/executor/conversations/c/items", json.dumps(batch).encode())
        await call("POST", "/api/executor/conversations/c/items", b'{"sender_id":')
        await call("POST", "/api/executor/conversations/c/items", b" " * (4 * 1024 * 1024 + 1))
        manifest = {
            "scope": "ticket",
            "owner_id": spec["ticket"],
            "relative_path": "out/a.bin",
            "sha256": hashlib.sha256(b"uploaded bytes").hexdigest(),
            "byte_count": len(b"uploaded bytes"),
        }
        description = (
            base64.urlsafe_b64encode(
                json.dumps({"manifest": manifest, "child": spec["child"]}).encode()
            )
            .rstrip(b"=")
            .decode()
        )
        await call(
            "PUT",
            "/api/executor/uploads/" + manifest["sha256"],
            b"uploaded bytes",
            {**work, "X-Panels-File-Upload": description},
        )
        await call(
            "POST",
            "/api/executor/reads",
            json.dumps(
                {"child": spec["child"], "path": "/api/tickets", "params": {"status": "open"}}
            ).encode(),
        )
        await call("POST", "/api/executor/conversations/c/live", b'{"events":[]}')
        await call(
            "GET",
            "/api/executor/update/fixture/archive",
            headers={**only_id, "Accept-Encoding": "gzip"},
        )
        await call("GET", "/api/executor/update/prefetch/" + spec["archive_sha"], headers=only_id)
        await call(
            "POST",
            "/api/executor/update",
            b'{"request_id":"fixture","application_revision":"a","phase":"invalid"}',
            only_id,
        )
        await call(
            "POST",
            "/api/executor/update",
            json.dumps(
                {"request_id": "fixture", "application_revision": "a" * 40, "phase": "current"}
            ).encode(),
            only_id,
        )
    return result


def snapshot(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as conn:
        return {
            table: [list(row) for row in conn.execute(query)]
            for table, query in {
                "presence": "SELECT * FROM mac_executor_presence ORDER BY singleton",
                "positions": (
                    "SELECT sender_id,conversation_id,sequence,submission_id,disposition,"
                    "result_json FROM mac_inbox_positions ORDER BY sender_id,conversation_id"
                ),
                "commands": (
                    "SELECT command_id,state,delivered_at_ms,finished_at_ms,outcome_json "
                    "FROM executor_commands ORDER BY command_sequence"
                ),
            }.items()
        }


async def continue_core(endpoint: VpsExecutor, path: Path, proxy: HttpsProxy) -> dict[str, Any]:
    """Exercise the actual core/run consumers after the shared wire transcript."""
    tail = ConversationLiveTail()
    core = SqliteProcessConversationSystem(
        store=ConversationStore(str(path), integer_now=lambda: NOW // 1000),
        backend_child_factories={key: endpoint.runs for key in ConversationBackendKey},
        message_files=endpoint.message_files,
        unix_time_now=lambda: NOW / 1000,
        live_tail=tail,
    )
    endpoint.bind_conversations(core)

    async def peer(method: str, route: str, identity: Any, value: Any = None) -> bytes:
        target = owned_url(proxy.origin, route)
        async with proxy.session_factory() as client:
            async with client.request(
                method,
                target,
                data=None if value is None else json.dumps(wire.encode_value(value)),
                headers={
                    "Accept-Encoding": "identity",
                    "X-Panels-Executor-ID": "test-executor",
                    "X-Panels-Executor-Incarnation": identity.executor_incarnation,
                    "X-Panels-Executor-Protocol": "22",
                },
                allow_redirects=False,
            ) as response:
                raw = await response.read()
                assert response.status == (204 if route.endswith("/live") else 200), raw
                return raw

    async def poll(identity: Any) -> Any:
        return wire.decode_bounded(
            await peer("GET", "/api/executor/commands?after=0&wait=0", identity),
            wire.MailboxPollReply,
        )

    async def until(predicate: Any) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.001)

    def rows(query: str) -> list[Any]:
        with sqlite3.connect(path) as conn:
            return [list(row) for row in conn.execute(query)]

    async def finish(commands: Any) -> None:
        await peer(
            "POST",
            "/api/executor/commands/outcomes",
            IDENTITY,
            wire.CommandOutcomeReport(
                tuple(
                    wire.CommandResult(
                        row.command_id,
                        wire.CommandOutcome(
                            row.command_id,
                            row.child_command.child,
                            wire.CommandDisposition.succeeded,
                            {},
                        ),
                    )
                    for row in commands
                    if row.child_command is not None
                )
            ),
        )
        for row in commands:
            command = row.child_command
            if command is None or command.operation is not wire.ChildOperation.stop:
                continue
            outcome = wire.CommandOutcome(
                row.command_id, command.child, wire.CommandDisposition.succeeded, {}
            )
            receipt = wire.decode_bounded(
                await peer(
                    "POST",
                    f"/api/executor/conversations/{command.child.conversation_id}/items",
                    IDENTITY,
                    wire.ItemBatch(
                        "fixture-stops",
                        (
                            wire.ItemSubmission(
                                wire.QueuedEnvelope(
                                    "fixture-stops",
                                    command.child.conversation_id,
                                    1,
                                    "stopped",
                                    wire.EnvelopeKind.child_stopped,
                                    wire.as_object(outcome),
                                )
                            ),
                        ),
                    ),
                ),
                wire.ItemBatchReply,
            )
            assert receipt.receipts[0].disposition is wire.ReceiptDisposition.accepted

    sequences: dict[str, int] = {}

    def envelope(conversation: str, kind: str, payload: Any) -> wire.QueuedEnvelope:
        sequence = sequences.get(conversation, 0) + 1
        sequences[conversation] = sequence
        return wire.QueuedEnvelope(
            "fixture-peer",
            conversation,
            sequence,
            f"peer-{sequence}",
            wire.EnvelopeKind(kind),
            payload,
        )

    async def submit(items: Any, identity: Any = IDENTITY) -> Any:
        return wire.decode_bounded(
            await peer(
                "POST",
                f"/api/executor/conversations/{items[0].conversation_id}/items",
                identity,
                wire.ItemBatch("fixture-peer", tuple(wire.ItemSubmission(item) for item in items)),
            ),
            wire.ItemBatchReply,
        )

    def event(command: Any, method: str, arguments: Any, turn: Any = "current") -> Any:
        return wire.as_object(
            wire.BackendEvent(
                command.child,
                wire.BackendEventMethod(method),
                command.turn if turn == "current" else turn,
                arguments,
            )
        )

    async def report(commands: Any) -> None:
        await peer(
            "POST",
            "/api/executor/commands/outcomes",
            IDENTITY,
            wire.CommandOutcomeReport(
                tuple(
                    wire.CommandResult(
                        row.command_id,
                        wire.CommandOutcome(
                            row.command_id,
                            row.child_command.child,
                            wire.CommandDisposition.succeeded,
                            {},
                        ),
                    )
                    for row in commands
                    if row.child_command is not None
                )
            ),
        )

    async def launched(conversation: str) -> Any:
        await core.send(
            conversation, text_message_content(f"work {conversation}"), sender_label="Owner"
        )
        commands = (await poll(IDENTITY)).commands
        command = next(
            row.child_command
            for row in commands
            if row.child_command is not None
            and row.child_command.child.conversation_id == conversation
            and row.child_command.operation is wire.ChildOperation.write_prompt
        )
        await report(commands)
        return command

    async def stop_proof(command: Any) -> Any:
        return await submit(
            [
                envelope(
                    command.child.conversation_id,
                    "child_stopped",
                    wire.as_object(
                        wire.CommandOutcome(
                            command.command_id, command.child, wire.CommandDisposition.succeeded, {}
                        )
                    ),
                )
            ]
        )

    async def join_boundaries() -> Any:
        live = await launched("live")
        watcher = tail.subscribe("live")
        before = rows("SELECT count(*) FROM conversation_events")[0][0]
        positions = rows("SELECT count(*) FROM mac_inbox_positions")[0][0]
        await peer(
            "POST",
            "/api/executor/conversations/live/live",
            IDENTITY,
            {
                "events": [
                    event(
                        live,
                        "agent_message_delta",
                        {"text_delta": "Wrong turn"},
                        TurnToken("live", 99),
                    ),
                    event(live, "agent_message_delta", {"text_delta": "Live text"}),
                    event(live, "model_thinking_happened", {}),
                    event(
                        live, "tool_call_progress", {"tool_call_id": "tool", "detail": "Working"}
                    ),
                ]
            },
        )
        frames = [type(await watcher.next_item()).__name__ for _ in range(3)]
        watcher.close()
        assert before == rows("SELECT count(*) FROM conversation_events")[0][0]
        assert positions == rows("SELECT count(*) FROM mac_inbox_positions")[0][0]
        completed = envelope(
            "live",
            "backend_event",
            event(
                live,
                "agent_message_completed",
                {"content": wire.encode_value(text_message_content("Full reply"))},
            ),
        )
        accepted = await submit([completed])
        assert accepted == await submit([completed])
        assert accepted.receipts[0].result == {"applied": True}
        # Backend prose is runtime output. Its durable receipt adds no notebook row.
        assert (
            rows(
                "SELECT count(*) FROM conversation_events "
                "WHERE conversation_id='live' AND kind='agent_message'"
            )[0][0]
            == 0
        )
        killing_live = asyncio.create_task(core.kill("live"))
        await until(
            lambda: bool(
                rows(
                    "SELECT 1 FROM executor_commands "
                    "WHERE conversation_id='live' AND operation='stop'"
                )
            )
        )
        commands = (await poll(IDENTITY)).commands
        stop = next(
            row.child_command
            for row in commands
            if row.child_command is not None
            and row.child_command.child.conversation_id == "live"
            and row.child_command.operation is wire.ChildOperation.stop
        )
        await report(commands)
        assert not killing_live.done() and core._conversations["live"].child is not None
        await stop_proof(stop)
        await killing_live
        current = await launched("live")
        current_watcher = tail.subscribe("live")
        await peer(
            "POST",
            "/api/executor/conversations/live/live",
            IDENTITY,
            {
                "events": [
                    event(
                        live, "agent_message_delta", {"text_delta": "Retired child"}, current.turn
                    ),
                    event(current, "agent_message_delta", {"text_delta": "New child"}),
                ]
            },
        )
        current_frame = await current_watcher.next_item()
        current_watcher.close()
        assert getattr(current_frame, "text_delta", None) == "New child"

        ordinary = await launched("ordinary")
        await submit(
            [
                envelope(
                    "ordinary",
                    "backend_event",
                    event(
                        ordinary,
                        "permission_ask_raised",
                        {
                            "ask": {
                                "ask_id": "ask",
                                "title": "Fixture",
                                "detail": None,
                                "options": [
                                    {
                                        "option_id": "allow",
                                        "label": "Allow",
                                        "option_kind": "allow_once",
                                    }
                                ],
                            }
                        },
                    ),
                )
            ]
        )
        ask_id = next(iter(await core.pending_permission_ask_ids("ordinary")))
        assert (await core.answer_permission_ask("ordinary", ask_id, "allow")).value == "saved"
        answer = next(
            row.child_command
            for row in (await poll(IDENTITY)).commands
            if row.child_command is not None
            and row.child_command.child.conversation_id == "ordinary"
            and row.child_command.operation is wire.ChildOperation.answer_permission_ask
        )
        application = cast(wire.AnswerPermission, answer.arguments).answer
        assert application is not None
        entered_application, release_application = asyncio.Event(), asyncio.Event()
        take_application = core.take_answer_application

        async def held_application(*args: Any, **kwargs: Any) -> Any:
            entered_application.set()
            await release_application.wait()
            return await take_application(*args, **kwargs)

        core.take_answer_application = held_application  # type: ignore[method-assign]
        killing_ordinary = asyncio.create_task(core.kill("ordinary"))
        await until(
            lambda: bool(
                rows(
                    "SELECT 1 FROM executor_commands "
                    "WHERE conversation_id='ordinary' AND operation='stop'"
                )
            )
        )
        pending = (await poll(IDENTITY)).commands
        ordinary_stop = next(
            row.child_command
            for row in pending
            if row.child_command is not None
            and row.child_command.child.conversation_id == "ordinary"
            and row.child_command.operation is wire.ChildOperation.stop
        )
        await report(pending)
        batch = [
            envelope(
                "ordinary",
                "answer_application",
                {
                    "answer_id": application.answer_id,
                    "ask": wire.as_object(application.ask),
                    "applied_at_mac_ms": NOW,
                },
            ),
            envelope(
                "ordinary",
                "backend_event",
                event(
                    ordinary,
                    "vendor_session_cursor_rebound",
                    {
                        "vendor_session_cursor": "cleanup-cursor",
                    },
                    None,
                ),
            ),
            envelope(
                "ordinary",
                "child_stopped",
                wire.as_object(
                    wire.CommandOutcome(
                        ordinary_stop.command_id,
                        ordinary_stop.child,
                        wire.CommandDisposition.succeeded,
                        {},
                    )
                ),
            ),
        ]
        saving = asyncio.create_task(submit(batch[:1]))
        final_saving = None
        try:
            entered_task = asyncio.create_task(entered_application.wait())
            try:
                done, _ = await asyncio.wait(
                    (entered_task, saving), timeout=5, return_when=asyncio.FIRST_COMPLETED
                )
                if saving in done:
                    raise AssertionError(f"application did not enter its core: {await saving}")
                assert entered_task in done, (
                    "application did not reach its core within five seconds"
                )
            finally:
                entered_task.cancel()
                await asyncio.gather(entered_task, return_exceptions=True)
            final_saving = asyncio.create_task(submit(batch[1:]))
            assert not killing_ordinary.done() and core._conversations["ordinary"].child is not None
            assert not rows(
                "SELECT 1 FROM mac_inbox_positions WHERE conversation_id='ordinary' AND sequence>1"
            )
        finally:
            release_application.set()
        application_reply = await saving
        assert final_saving is not None
        final_reply = await final_saving
        assert len(application_reply.receipts) == 1 and application_reply.stop is None
        assert len(final_reply.receipts) == 2 and final_reply.stop is None
        ordered = wire.ItemBatchReply((*application_reply.receipts, *final_reply.receipts))
        core.take_answer_application = take_application  # type: ignore[method-assign]
        await killing_ordinary
        assert len(ordered.receipts) == 3
        assert all(row.disposition is wire.ReceiptDisposition.accepted for row in ordered.receipts)
        assert ordered.receipts[2].result["stop_command_id"] == ordinary_stop.command_id
        assert ordered.receipts[2].result["stopped_child"] == wire.as_object(ordinary_stop.child)

        rebind = await launched("rebind")
        await submit(
            [
                envelope(
                    "rebind", "backend_event", event(rebind, "turn_ended", {"ending": "completed"})
                )
            ]
        )
        changing = asyncio.create_task(
            core.send(
                "rebind",
                text_message_content("changed model"),
                sender_label="Owner",
                sender_message_id="same-reservation",
                model_change="fixture-new",
            )
        )
        await until(
            lambda: bool(
                rows(
                    "SELECT 1 FROM executor_commands "
                    "WHERE conversation_id='rebind' AND operation='stop'"
                )
            )
        )
        changing_commands = (await poll(IDENTITY)).commands
        rebind_stop = next(
            row.child_command
            for row in changing_commands
            if row.child_command is not None
            and row.child_command.child.conversation_id == "rebind"
            and row.child_command.operation is wire.ChildOperation.stop
        )
        await report(changing_commands)
        assert not changing.done()
        assert (
            len(
                rows(
                    "SELECT 1 FROM executor_commands "
                    "WHERE conversation_id='rebind' AND operation='write_prompt'"
                )
            )
            == 1
        )
        await stop_proof(rebind_stop)
        await changing
        await until(
            lambda: (
                len(
                    rows(
                        "SELECT 1 FROM executor_commands "
                        "WHERE conversation_id='rebind' AND operation='write_prompt'"
                    )
                )
                == 2
            )
        )
        joined = []
        for operation, raw in rows(
            "SELECT operation,command_json FROM executor_commands "
            "WHERE conversation_id='rebind' ORDER BY command_sequence"
        ):
            if operation not in ("write_prompt", "start"):
                continue
            command = json.loads(raw)["child_command"]
            joined.append(
                [
                    operation,
                    command["turn"],
                    command["arguments"].get("resolved_start", {}).get("model"),
                    command["arguments"].get("sender_content"),
                ]
            )
        return {
            "frames": frames,
            "completed_receipt": accepted.receipts[0].result,
            "completed_count": rows(
                "SELECT count(*) FROM conversation_events "
                "WHERE conversation_id='live' AND kind='agent_message'"
            )[0][0],
            "stop_waited_for_proof": True,
            "ordered": [
                row.result.get("applied", row.result.get("recorded")) for row in ordered.receipts
            ],
            "rebind": joined,
            "reservation": rows(
                "SELECT count(*) FROM conversation_events "
                "WHERE conversation_id='rebind' AND payload LIKE '%same-reservation%'"
            )[0][0],
        }

    async def isolation_alarm() -> Any:
        a, b = await launched("isolate-a"), await launched("isolate-b")
        at = NOW
        original_clock = endpoint.presence._clock
        endpoint.presence._clock = lambda: at
        chief, pushes = FakeChief(lambda text: True), FakeWebPush()
        with closing(connect(str(path))) as conn:
            notifications_data.register_subscription(
                conn, endpoint="https://push.example/1", p256dh="key", auth="auth", now=1
            )
        checker = StallChecker(str(path), WAITS, endpoint.stall_sources, lambda: at)

        def watch() -> Any:
            return run_watchdog(
                str(path),
                Path(str(path) + ".watchdog"),
                now_ms=at,
                web_push=pushes,
                apns=None,
                push_origin=None,
                message_chief=chief,
            )

        try:
            with (
                patch.object(WAITS, "_clock", lambda: NOW),
                held_mac_writes(
                    cast(ExecutorHarness, SimpleNamespace(system=core)), "isolate-a"
                ) as (
                    entered_save,
                    release_save,
                ),
            ):
                stuck = envelope(
                    "isolate-a",
                    "backend_event",
                    event(
                        a,
                        "tool_call_started",
                        {
                            "tool_call_id": "stuck",
                            "title": "Stuck",
                            "tool_kind": "other",
                            "detail": None,
                        },
                    ),
                )
                saving = asyncio.create_task(submit([stuck]))
                try:
                    await entered_save.wait()
                    assert writer_lock_is_free(str(path))

                    async def lease() -> None:
                        await peer(
                            "POST",
                            "/api/executor/lease",
                            IDENTITY,
                            lease_request(
                                IDENTITY,
                                waiting=(wire.WaitingConversation("isolate-a", 1),),
                                queue_depth=1,
                            ),
                        )

                    await submit(
                        [
                            envelope(
                                "isolate-b",
                                "backend_event",
                                event(
                                    b,
                                    "agent_message_completed",
                                    {
                                        "content": wire.encode_value(
                                            text_message_content("Other reply")
                                        ),
                                    },
                                ),
                            )
                        ]
                    )
                    await lease()
                    await core.send(
                        "isolate-b", text_message_content("other next"), sender_label="Owner"
                    )
                    await submit(
                        [
                            envelope(
                                "isolate-b",
                                "backend_event",
                                event(b, "turn_ended", {"ending": "completed"}),
                            )
                        ]
                    )
                    await until(
                        lambda: (
                            len(
                                rows(
                                    "SELECT 1 FROM executor_commands "
                                    "WHERE conversation_id='isolate-b' AND operation='write_prompt'"
                                )
                            )
                            == 2
                        )
                    )
                    await report((await poll(IDENTITY)).commands)
                    assert not rows(
                        "SELECT 1 FROM conversation_events "
                        "WHERE conversation_id='isolate-a' AND kind='tool_call_started'"
                    )
                    await checker.check_once()
                    for _ in range(19):
                        at += 10000
                        await lease()
                        await checker.check_once()
                    findings = tuple(
                        finding
                        for finding in await checker.check_once()
                        if finding.conversation_id == "isolate-a"
                    )
                    assert sorted(finding.kind.value for finding in findings) == [
                        "mac_item_still_saving",
                        "mac_items_not_moving",
                    ]
                    assert all(
                        finding.ticket_title == "isolate-a" and finding.ticket_id
                        for finding in findings
                    )
                    first, second = watch(), watch()
                    assert len(first.raised) == 2 and first.pushes_delivered == 1
                    assert not second.raised and len(pushes.sent) == len(chief.messages) == 1
                    backlog = next(
                        finding
                        for finding in findings
                        if finding.kind.value == "mac_items_not_moving"
                    )
                    release_save.set()
                    saved = await saving
                    assert saved.receipts[0].result["applied"] is True
                    await submit([stuck])
                    assert (
                        rows(
                            "SELECT count(*) FROM conversation_events "
                            "WHERE conversation_id='isolate-a' AND kind='tool_call_started'"
                        )[0][0]
                        == 1
                    )
                    at += 61000
                    away = tuple(
                        finding
                        for finding in await checker.check_once()
                        if finding.conversation_id == "isolate-a"
                    )
                    final = watch()
                    assert len(away) == 1 and away[0].finding_key == backlog.finding_key
                    assert away[0].stalled_since_ms == backlog.stalled_since_ms
                    assert not final.raised and len(pushes.sent) == len(chief.messages) == 1
                    return {
                        "independent_prompts": 2,
                        "a_events": rows(
                            "SELECT count(*) FROM conversation_events "
                            "WHERE conversation_id='isolate-a' AND kind='tool_call_started'"
                        )[0][0],
                        "kinds": sorted(finding.kind.value for finding in findings),
                        "first_alarm": len(first.raised),
                        "pushes": [
                            {"body": intent.body, "route": intent.route} for intent in pushes.sent
                        ],
                        "chief": chief.messages,
                        "away": [
                            [
                                finding.kind.value,
                                finding.stalled_since_ms == backlog.stalled_since_ms,
                            ]
                            for finding in away
                        ],
                        "raised_again": len(final.raised),
                    }
                finally:
                    release_save.set()
                    await asyncio.gather(saving, return_exceptions=True)
        finally:
            endpoint.presence._clock = original_clock
            await checker.close()

    entered, release = asyncio.Event(), asyncio.Event()
    original = endpoint.mailbox.put_child_command

    async def held(command: wire.ChildCommand) -> Any:
        if command.operation is wire.ChildOperation.write_prompt:
            entered.set()
            await release.wait()
        return await original(command)

    sending = killing = None
    try:
        endpoint.mailbox.put_child_command = held  # type: ignore[method-assign]
        sending = asyncio.create_task(
            core.send("withdraw", text_message_content("withdraw me"), sender_label="Owner")
        )
        await entered.wait()
        killing = asyncio.create_task(core.kill("withdraw"))
        await until(
            lambda: (
                cast(
                    MacRemoteChild, core._conversations["withdraw"].child
                ).initial_delivery_cancelled
            )
        )
        release.set()
        fate = await sending
        await killing
        assert type(fate).__name__ == "PromptDeliverySuppressed"
        endpoint.mailbox.put_child_command = original  # type: ignore[method-assign]
        await until(
            lambda: bool(
                rows(
                    "SELECT 1 FROM executor_commands WHERE conversation_id='withdraw' "
                    "AND operation='stop'"
                )
            )
        )
        await finish((await poll(IDENTITY)).commands)
        await core.wait_until_quiescent()
        joins = await join_boundaries()
        isolation = await isolation_alarm()
        await core.send("recover", text_message_content("already ran"), sender_label="Owner")
        await finish((await poll(IDENTITY)).commands)
        fenced = await launched("fenced")
        await core.send(
            "fenced",
            text_message_content("waited"),
            sender_label="Owner",
            sender_message_id="fenced-reservation",
        )
        await core.send("rewrite", text_message_content("never ran"), sender_label="Owner")
        fresh = mac_identity("22222222-2222-4222-8222-222222222222")
        await peer(
            "POST",
            "/api/executor/lease",
            fresh,
            lease_request(fresh, waiting=(wire.WaitingConversation("fenced", 1),)),
        )
        await poll(fresh)
        ended = await submit(
            [
                envelope(
                    "fenced", "backend_event", event(fenced, "turn_ended", {"ending": "completed"})
                )
            ],
            fresh,
        )
        assert ended.receipts[0].result["applied"] is True
        assert (
            len(
                rows(
                    "SELECT 1 FROM executor_commands "
                    "WHERE conversation_id='fenced' AND operation='write_prompt'"
                )
            )
            == 1
        )
        await peer("POST", "/api/executor/lease", fresh, lease_request(fresh))
        await until(
            lambda: (
                len(
                    rows(
                        "SELECT 1 FROM executor_commands WHERE executor_incarnation='"
                        + fresh.executor_incarnation
                        + "' AND operation='write_prompt' "
                        "AND conversation_id IN ('recover','rewrite','fenced')"
                    )
                )
                == 3
            )
        )
        await core.wait_until_quiescent()
        await poll(fresh)
        commands = rows(
            "SELECT conversation_id,operation,state,command_json FROM executor_commands "
            "WHERE conversation_id IN ('withdraw','recover','rewrite') "
            "ORDER BY conversation_id,command_sequence"
        )
        return {
            "kill": type(fate).__name__,
            "joins": joins,
            "isolation": isolation,
            "fenced": {
                "prompts": [
                    [
                        "fresh" if incarnation == fresh.executor_incarnation else "old",
                        json.loads(raw)["child_command"]["arguments"].get("sender_content"),
                    ]
                    for incarnation, raw in rows(
                        "SELECT executor_incarnation,command_json FROM executor_commands "
                        "WHERE conversation_id='fenced' AND operation='write_prompt' "
                        "ORDER BY command_sequence"
                    )
                ],
                "endings": rows(
                    "SELECT json_extract(payload,'$.ending') FROM conversation_events "
                    "WHERE conversation_id='fenced' AND kind='turn_ended' ORDER BY sequence"
                ),
                "refused": rows(
                    "SELECT count(*) FROM conversation_events "
                    "WHERE conversation_id='fenced' AND kind='prompt_delivery_refused'"
                )[0][0],
            },
            "commands": [
                [
                    conversation,
                    operation,
                    state,
                    json.loads(command)["child_command"]["turn"],
                    json.loads(command)["child_command"]["arguments"].get("content"),
                    json.loads(command)["child_command"]["arguments"].get("mode"),
                ]
                for conversation, operation, state, command in commands
            ],
            "interruptions": rows(
                "SELECT source_conversation_id,turn_number,recovery_state,detail "
                "FROM deploy_interruptions ORDER BY source_conversation_id"
            ),
            "claims": rows(
                "SELECT conversation_id,worker_step_claim FROM tickets "
                "WHERE conversation_id IN ('withdraw','recover','rewrite') "
                "ORDER BY conversation_id"
            ),
            "turns": rows(
                "SELECT conversation_id,turn_number,status FROM conversation_lifecycle_facts "
                "WHERE conversation_id IN ('withdraw','recover','rewrite') AND kind='turn' "
                "ORDER BY conversation_id,turn_number"
            ),
        }
    finally:
        release.set()
        endpoint.mailbox.put_child_command = original  # type: ignore[method-assign]
        for task in (sending, killing):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await core.shutdown()


def test_unit22_executor_real_wire(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)

    async def run() -> dict[str, Any]:
        source = tmp_path / "source"
        source.mkdir()
        path = source / "panels.sqlite"
        clock = FixedClock(datetime.fromtimestamp(NOW / 1000, UTC))
        core_tickets = {}
        with closing(connect(str(path))) as conn:
            create_schema(conn)
            notifications_data.get_or_create_web_push_identity(conn, NOW // 1000)
            ticket = write_create_ticket(
                {"worker_type": "coding", "title": "Transport fixture", "brief": "Synthetic only"},
                conn,
                RequestContext(Principal(PrincipalKind.owner, "owner")),
                clock,
            )["id"]
            manager = create_manager(
                conn, title="Transport manager", project_id="project_other", clock=clock
            ).id
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,created_at) "
                "VALUES('c','codex','fixture','/tmp','full',1760000000)"
            )
            conn.commit()
            for conversation in (
                "withdraw",
                "recover",
                "rewrite",
                "live",
                "ordinary",
                "rebind",
                "fenced",
                "isolate-a",
                "isolate-b",
            ):
                core_tickets[conversation] = write_create_ticket(
                    {
                        "worker_type": "coding",
                        "title": conversation,
                        "brief": "Synthetic only",
                        "locale": "macos",
                    },
                    conn,
                    RequestContext(Principal(PrincipalKind.owner, "owner")),
                    clock,
                )["id"]
                accept_proposal(
                    conn,
                    core_tickets[conversation],
                    field="brief",
                    principal=Principal(PrincipalKind.owner, "owner"),
                    next_holder=Principal(PrincipalKind.owner, "owner"),
                    next_ceiling="none",
                    now=NOW // 1000,
                )
        seed = ConversationStore(str(path), integer_now=lambda: NOW // 1000)
        for conversation, ticket_id in core_tickets.items():
            await seed.create_conversation(
                ResolvedConversationStart(
                    conversation,
                    ConversationBackendKey.claude
                    if conversation == "rebind"
                    else ConversationBackendKey.codex,
                    "fixture",
                    None,
                    ConversationRoleMaterials(
                        "Synthetic Worker",
                        identity_environment_variables=(
                            ("PLAN_ACTOR", "worker"),
                            ("PLAN_TICKET_ID", ticket_id),
                        ),
                    ),
                    Path("/tmp"),
                    ConversationAccess.full,
                )
            )
            with closing(connect(str(path))) as conn:
                write_ticket_conversation_start(
                    conn,
                    ticket_id,
                    conversation_id=conversation,
                    backend="claude" if conversation == "rebind" else "codex",
                    model="fixture",
                    reasoning_effort=None,
                    now=NOW // 1000,
                )
                claim_ticket_for_worker_step(
                    conn,
                    ticket_id,
                    readiness_check=lambda *args, **kwargs: True,
                    now=NOW // 1000,
                )
        make_current(str(path), IDENTITY, NOW)
        endpoint = VpsExecutor(str(path), clock)
        endpoint.presence.returned = None
        endpoint.presence._clock = lambda: NOW
        endpoint.mailbox._clock = lambda: NOW
        endpoint.app = _reads_app()
        child = ChildReference(IDENTITY.executor_incarnation, "child", "c")
        endpoint.runs.store.register_child(child, "ticket", ticket, NOW)
        payload = bytes(range(256)) * (32 * 1024)
        kept = await endpoint.message_files.keep(
            "c", payload, media_type="application/octet-stream"
        )
        content = (
            *text_message_content("input"),
            MessageFile(kept.stored_file_id, kept.media_type, "input.bin", len(payload)),
        )
        command = wire.ChildCommand(
            "prompt",
            child,
            wire.ChildOperation.write_prompt,
            TurnToken("c", 1),
            wire.WritePrompt(
                content=content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
            ),
        )
        await endpoint.mailbox.put_child_command(command)
        archive = source / "archive.gz"
        archive.write_bytes(b"fixture archive")
        os.utime(archive, (NOW / 1000, NOW / 1000))
        with sqlite3.connect(path) as left, sqlite3.connect(tmp_path / "copied.sqlite") as right:
            left.backup(right)
        candidate = tmp_path / "candidate"
        shutil.copytree(source, candidate)
        shutil.copyfile(tmp_path / "copied.sqlite", candidate / "panels.sqlite")
        spec = {
            "ticket": ticket,
            "lease": {**wire.as_object(lease_request(IDENTITY)), "mac_wall_clock_ms": NOW},
            "manager": manager,
            "child": wire.as_object(child),
            "file": kept.stored_file_id,
            "archive_sha": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        updates = RecordingUpdates(archive)
        try:
            async with serve_executor_app(
                _transport_app(endpoint, updates), tmp_path / "source-tls"
            ) as proxy:
                OWNED_ORIGINS.add(proxy.origin)
                try:
                    expected = await transcript(proxy, spec)
                    expected_rows = snapshot(path)
                    expected_core = await continue_core(endpoint, path, proxy)
                finally:
                    OWNED_ORIGINS.remove(proxy.origin)
        finally:
            await endpoint.close()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        upstream = f"http://127.0.0.1:{listener.getsockname()[1]}"
        host_spec = {
            **spec,
            "path": str(candidate / "panels.sqlite"),
            "archive": str(candidate / "archive.gz"),
            "ready": str(tmp_path / "ready.json"),
            "state": str(tmp_path / "state.json"),
            "fd": listener.fileno(),
            "continuation": str(tmp_path / "continuation.json"),
        }
        spec_file = tmp_path / "host.json"
        spec_file.write_text(json.dumps(host_spec))
        process = subprocess.Popen(
            ["node", str(ROOT / "server/executor/testing/app_transport.ts"), str(spec_file)],
            cwd=ROOT,
            env={
                **homes,
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "TMPDIR": str(tmp_path),
                "PLAN_TEST_MODE": "1",
                "PLAN_SERVER_URL": "http://127.0.0.1:0",
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(listener.fileno(),),
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(5):
                while not Path(host_spec["ready"]).exists():
                    if process.poll() is not None:
                        raise AssertionError(process.communicate()[1])
                    await asyncio.sleep(0.01)
            ready = json.loads(Path(host_spec["ready"]).read_text())
            assert (ready["address"], ready["port"]) == listener.getsockname()
            with isolated_https_proxy(tmp_path / "candidate-tls", upstream) as proxy:
                OWNED_ORIGINS.add(proxy.origin)
                try:
                    actual = await transcript(proxy, spec)
                    for escaped in (
                        "https://panels.invalid/api/executor/lease",
                        "//panels.invalid/api/executor/lease",
                    ):
                        with pytest.raises(ValueError):
                            owned_url(proxy.origin, escaped)
                    with pytest.raises(ValueError):
                        owned_url("https://127.0.0.1:1", "/api/executor/lease")
                finally:
                    OWNED_ORIGINS.remove(proxy.origin)
            actual_rows = snapshot(candidate / "panels.sqlite")
            assert process.stdin is not None
            process.stdin.write("continue\n")
            process.stdin.flush()
            async with asyncio.timeout(10):
                while not Path(host_spec["continuation"]).exists():
                    if process.poll() is not None:
                        raise AssertionError(process.communicate()[1])
                    await asyncio.sleep(0.01)
            actual_core = json.loads(Path(host_spec["continuation"]).read_text())
            stdout, stderr = await asyncio.to_thread(process.communicate, "close\n", timeout=10)
            assert process.returncode == 0, stdout + stderr
            final = json.loads(Path(host_spec["state"]).read_text())
            assert final["catalog_state"] == "withdrawn"
            assert final["descriptors"] == []
            assert final["reports"] == updates.reports
            assert actual == expected
            assert actual_rows == expected_rows
            assert actual_core == expected_core
            return {
                "source": expected,
                "candidate": actual,
                "source_rows": expected_rows,
                "candidate_rows": actual_rows,
                "core_source": expected_core,
                "core_candidate": actual_core,
                "guard_refusals": 3,
                "refused_connection_attempts": 0,
                "remaining_input_descriptors": final["descriptors"],
                "catalog_state_after_stop": final["catalog_state"],
            }
        finally:
            listener.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    patches = [
        "planner.executor.vps_presence.now_ms",
        "planner.executor.vps_mailbox.now_ms",
        "planner.executor.vps_inbox.now_ms",
    ]
    with (
        synthetic_home_environment(homes),
        patch(patches[0], return_value=NOW),
        patch(patches[1], return_value=NOW),
        patch(patches[2], return_value=NOW),
    ):
        evidence = asyncio.run(asyncio.wait_for(run(), 45))
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        target = Path(directory) / "unit-22-app-transport.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"elapsed_seconds": time.perf_counter() - started, **evidence}, indent=2)
            + "\n"
        )
