"""One owned wire/core exchange. Existing component proofs cover their permutations."""

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
from uuid import UUID

import pytest
from tests.integration.test_executor_app_transport import _Core, _reads_app, _transport_app
from tests.integration.test_executor_end_to_end import ExecutorHarness, create_mac_ticket
from tests.integration.test_executor_isolation import held_mac_writes, writer_lock_is_free
from tests.support.executor_https import HttpsProxy, isolated_https_proxy, serve_executor_app
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
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import MessageFile, text_message_content
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import TestClock as FixedClock
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import UpdateConflict
from planner.execution_configuration import PROTOCOL_VERSION
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.registry import VpsExecutorRegistry
from planner.executor.update_contracts import (
    MacUpdateCheck,
    MacUpdatePrefetch,
    MacUpdateReport,
    MacUpdateStatus,
)
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_transport import VpsExecutor
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID
from planner.machines.credential_api import router as machine_credential_router
from planner.tickets.data import claim_ticket_for_worker_step, write_ticket_conversation_start

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_760_000_000_000
IDENTITY = mac_identity("11111111-1111-4111-8111-111111111111")
OWNED_ORIGINS: set[str] = set()
ENROLLED_MACHINE = "77777777-7777-4777-8777-777777777777"
ENROLLED_CREDENTIAL = base64.urlsafe_b64encode(b"c" * 32).decode().rstrip("=")


def owned_url(origin: str, path: str) -> str:
    parsed = urlsplit(origin)
    if (
        origin not in OWNED_ORIGINS
        or parsed.scheme not in {"https", "http"}
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.port == 8767
        or not path.startswith("/")
        or path.startswith("//")
    ):
        raise ValueError("request is outside this fixture's exact owned listener")
    return origin + path


class RecordingUpdates(MacUpdateService):
    def __init__(self, archive: Path) -> None:
        self.archive_file = archive
        self.reports: list[Any] = []
        self.machine_id = ORIGINAL_MAC_MACHINE_ID

    def for_machine(self, machine_id: str) -> MacUpdateService:
        assert machine_id == self.machine_id
        return self

    def check(self) -> MacUpdateCheck:
        return MacUpdateCheck(1, None)

    def archive(self, request_id: str) -> Path:
        assert request_id == "fixture"
        return self.archive_file

    def prefetch_archive(self, sha256: str) -> Path:
        assert sha256 == hashlib.sha256(self.archive_file.read_bytes()).hexdigest()
        return self.archive_file

    def report(self, report: MacUpdateReport, health: Any) -> MacUpdateStatus:
        self.reports.append([wire.encode_value(report), wire.encode_value(health)])
        return MacUpdateStatus(report.request_id, report.application_revision, report.phase)


def credential_rows(path: Path) -> list[dict[str, Any]]:
    with closing(connect(str(path))) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM machine_executor_credentials ORDER BY machine_id"
            )
        ]


class CredentialUpdates(RecordingUpdates):
    def __init__(self, archive: Path, path: Path) -> None:
        super().__init__(archive)
        self.path = path
        self.platform = "darwin"
        self.services: dict[str, CredentialUpdates] = {self.machine_id: self}

    def for_machine(self, machine_id: str) -> MacUpdateService:
        platform = next(
            row["platform"] for row in credential_rows(self.path)
            if row["machine_id"] == machine_id
        )
        if machine_id not in self.services or self.services[machine_id].platform != platform:
            service = CredentialUpdates(self.archive_file, self.path)
            service.machine_id, service.reports = machine_id, self.reports
            service.platform = platform
            self.services[machine_id] = service
        return self.services[machine_id]

    def check(self) -> MacUpdateCheck:
        digest = hashlib.sha256(self.archive_file.read_bytes()).hexdigest()
        return MacUpdateCheck(1, None, MacUpdatePrefetch(
            ("b" if self.platform == "linux" else "a") * 40,
            digest, self.archive_file.stat().st_size,
            "/api/executor/update/prefetch/" + digest,
        ))

    def report(self, report: MacUpdateReport, health: Any) -> MacUpdateStatus:
        self.reports.append([
            self.machine_id, self.platform, wire.encode_value(report), wire.encode_value(health),
            credential_rows(self.path),
        ])
        if report.request_id == "refused":
            raise UpdateConflict("fixture report refused")
        return MacUpdateStatus(report.request_id, report.application_revision, report.phase)


