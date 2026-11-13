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
from planner.core.db import connect, create_schema
from planner.environments.mac_updates import MacUpdateService
from planner.execution_identity import ChildReference
from planner.executor import protocol as wire
from planner.executor.update_contracts import MacUpdateCheck, MacUpdateReport, MacUpdateStatus
from planner.executor.vps_remote_child import MacRemoteChild
from planner.executor.vps_transport import VpsExecutor
from planner.tickets.data import claim_ticket_for_worker_step, write_ticket_conversation_start

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_760_000_000_000
IDENTITY = mac_identity("11111111-1111-4111-8111-111111111111")
OWNED_ORIGINS: set[str] = set()


def owned_url(origin: str, path: str) -> str:
    parsed = urlsplit(origin)
    if (
        origin not in OWNED_ORIGINS
        or parsed.scheme != "https"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or not path.startswith("/")
        or path.startswith("//")
    ):
        raise ValueError("request is outside this fixture's exact owned listener")
    return origin + path


class RecordingUpdates(MacUpdateService):
    def __init__(self, archive: Path) -> None:
        self.archive_file = archive
        self.reports: list[Any] = []

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


async def transcript(proxy: HttpsProxy, spec: dict[str, Any]) -> list[Any]:
    only_id = {"X-Panels-Executor-ID": "test-executor"}
    work = {
        **only_id,
        "X-Panels-Executor-Incarnation": IDENTITY.executor_incarnation,
        "X-Panels-Executor-Protocol": "22",
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
            "X-Panels-Executor-Protocol": "22",
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
        await killing
        endpoint.mailbox.put_child_command = original_put  # type: ignore[method-assign]
        await proof(await stop("withdraw"))
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
            assert actual == expected and actual_core == expected_core
            return {
                "source": expected,
                "candidate": actual,
                "core_source": expected_core,
                "core_candidate": actual_core,
                "guard_refusals": 3,
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