async def transcript(proxy: HttpsProxy, spec: dict[str, Any]) -> list[Any]:
    only_id = {"X-Panels-Executor-ID": "test-executor"}
    work = {
        **only_id,
        "X-Panels-Executor-Incarnation": IDENTITY.executor_incarnation,
        "X-Panels-Executor-Protocol": str(PROTOCOL_VERSION),
    }
    result = []
    async with proxy.session_factory() as client:

        async def call(method: str, path: str, body: Any = None, headers: Any = None) -> None:
            chosen = work if headers is None else headers
            pairs = (
                list({"Accept-Encoding": "identity", **chosen}.items())
                if isinstance(chosen, dict)
                else [("Accept-Encoding", "identity"), *chosen]
            )
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            async with client.request(
                method,
                owned_url(proxy.origin, path),
                data=raw if body is not None else None,
                headers=pairs,
                allow_redirects=False,
            ) as response:
                value = await response.read()
                content = (
                    json.loads(value)
                    if response.content_type == "application/json"
                    else hashlib.sha256(value).hexdigest()
                )
                names = ("Content-Type", "Content-Encoding", "X-Panels-File-Sha256")
                result.append(
                    [
                        response.status,
                        content,
                        {key: response.headers[key] for key in names if key in response.headers},
                    ]
                )

        actor = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": spec["ticket"]}
        await call("POST", "/api/executor/lease", spec["lease"], actor)
        for headers in ({**only_id, "Origin": proxy.origin}, [*only_id.items(), *only_id.items()]):
            await call("POST", "/api/executor/lease", spec["lease"], headers)
        websocket = owned_url(proxy.origin, "/api/executor/connect").replace("https:", "wss:")
        with pytest.raises(Exception) as refusal:
            async with client.ws_connect(websocket, headers=only_id):
                raise AssertionError("old WebSocket was accepted")
        assert getattr(refusal.value, "status", None) == 403
        await call("GET", "/api/executor/update", headers=only_id)
        await call("POST", "/api/executor/lease", spec["lease"], only_id)
        foreign = {**work, "X-Panels-Executor-Incarnation": "22222222-2222-4222-8222-222222222222"}
        await call("GET", "/api/executor/commands?after=0&wait=0", headers=foreign)
        await call("GET", "/api/executor/commands?after=0&wait=0")
        path = f"/api/executor/commands/prompt/files/{spec['file']}"
        await call("GET", path, headers={**work, "Accept-Encoding": "gzip"})
        async with client.get(owned_url(proxy.origin, path), headers=work) as response:
            assert response.content_length == 8 * 1024 * 1024
            await response.content.readexactly(64 * 1024)
        child = ChildReference(**spec["child"])
        outcome = wire.CommandOutcome("prompt", child, wire.CommandDisposition.succeeded, {})
        report = wire.CommandOutcomeReport((wire.CommandResult("prompt", outcome),))
        await call("POST", "/api/executor/commands/outcomes", wire.encode_value(report))
        await call("POST", "/api/executor/conversations/c/items", b" " * (4 * 1024 * 1024 + 1))
        manifest = wire.FileManifest(
            wire.FileScope.ticket,
            spec["ticket"],
            "out/a.bin",
            hashlib.sha256(b"upload").hexdigest(),
            6,
        )
        upload = {"manifest": wire.as_object(manifest), "child": spec["child"]}
        header = base64.urlsafe_b64encode(json.dumps(upload).encode()).rstrip(b"=").decode()
        await call(
            "PUT",
            "/api/executor/uploads/" + manifest.sha256,
            b"upload",
            {**work, "X-Panels-File-Upload": header},
        )
        await call(
            "POST",
            "/api/executor/reads",
            {"child": spec["child"], "path": "/api/tickets", "params": {"status": "open"}},
        )
        await call("POST", "/api/executor/conversations/c/live", {"events": []})
        await call("GET", "/api/executor/update/fixture/archive", headers=only_id)
        await call("GET", "/api/executor/update/prefetch/" + spec["archive_sha"], headers=only_id)
        for phase in ("invalid", "current"):
            update = {"request_id": "fixture", "application_revision": "a" * 40, "phase": phase}
            await call("POST", "/api/executor/update", update, only_id)
    return result


async def continue_core(endpoint: VpsExecutor, path: Path, proxy: HttpsProxy) -> dict[str, Any]:
    tail = ConversationLiveTail()
    core = SqliteProcessConversationSystem(
        store=ConversationStore(str(path), integer_now=lambda: NOW // 1000),
        live_tail=tail,
        backend_child_factories={key: endpoint.runs for key in ConversationBackendKey},
        message_files=endpoint.message_files,
        unix_time_now=lambda: NOW / 1000,
    )
    endpoint.bind_conversations(core)
    sequences: dict[str, int] = {}

    def rows(query: str) -> list[Any]:
        with sqlite3.connect(path) as conn:
            return [list(row) for row in conn.execute(query)]

    async def until(predicate: Any) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.001)

    async def post(route: str, value: Any, identity: Any = IDENTITY) -> Any:
        headers = {
            "Accept-Encoding": "identity",
            "X-Panels-Executor-ID": "test-executor",
            "X-Panels-Executor-Incarnation": identity.executor_incarnation,
            "X-Panels-Executor-Protocol": str(PROTOCOL_VERSION),
        }
        async with proxy.session_factory() as client:
            async with client.post(
                owned_url(proxy.origin, route),
                json=wire.encode_value(value),
                headers=headers,
                allow_redirects=False,
            ) as response:
                body = await response.read()
                assert response.status == (204 if route.endswith("/live") else 200), body
                return json.loads(body) if body else None

    def envelope(conversation: str, kind: str, payload: Any) -> Any:
        seq = sequences[conversation] = sequences.get(conversation, 0) + 1
        return wire.QueuedEnvelope(
            "peer", conversation, seq, str(seq), wire.EnvelopeKind(kind), payload
        )

    async def items(values: Any, identity: Any = IDENTITY) -> Any:
        batch = wire.ItemBatch("peer", tuple(wire.ItemSubmission(row) for row in values))
        return await post(
            f"/api/executor/conversations/{values[0].conversation_id}/items", batch, identity
        )

    def event(command: Any, method: str, arguments: Any, turn: Any = "current") -> Any:
        token = command.turn if turn == "current" else turn
        return wire.as_object(
            wire.BackendEvent(command.child, wire.BackendEventMethod(method), token, arguments)
        )

    async def poll(identity: Any = IDENTITY) -> Any:
        return (await endpoint.mailbox.poll(identity, 0, 0)).commands

    def outcome(command: Any) -> Any:
        return wire.CommandOutcome(
            command.command_id, command.child, wire.CommandDisposition.succeeded, {}
        )

    async def report(commands: Any) -> None:
        results = tuple(
            wire.CommandResult(row.command_id, outcome(row.child_command))
            for row in commands
            if row.child_command is not None
        )
        await endpoint.mailbox.record_results(IDENTITY, wire.CommandOutcomeReport(results))

    async def launched(conversation: str) -> Any:
        await core.send(
            conversation, text_message_content("work " + conversation), sender_label="Owner"
        )
        commands = await poll()
        command = next(
            row.child_command
            for row in commands
            if row.child_command
            and row.child_command.child.conversation_id == conversation
            and row.child_command.operation is wire.ChildOperation.write_prompt
        )
        await report(commands)
        return command

    async def stop(identity: str) -> Any:
        query = (
            "SELECT 1 FROM executor_commands "
            f"WHERE conversation_id='{identity}' AND operation='stop'"
        )
        await until(lambda: bool(rows(query)))
        commands = await poll()
        command = next(
            row.child_command
            for row in commands
            if row.child_command
            and row.child_command.child.conversation_id == identity
            and row.child_command.operation is wire.ChildOperation.stop
        )
        await report(commands)
        return command

    async def proof(command: Any) -> Any:
        return await items(
            [
                envelope(
                    command.child.conversation_id, "child_stopped", wire.as_object(outcome(command))
                )
            ]
        )

    original_put = endpoint.mailbox.put_child_command
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(command: Any) -> Any:
        if command.operation is wire.ChildOperation.write_prompt:
            entered.set()
            await release.wait()
        return await original_put(command)

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
        assert type(await sending).__name__ == "PromptDeliverySuppressed"
        with pytest.raises(PlannerError) as refused:
            await killing
        assert refused.value.code.value == "retry_later"
        endpoint.mailbox.put_child_command = original_put  # type: ignore[method-assign]
        await proof(await stop("withdraw"))
        assert rows("SELECT stop_state FROM mac_runs WHERE conversation_id='withdraw'") == [
            ["closed"]
        ]
        assert rows(
            "SELECT 1 FROM executor_child_assignments "
            "WHERE conversation_id='withdraw' AND closed_at_ms IS NOT NULL"
        ) == [[1]]
        state = core._conversations["withdraw"]
        await until(
            lambda: state.retained_child_reference is None and not state.child_is_quarantined
        )
        if state.retirement_task is not None:
            await state.retirement_task
        await core.kill("withdraw")
        ordinary = await launched("ordinary")
        watcher = tail.subscribe("ordinary")
        before = (
            rows("SELECT count(*) FROM conversation_events"),
            rows("SELECT count(*) FROM mac_inbox_positions"),
        )
        live = [
            event(ordinary, "agent_message_delta", {"text_delta": "Live text"}),
            event(ordinary, "model_thinking_happened", {}),
            event(ordinary, "tool_call_progress", {"tool_call_id": "tool", "detail": "Working"}),
        ]
        wrong = event(
            ordinary, "agent_message_delta", {"text_delta": "Wrong"}, TurnToken("ordinary", 99)
        )
        await post("/api/executor/conversations/ordinary/live", {"events": [wrong, *live]})
        frames = [type(await watcher.next_item()).__name__ for _ in range(3)]
        watcher.close()
        assert before == (
            rows("SELECT count(*) FROM conversation_events"),
            rows("SELECT count(*) FROM mac_inbox_positions"),
        )
        completed = envelope(
            "ordinary",
            "backend_event",
            event(ordinary, "agent_message_completed", {"content": [{"text": "Full reply"}]}),
        )
        assert await items([completed]) == await items([completed])
        assert not rows("SELECT 1 FROM conversation_events WHERE kind='agent_message'")
        ask = {
            "ask_id": "ask",
            "title": "Fixture",
            "detail": None,
            "options": [{"option_id": "allow", "label": "Allow", "option_kind": "allow_once"}],
        }
        await items(
            [
                envelope(
                    "ordinary",
                    "backend_event",
                    event(ordinary, "permission_ask_raised", {"ask": ask}),
                )
            ]
        )
        ask_id = next(iter(await core.pending_permission_ask_ids("ordinary")))
        assert (await core.answer_permission_ask("ordinary", ask_id, "allow")).value == "saved"
        answer_command = next(
            row.child_command
            for row in await poll()
            if row.child_command
            and row.child_command.operation is wire.ChildOperation.answer_permission_ask
        )
        answer = cast(wire.AnswerPermission, answer_command.arguments).answer
        assert answer is not None
        killing = asyncio.create_task(core.kill("ordinary"))
        stopped = await stop("ordinary")
        assert not killing.done()
        application = envelope(
            "ordinary",
            "answer_application",
            wire.as_object(wire.AnswerApplication(answer.answer_id, answer.ask, NOW)),
        )
        last_event = envelope(
            "ordinary",
            "backend_event",
            event(
                ordinary,
                "vendor_session_cursor_rebound",
                {"vendor_session_cursor": "cleanup-cursor"},
                None,
            ),
        )
        stopped_item = envelope("ordinary", "child_stopped", wire.as_object(outcome(stopped)))
        other = await launched("other")
        with held_mac_writes(cast(ExecutorHarness, SimpleNamespace(system=core)), "ordinary") as (
            entered,
            release,
        ):
            saving = asyncio.create_task(items([application]))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                final = asyncio.create_task(items([last_event, stopped_item]))
                assert writer_lock_is_free(str(path)) and not killing.done()
                completed_other = event(
                    other, "agent_message_completed", {"content": [{"text": "Other reply"}]}
                )
                await items([envelope("other", "backend_event", completed_other)])
                waiting = (wire.WaitingConversation("ordinary", 1),)
                await post("/api/executor/lease", lease_request(IDENTITY, waiting=waiting))
                await core.send("other", text_message_content("other next"), sender_label="Owner")
                await items(
                    [
                        envelope(
                            "other",
                            "backend_event",
                            event(other, "turn_ended", {"ending": "completed"}),
                        )
                    ]
                )
                query = (
                    "SELECT 1 FROM executor_commands "
                    "WHERE conversation_id='other' AND operation='write_prompt'"
                )
                await until(lambda: len(rows(query)) == 2)
                assert not rows(
                    "SELECT 1 FROM mac_inbox_positions "
                    "WHERE conversation_id='ordinary' AND sequence>2"
                )
            finally:
                release.set()
            first, last = await saving, await final
        await killing
        assert len(first["receipts"]) == 1 and len(last["receipts"]) == 2
        assert first["stop"] is last["stop"] is None
        receipts = first["receipts"] + last["receipts"]
        assert receipts[0]["result"]["recorded"] and receipts[1]["result"]["applied"]
        assert receipts[-1]["result"]["stop_command_id"] == stopped.command_id
        assert receipts[-1]["result"]["stopped_child"] == wire.as_object(stopped.child)
        current = await launched("ordinary")
        watcher = tail.subscribe("ordinary")
        stale = event(ordinary, "agent_message_delta", {"text_delta": "Retired"}, current.turn)
        fresh_live = event(current, "agent_message_delta", {"text_delta": "New child"})
        await post("/api/executor/conversations/ordinary/live", {"events": [stale, fresh_live]})
        assert getattr(await watcher.next_item(), "text_delta", None) == "New child"
        watcher.close()
        rebind = await launched("rebind")
        await items(
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
        stopped = await stop("rebind")
        query = (
            "SELECT 1 FROM executor_commands "
            "WHERE conversation_id='rebind' AND operation='write_prompt'"
        )
        assert not changing.done() and len(rows(query)) == 1
        await proof(stopped)
        await changing
        assert len(rows(query)) == 2
        rebound = json.loads(
            rows(
                "SELECT command_json FROM executor_commands WHERE conversation_id='rebind' "
                "AND operation='write_prompt' ORDER BY command_sequence"
            )[1][0]
        )["child_command"]
        start = json.loads(
            rows(
                "SELECT command_json FROM executor_commands WHERE conversation_id='rebind' "
                "AND operation='start' ORDER BY command_sequence"
            )[1][0]
        )["child_command"]
        model = start["arguments"]["resolved_start"]["model"]
        text = "".join(piece["text"] for piece in rebound["arguments"]["sender_content"])
        assert (
            model == "fixture-new"
            and text == "changed model"
            and rebound["turn"]["turn_number"] == 2
        )
        reservations = rows(
            "SELECT count(*) FROM conversation_events WHERE payload LIKE '%same-reservation%'"
        )[0][0]
        assert reservations == 1
        fenced = await launched("fenced")
        await core.send("fenced", text_message_content("waited"), sender_label="Owner")
        fresh = mac_identity("22222222-2222-4222-8222-222222222222")
        await post(
            "/api/executor/lease",
            lease_request(fresh, waiting=(wire.WaitingConversation("fenced", 1),)),
            fresh,
        )
        await poll(fresh)
        ended = await items(
            [
                envelope(
                    "fenced", "backend_event", event(fenced, "turn_ended", {"ending": "completed"})
                )
            ],
            fresh,
        )
        assert ended["receipts"][0]["result"]["applied"]
        query = (
            "SELECT command_json FROM executor_commands WHERE conversation_id='fenced' "
            "AND operation='write_prompt' ORDER BY command_sequence"
        )
        assert len(rows(query)) == 1
        await post("/api/executor/lease", lease_request(fresh), fresh)
        await until(lambda: len(rows(query)) == 2)
        command = json.loads(rows(query)[1][0])["child_command"]
        assert command["child"]["executor_incarnation"] == fresh.executor_incarnation
        assert command["arguments"]["sender_content"] == [{"text": "waited"}]
        assert rows(
            "SELECT json_extract(payload,'$.ending') FROM conversation_events "
            "WHERE conversation_id='fenced' AND kind='turn_ended'"
        ) == [["completed"]]
        a_events = rows(
            "SELECT count(*) FROM conversation_events "
            "WHERE conversation_id='ordinary' AND kind='answer_applied'"
        )[0][0]
        assert a_events == 1
        return {
            "kill": "PromptDeliverySuppressed",
            "frames": frames,
            "ordered": [row["disposition"] == "accepted" for row in receipts],
            "rebind": {
                "model": model,
                "text": text,
                "turn": rebound["turn"]["turn_number"],
                "reservations": reservations,
            },
            "isolation": {"independent_prompts": 2, "a_events": 1},
            "replacement": {"fenced_text": "waited", "fenced_ending": "completed"},
        }
    finally:
        release.set()
        endpoint.mailbox.put_child_command = original_put  # type: ignore[method-assign]
        await core.shutdown()


def test_unit22_executor_real_wire(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)

    async def run() -> Any:
        source = tmp_path / "source"
        source.mkdir()
        path = source / "panels.sqlite"
        clock = FixedClock(datetime.fromtimestamp(NOW / 1000, UTC))
        with closing(connect(str(path))) as conn:
            create_schema(conn)
        seed = ConversationStore(str(path), integer_now=lambda: NOW // 1000)
        for name in ("withdraw", "ordinary", "rebind", "other", "fenced"):
            backend = (
                ConversationBackendKey.claude if name == "rebind" else ConversationBackendKey.codex
            )
            ticket = create_mac_ticket(
                str(path), tmp_path, title=name, backend_key=backend, model="fixture"
            )
            materials = ConversationRoleMaterials(
                "Synthetic Worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket))
            )
            start = ResolvedConversationStart(
                name, backend, "fixture", None, materials, Path("/tmp"), ConversationAccess.full
            )
            await seed.create_conversation(start)
            with closing(connect(str(path))) as conn:
                write_ticket_conversation_start(
                    conn,
                    ticket,
                    conversation_id=name,
                    backend=backend.value,
                    model="fixture",
                    reasoning_effort=None,
                    now=NOW // 1000,
                )
                claim_ticket_for_worker_step(
                    conn, ticket, readiness_check=lambda *args, **kwargs: True, now=NOW // 1000
                )
        with closing(connect(str(path))) as conn:
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,created_at) "
                "VALUES('c','codex','fixture','/tmp','full',1760000000)"
            )
            conn.commit()
        make_current(str(path), IDENTITY, NOW)
        endpoint = VpsExecutor(str(path), clock)
        endpoint.presence.returned = None
        endpoint.runs.conversations = _Core()
        endpoint.presence._clock = endpoint.mailbox._clock = lambda: NOW
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
        arguments = wire.WritePrompt(
            content=content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.queue,
        )
        await endpoint.mailbox.put_child_command(
            wire.ChildCommand(
                "prompt", child, wire.ChildOperation.write_prompt, TurnToken("c", 1), arguments
            )
        )
        archive = source / "archive.gz"
        archive.write_bytes(b"fixture archive")
        os.utime(archive, (NOW / 1000, NOW / 1000))
        candidate = tmp_path / "candidate"
        shutil.copytree(source, candidate)
        with sqlite3.connect(path) as left, sqlite3.connect(candidate / "panels.sqlite") as right:
            left.backup(right)
        spec: dict[str, Any] = {
            "ticket": ticket,
            "lease": wire.as_object(lease_request(IDENTITY)),
            "child": wire.as_object(child),
            "file": kept.stored_file_id,
            "archive_sha": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        spec["lease"]["mac_wall_clock_ms"] = NOW
        updates = RecordingUpdates(archive)
        try:
            async with serve_executor_app(
                _transport_app(endpoint, updates), tmp_path / "source-tls"
            ) as proxy:
                OWNED_ORIGINS.add(proxy.origin)
                try:
                    expected = await transcript(proxy, spec)
                    expected_core = await continue_core(endpoint, path, proxy)
                finally:
                    OWNED_ORIGINS.remove(proxy.origin)
        finally:
            await endpoint.close()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host: dict[str, Any] = {
            **spec,
            "path": str(candidate / "panels.sqlite"),
            "archive": str(candidate / "archive.gz"),
            "ready": str(tmp_path / "ready.json"),
            "state": str(tmp_path / "state.json"),
            "fd": listener.fileno(),
            "continuation": str(tmp_path / "continuation.json"),
        }
        (tmp_path / "host.json").write_text(json.dumps(host))
        env = {
            **homes,
            "PATH": os.environ["PATH"],
            "TMPDIR": str(tmp_path),
            "PLAN_TEST_MODE": "1",
            "PLAN_SERVER_URL": "http://127.0.0.1:0",
        }
        process = subprocess.Popen(
            [
                "node",
                str(ROOT / "server/executor/testing/app_transport.ts"),
                str(tmp_path / "host.json"),
            ],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            pass_fds=(listener.fileno(),),
            start_new_session=True,
        )
        try:

            async def read(name: str) -> Any:
                async with asyncio.timeout(10):
                    while not Path(host[name]).exists():
                        assert process.poll() is None, process.communicate()
                        await asyncio.sleep(0.01)
                return json.loads(Path(host[name]).read_text())

            ready = await read("ready")
            assert (ready["address"], ready["port"]) == listener.getsockname()
            with isolated_https_proxy(
                tmp_path / "candidate-tls", f"http://127.0.0.1:{ready['port']}"
            ) as proxy:
                OWNED_ORIGINS.add(proxy.origin)
                try:
                    actual = await transcript(proxy, spec)
                    # Query parameters bypass the JSON codec but keep its integer admission.
                    async with proxy.session_factory() as client:
                        async with client.get(
                            owned_url(
                                proxy.origin, "/api/executor/commands?after=9007199254740992&wait=0"
                            ),
                            headers={
                                "X-Panels-Executor-ID": "test-executor",
                                "X-Panels-Executor-Incarnation": IDENTITY.executor_incarnation,
                                "X-Panels-Executor-Protocol": str(PROTOCOL_VERSION),
                            },
                        ) as response:
                            assert response.status == 400
                            unsafe_poll = await response.json()
                            assert unsafe_poll["error"]["code"] == "validation"
                    for origin, route in (
                        ("https://127.0.0.1:1", "/api/executor/lease"),
                        (proxy.origin, "https://panels.invalid/api/executor/lease"),
                        (proxy.origin, "//panels.invalid/api/executor/lease"),
                    ):
                        with pytest.raises(ValueError):
                            owned_url(origin, route)
                finally:
                    OWNED_ORIGINS.remove(proxy.origin)
            assert process.stdin is not None
            process.stdin.write("continue\n")
            process.stdin.flush()
            actual_core = await read("continuation")
            stdout, stderr = await asyncio.to_thread(process.communicate, "close\n", timeout=10)
            assert process.returncode == 0, stdout + stderr
            final = await read("state")
            assert final["catalog_state"] == "withdrawn" and final["descriptors"] == []
            assert final["poll_disconnected"] is True
            assert final["reports"] == updates.reports

            def comparable(rows: list[Any]) -> list[Any]:
                status, content, headers = rows[-2]  # The invalid MacUpdateReport phase.
                assert status == 409 and set(content) == {"detail"}
                detail = content["detail"]
                assert isinstance(detail, str) and "phase" in detail
                return [
                    *rows[:-2],
                    [status, {"detail": "<invalid update phase>"}, headers],
                    rows[-1],
                ]

            assert comparable(actual) == comparable(expected) and actual_core == expected_core
            return {
                "source": expected,
                "candidate": actual,
                "core_source": expected_core,
                "core_candidate": actual_core,
                "comparison_rule": (
                    "Invalid phase keeps its HTTP status and error shape with native text."
                ),
                "guard_refusals": 3,
                "unsafe_poll": unsafe_poll,
                "refused_connection_attempts": 0,
                "remaining_input_descriptors": [],
                "catalog_state_after_stop": final["catalog_state"],
                "disconnected_poll_settled": final["poll_disconnected"],
            }
        finally:
            listener.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    with (
        synthetic_home_environment(homes),
        patch("planner.executor.vps_presence.now_ms", return_value=NOW),
        patch("planner.executor.vps_mailbox.now_ms", return_value=NOW),
        patch("planner.executor.vps_inbox.now_ms", return_value=NOW),
    ):
        evidence = asyncio.run(asyncio.wait_for(run(), 45))
    if directory := request.config.getoption("--compare-report-dir"):
        target = Path(directory) / "unit-22-app-transport.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"elapsed_seconds": time.perf_counter() - started, **evidence}, indent=2)
            + "\n"
        )


async def credential_transcript(
    proxy: HttpsProxy, path: Path, named_machine: str
) -> list[dict[str, Any]]:
    original = {
        "X-Panels-Executor-ID": "test-executor",
        "X-Panels-Executor-Credential": "a" * 43,
        "X-Panels-Executor-Incarnation": IDENTITY.executor_incarnation,
    }
    named = {
        **original,
        "X-Panels-Executor-ID": named_machine,
        "X-Panels-Executor-Credential": "b" * 43,
        "X-Panels-Executor-Incarnation": "22222222-2222-4222-8222-222222222222",
        "X-Panels-Executor-Platform": "linux",
    }
    legacy = {"X-Panels-Executor-ID": "test-executor"}
    current = {"request_id": "fixture", "application_revision": "a" * 40, "phase": "current"}
    failed = {**current, "phase": "failed"}
    operations: list[
        tuple[str, str, dict[str, str] | list[tuple[str, str]], dict[str, str] | None]
    ] = [
        ("authenticate", "GET", original, None),
        ("platform", "GET", named, None),
        ("foreign", "POST", {**original, "X-Panels-Executor-Credential": "b" * 43,
                             "X-Panels-Executor-Platform": "linux"}, current),
        ("duplicate", "GET", [*original.items(),
                              ("X-Panels-Executor-Credential", "a" * 43)], None),
        ("refused", "POST", original, {**current, "request_id": "refused"}),
        ("legacy_current", "POST", legacy, current),
        ("other_phase", "POST", original, failed),
        ("named_report", "POST", named, failed),
        ("confirmed", "POST", original, current),
        ("legacy_open", "GET", legacy, None),
    ]
    routed: list[
        tuple[str, str, str, dict[str, str] | list[tuple[str, str]], dict[str, str] | None]
    ] = [
        ("bootstrap_login", "POST", "/api/executor/credential", legacy, None),
        ("bootstrap_credential", "POST", "/api/executor/credential", original, None),
        ("bootstrap_origin", "POST", "/api/executor/credential",
         {**legacy, "Origin": "https://owner.invalid"}, {}),
        *((name, method, "/api/executor/update", headers, body)
          for name, method, headers, body in operations),
        ("close_legacy", "POST", "/api/executor/credential/handover/close", legacy, None),
        ("close_foreign", "POST", "/api/executor/credential/handover/close",
         {**original, "X-Panels-Executor-Credential": "b" * 43}, None),
        ("close_duplicate", "POST", "/api/executor/credential/handover/close",
         [*original.items(), ("X-Panels-Executor-Credential", "a" * 43)], None),
        ("close", "POST", "/api/executor/credential/handover/close",
         {key: value for key, value in original.items()
          if key != "X-Panels-Executor-Incarnation"}, None),
        ("close_repeat", "POST", "/api/executor/credential/handover/close", original, None),
        ("legacy_closed", "GET", "/api/executor/update", legacy, None),
        ("credential_kept", "GET", "/api/executor/update", original, None),
        ("enrollment", "POST", "/api/machines", {},
         {"name": "Enrolled Linux", "platform": "linux"}),
    ]
    answers = []
    async with proxy.session_factory() as client:
        for name, method, route, headers, body in routed:
            pairs = list(headers.items()) if isinstance(headers, dict) else headers
            origin = proxy.upstream if name == "bootstrap_login" else proxy.origin
            assert origin is not None

            async def call(
                method: str = method, origin: str = origin, route: str = route,
                pairs: list[tuple[str, str]] = pairs, body: dict[str, str] | None = body,
                name: str = name,
            ) -> dict[str, Any]:
                async with client.request(
                    method, owned_url(origin, route),
                    headers=[("Accept-Encoding", "identity"), *pairs],
                    json=body, allow_redirects=False,
                ) as response:
                    return {
                        "name": name, "status": response.status,
                        "body": await response.json(), "credentials": credential_rows(path),
                    }

            if name == "enrollment":
                pending = asyncio.create_task(call())
                release = Path(str(path) + ".ensure-release")
                try:
                    async with asyncio.timeout(5):
                        while not Path(str(path) + ".ensure-started.json").exists():
                            assert not pending.done(), "grant returned without registry admission"
                            await asyncio.sleep(0.001)
                    assert not pending.done(), "grant response did not wait for registry admission"
                finally:
                    release.touch()
                    answer = await pending
                answer["waited_for_ensure"] = True
                answers.append(answer)
            else:
                answers.append(await call())
    assert [answer["status"] for answer in answers] == [
        403, 403, 403, 200, 200, 403, 403, 409, 200, 200, 200, 200, 200,
        403, 403, 403, 200, 200, 403, 200, 200,
    ]
    for answer in answers:
        rows = {row["machine_id"]: row for row in answer["credentials"]}
        original_row = rows[ORIGINAL_MAC_MACHINE_ID]
        closed = answer["name"] in {
            "close", "close_repeat", "legacy_closed", "credential_kept", "enrollment"
        }
        assert original_row["legacy_closed_at_ms"] == (NOW if closed else None)
        assert original_row["pending_credential"] == (None if closed else "a" * 43)
        assert original_row["platform"] == "darwin"
        assert rows[named_machine]["platform"] == (
            "darwin" if answer["name"] in {
                "bootstrap_login", "bootstrap_credential", "bootstrap_origin", "authenticate"
            } else "linux"
        )
    by_name = {answer["name"]: answer for answer in answers}
    assert (
        by_name["foreign"]["credentials"] == by_name["duplicate"]["credentials"]
        == by_name["platform"]["credentials"]
    )
    assert by_name["authenticate"]["body"]["prefetch"]["application_revision"] == "a" * 40
    assert by_name["platform"]["body"]["prefetch"]["application_revision"] == "b" * 40
    assert by_name["confirmed"]["body"] == {"confirmed": True}
    assert by_name["close"]["body"] == by_name["close_repeat"]["body"] == {"closed": True}
    assert by_name["close"]["credentials"] == by_name["close_repeat"]["credentials"]
    assert by_name["enrollment"]["body"] == {
        "machine_id": ENROLLED_MACHINE, "credential": ENROLLED_CREDENTIAL, "issued_at_ms": NOW,
    }
    return answers


def test_executor_credentialed_update_handover(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """The existing TLS peer proves credential admission and committed handover ordering."""
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    named_machine = "66666666-6666-4666-8666-666666666666"

    async def run() -> dict[str, Any]:
        path, candidate = tmp_path / "source.sqlite", tmp_path / "candidate.sqlite"
        archive = tmp_path / "archive.gz"
        archive.write_bytes(b"fixture archive")
        with closing(connect(str(path))) as conn:
            create_schema(conn)
            conn.execute("INSERT INTO machines VALUES (?,'Named','server','cyan',0)",
                         (named_machine,))
            conn.execute(
                "UPDATE machine_executor_credentials SET credential_sha256=?,issued_at_ms=?,"
                "issued_incarnation=?,pending_credential=? WHERE machine_id=?",
                (hashlib.sha256(("a" * 43).encode()).hexdigest(), NOW - 5,
                 IDENTITY.executor_incarnation, "a" * 43, ORIGINAL_MAC_MACHINE_ID),
            )
            conn.execute(
                "INSERT INTO machine_executor_credentials(machine_id,credential_sha256,"
                "issued_at_ms,issued_incarnation,legacy_closed_at_ms) VALUES (?,?,?,?,?)",
                (named_machine, hashlib.sha256(("b" * 43).encode()).hexdigest(), NOW - 5,
                 "22222222-2222-4222-8222-222222222222", NOW - 5),
            )
            for machine, incarnation in (
                (ORIGINAL_MAC_MACHINE_ID, IDENTITY.executor_incarnation),
                (named_machine, "22222222-2222-4222-8222-222222222222"),
            ):
                conn.execute(
                    "INSERT INTO executor_incarnations(executor_incarnation,first_leased_at_ms,"
                    "machine_id) VALUES (?,?,?)", (incarnation, NOW - 5, machine),
                )
                # Updates need no work identity, lease, or protocol admission.
                conn.execute(
                    "INSERT OR REPLACE INTO mac_executor_presence(machine_id,executor_incarnation)"
                    " VALUES (?,?)", (machine, incarnation),
                )
            conn.commit()
        with sqlite3.connect(path) as source, sqlite3.connect(candidate) as destination:
            source.backup(destination)
        clock = FixedClock(datetime.fromtimestamp(NOW / 1000, UTC))
        registry = VpsExecutorRegistry(str(path), clock, original_enabled=True)
        with closing(connect(str(path))) as conn:
            registry.load(conn)
        endpoint = registry.get(ORIGINAL_MAC_MACHINE_ID)
        assert endpoint is not None
        for machine in (ORIGINAL_MAC_MACHINE_ID, named_machine):
            owner = registry.get(machine)
            assert owner is not None
            owner.presence._clock = lambda: NOW
            owner.presence.record()
        updates = CredentialUpdates(archive, path)
        app = _transport_app(endpoint, updates)
        app.state.executor_registry = registry
        app.state.conn_factory = lambda: connect(str(path))
        app.include_router(machine_credential_router, prefix="/api")
        enrollment: list[dict[str, Any]] = []
        enrollment_responses: list[dict[str, bool]] = []
        original_ensure = registry.ensure

        @app.middleware("http")
        async def observe_enrollment_response(request: Any, call_next: Any) -> Any:
            response = await call_next(request)
            if request.method == "POST" and request.url.path == "/api/machines":
                enrollment_responses.append({
                    "released": Path(str(path) + ".ensure-release").exists(),
                    "owner_published": registry.get(ENROLLED_MACHINE) is not None,
                })
            return response

        async def observe_ensure(machine_id: str) -> VpsExecutor:
            if machine_id != ENROLLED_MACHINE:
                return await original_ensure(machine_id)
            with closing(connect(str(path))) as fresh:
                row = fresh.execute(
                    "SELECT m.name,m.icon,m.color,m.is_home,c.* FROM machines m "
                    "JOIN machine_executor_credentials c USING(machine_id) WHERE machine_id=?",
                    (machine_id,),
                ).fetchone()
            assert row is not None, "registry admission preceded the registration commit"
            evidence: dict[str, Any] = {
                "machine_id": machine_id, "committed": dict(row),
                "owner_before": registry.get(machine_id) is not None,
            }
            enrollment.append(evidence)
            Path(str(path) + ".ensure-started.json").write_text(json.dumps(evidence))
            while not Path(str(path) + ".ensure-release").exists():
                await asyncio.sleep(0.001)
            owner = await original_ensure(machine_id)
            evidence["owner_after"] = registry.get(machine_id) is owner
            return owner

        registry.ensure = observe_ensure  # type: ignore[method-assign]
        try:
            async with serve_executor_app(app, tmp_path / "source-tls") as proxy:
                assert proxy.upstream is not None
                OWNED_ORIGINS.update((proxy.origin, proxy.upstream))
                try:
                    expected = await credential_transcript(proxy, path, named_machine)
                finally:
                    OWNED_ORIGINS.difference_update((proxy.origin, proxy.upstream))
        finally:
            await registry.close()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        host: dict[str, Any] = {
            "path": str(candidate), "archive": str(archive),
            "archive_sha": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "ready": str(tmp_path / "ready.json"), "state": str(tmp_path / "state.json"),
            "continuation": str(tmp_path / "unused.json"), "fd": listener.fileno(),
            "auth_mode": True,
        }
        spec = tmp_path / "host.json"
        spec.write_text(json.dumps(host))
        process = subprocess.Popen(
            ["node", str(ROOT / "server/executor/testing/app_transport.ts"), str(spec)],
            cwd=ROOT, env={**homes, "PATH": os.environ["PATH"], "TMPDIR": str(tmp_path),
                           "PLAN_TEST_MODE": "1", "PLAN_SERVER_URL": "http://127.0.0.1:0"},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, pass_fds=(listener.fileno(),), start_new_session=True,
        )
        try:
            async with asyncio.timeout(10):
                while not Path(host["ready"]).exists():
                    assert process.poll() is None, process.communicate()
                    await asyncio.sleep(0.01)
            ready = json.loads(Path(host["ready"]).read_text())
            assert (ready["address"], ready["port"]) == listener.getsockname()
            with isolated_https_proxy(
                tmp_path / "candidate-tls", f"http://127.0.0.1:{ready['port']}"
            ) as proxy:
                assert proxy.upstream is not None
                OWNED_ORIGINS.update((proxy.origin, proxy.upstream))
                try:
                    actual = await credential_transcript(proxy, candidate, named_machine)
                finally:
                    OWNED_ORIGINS.difference_update((proxy.origin, proxy.upstream))
            stdout, stderr = await asyncio.to_thread(process.communicate, "close\n", timeout=10)
            assert process.returncode == 0, stdout + stderr
            final = json.loads(Path(host["state"]).read_text())
            assert actual == expected
            assert final == {
                "reports": updates.reports, "credentials": credential_rows(path),
                "enrollment": enrollment, "enrollment_responses": enrollment_responses,
            }
            assert len(enrollment) == 1
            assert not enrollment[0]["owner_before"] and enrollment[0]["owner_after"]
            assert enrollment_responses == [{"released": True, "owner_published": True}]
            assert [report[0] for report in updates.reports] == [
                ORIGINAL_MAC_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID,
                named_machine, ORIGINAL_MAC_MACHINE_ID,
            ]
            assert [report[1] for report in updates.reports] == [
                "darwin", "darwin", "darwin", "linux", "darwin",
            ]
            # Reports leave authority open. Only the explicit close response sees its commit.
            assert all(
                row["legacy_closed_at_ms"] is None
                for report in updates.reports for row in report[4]
                if row["machine_id"] == ORIGINAL_MAC_MACHINE_ID
            )
            return {"answers": actual, **final}
        finally:
            listener.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    with (
        synthetic_home_environment(homes),
        patch("planner.executor.vps_presence.now_ms", return_value=NOW),
        patch("planner.machines.executor_credentials.time.time_ns", return_value=NOW * 1_000_000),
        patch("planner.machines.executor_credentials.uuid4", return_value=UUID(ENROLLED_MACHINE)),
        patch("planner.machines.executor_credentials.secrets.token_urlsafe",
              return_value=ENROLLED_CREDENTIAL),
    ):
        evidence = asyncio.run(asyncio.wait_for(run(), 35))
    if directory := request.config.getoption("--compare-report-dir"):
        target = Path(directory) / "unit-22-credential-update-routes.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "elapsed_seconds": time.perf_counter() - started,
            "boundary": "Existing TLS peer: machine authentication, selector, and handover commit",
            **evidence,
        }, indent=2) + "\n")
