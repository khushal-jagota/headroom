"""Hosted-only Node Mac peer over the existing Python VPS/TLS fixture."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from tests.integration.test_executor_end_to_end import (
    CutReceiptEndpoint,
    create_mac_ticket,
    make_core,
    start_ticket_conversation,
)
from tests.support.executor_https import HttpsProxy, serve_executor_app, short_transport_root
from tests.support.synthetic_homes import create_synthetic_homes

from planner.conversation.message_content import MessageFile, MessageText
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema
from planner.execution_identity import AskKind, AskReference, ExecutorIdentity
from planner.executor.protocol import (
    EnvelopeKind,
    ItemBatch,
    ItemBatchReply,
    QueuedEnvelope,
    type_adapter,
)

ROOT = Path(__file__).resolve().parents[3]


async def eventually(
    condition: Callable[[], Awaitable[bool]], detail: str, seconds: float = 12
) -> None:
    try:
        async with asyncio.timeout(seconds):
            while not await condition():
                await asyncio.sleep(0.03)
    except TimeoutError as error:
        raise AssertionError(detail) from error


class Endpoint(CutReceiptEndpoint):
    """Hold specific existing endpoint operations. Only the proxy cuts the link."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self.input_entered, self.input_release = asyncio.Event(), asyncio.Event()
        self.poll_entered, self.poll_release = asyncio.Event(), asyncio.Event()
        self.input_release.set()
        self.poll_release.set()
        self.input_attempts = 0
        self.cut_next_event = False
        self.cut_envelope: QueuedEnvelope | None = None

    async def input_file(
        self, identity: ExecutorIdentity, command_id: str, stored_file_id: str
    ) -> Any:
        self.input_attempts += 1
        self.input_entered.set()
        await self.input_release.wait()
        return await super().input_file(identity, command_id, stored_file_id)

    async def poll(self, identity: ExecutorIdentity, after: int, wait_seconds: float) -> Any:
        if not self.poll_release.is_set():
            self.poll_entered.set()
            await self.poll_release.wait()
        return await super().poll(identity, after, wait_seconds)

    async def accept_items(
        self, identity: ExecutorIdentity, conversation_id: str, batch: ItemBatch
    ) -> ItemBatchReply:
        if self.cut_next_event:
            for item in batch.items:
                if item.envelope.kind is EnvelopeKind.backend_event:
                    self.cut_next_event = False
                    self.cut_envelope = item.envelope
                    self.cut_submission = item.envelope.submission_id
                    break
        return await super().accept_items(identity, conversation_id, batch)


class NodePeer:
    def __init__(self, process: asyncio.subprocess.Process, log: Path) -> None:
        self.process, self.log = process, log
        self.lock = asyncio.Lock()
        self.sequence = 0

    async def line(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        line = await self.process.stdout.readline()
        assert line, self.log.read_text()
        value: dict[str, Any] = json.loads(line)
        return value

    async def call(self, operation: str, **arguments: Any) -> dict[str, Any]:
        async with self.lock:
            self.sequence += 1
            assert self.process.stdin is not None
            self.process.stdin.write(
                (
                    json.dumps(dict(id=self.sequence, operation=operation, **arguments)) + "\n"
                ).encode()
            )
            await self.process.stdin.drain()
            reply = await asyncio.wait_for(self.line(), 25)
            assert reply.get("id") == self.sequence, reply
            assert "error" not in reply, reply
            result: dict[str, Any] = reply["result"]
            return result

    async def close(self) -> None:
        try:
            if self.process.returncode is None:
                await self.call("stop")
                assert self.process.stdin is not None
                self.process.stdin.close()
                await asyncio.wait_for(self.process.wait(), 25)
            assert self.process.returncode == 0, self.log.read_text()
        finally:
            if self.process.returncode is None:
                os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()


class Harness:
    def __init__(
        self,
        root: Path,
        mac_root: Path,
        endpoint: Endpoint,
        proxy: HttpsProxy,
        peer: NodePeer,
        system: SqliteProcessConversationSystem,
        app: FastAPI,
        ticket_id: str,
    ) -> None:
        self.root, self.mac_root, self.endpoint, self.proxy, self.peer = (
            root,
            mac_root,
            endpoint,
            proxy,
            peer,
        )
        self.system = system
        self.transport_app, self.ticket_id = app, ticket_id
        self.old_systems: list[SqliteProcessConversationSystem] = []
        self.endpoints = [endpoint]

    async def restart_vps_core(self) -> None:
        await asyncio.wait_for(self.endpoint.close(), 2)
        self.system._runtime_ownership.close()
        self.old_systems.append(self.system)
        self.endpoint = Endpoint(self.endpoint.db_path)
        self.endpoint.proxy = self.proxy
        self.endpoints.append(self.endpoint)
        self.system = make_core(self.endpoint)
        self.endpoint.bind_conversations(self.system)
        self.transport_app.state.executor_endpoint = self.endpoint
        await self.endpoint.start()

    def events(self, kind: str) -> int:
        with contextlib.closing(connect(self.endpoint.db_path)) as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM conversation_events "
                    "WHERE conversation_id='c_scripted' AND kind=?",
                    (kind,),
                ).fetchone()[0]
            )

    def commands(self, operation: str) -> list[tuple[str, str]]:
        with contextlib.closing(connect(self.endpoint.db_path)) as connection:
            return [
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT command_id,state FROM executor_commands WHERE operation=? "
                    "ORDER BY command_sequence",
                    (operation,),
                )
            ]

    def journal_rows(self, command_id: str) -> int:
        with contextlib.closing(
            sqlite3.connect(f"file:{self.mac_root / 'spool/commands.sqlite3'}?mode=ro", uri=True)
        ) as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM commands WHERE command_id=?", (command_id,)
                ).fetchone()[0]
            )

    def input_path(self, stored_file_id: str) -> Path:
        return self.mac_root / "artifacts/files/conversations/c_scripted" / stored_file_id

    async def state(self) -> dict[str, Any]:
        return await self.peer.call("state")

    async def wait_calls(self, **counts: int) -> None:
        async def ready() -> bool:
            state = await self.state()
            return all(state["calls"][key] == value for key, value in counts.items())

        await eventually(ready, f"native counts did not reach {counts}")

    async def wait_event(self, kind: str, count: int, seconds: float = 12) -> None:
        async def ready() -> bool:
            return self.events(kind) == count

        await eventually(ready, f"{kind} did not reach {count}", seconds)

    async def wait_settled(self, seconds: float = 12) -> None:
        async def ready() -> bool:
            state = await self.state()
            with contextlib.closing(connect(self.endpoint.db_path)) as connection:
                waiting = connection.execute(
                    "SELECT count(*) FROM executor_commands WHERE state IN ('waiting','delivered')"
                ).fetchone()[0]
            return not state["pending"] and not state["unreported"] and waiting == 0

        await eventually(ready, "mailbox and outbox did not settle", seconds)

    async def end_turn(self) -> None:
        count = self.events("turn_ended")
        await self.peer.call("emit", kind="end")

        async def done() -> bool:
            return (
                self.events("turn_ended") == count + 1
                and self.system._conversations["c_scripted"].running_turn is None
            )

        await eventually(done, "turn did not settle")

    async def send_file(self, stored_file_id: str, size: int) -> Any:
        return await asyncio.wait_for(
            self.system.send(
                "c_scripted",
                (
                    MessageText("Inspect the attachment"),
                    MessageFile(stored_file_id, "application/octet-stream", "input.bin", size),
                ),
                sender_label="Test",
            ),
            5,
        )

    async def expect_input(self, stored_file_id: str, content: bytes) -> None:
        import base64

        await self.peer.call(
            "configure",
            expected_input={"id": stored_file_id, "base64": base64.b64encode(content).decode()},
        )

    async def ask(self, kind: AskKind) -> AskReference:
        from planner.execution_identity import ChildReference, TurnToken

        state = await self.state()
        return AskReference(
            type_adapter(ChildReference).validate_json(
                json.dumps(state["children"][0]["reference"]), strict=True
            ),
            type_adapter(TurnToken).validate_json(json.dumps(state["turn"]), strict=True),
            "ask_permission" if kind is AskKind.permission else "request_input",
            kind,
        )


@contextlib.asynccontextmanager
async def boot(root: Path, *, early_event: bool = False) -> AsyncIterator[Harness]:
    homes = create_synthetic_homes(root)
    database = root / "vps.sqlite"
    with contextlib.closing(connect(str(database))) as connection:
        create_schema(connection)
    ticket = create_mac_ticket(str(database), root, title="TypeScript transport worker")
    endpoint = Endpoint(str(database))
    system = make_core(endpoint)
    endpoint.bind_conversations(system)
    app = FastAPI()
    app.state.executor_id, app.state.executor_endpoint = "test-executor", endpoint
    from planner.core.response_compression import (
        CompressExceptMarkedRoutes,
        uncompressed_route_patterns,
    )
    from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
    from planner.executor.app_transport import build_executor_router

    router = build_executor_router()
    app.include_router(router)
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig("tailscale", "owner@example.com", "https://localhost"),
    )
    app.add_middleware(
        CompressExceptMarkedRoutes,
        uncompressed_patterns=uncompressed_route_patterns(router.routes),
        minimum_size=1024,
        compresslevel=4,
    )
    with short_transport_root() as mac_root:
        peer: NodePeer | None = None
        harness: Harness | None = None
        async with serve_executor_app(app, root / "tls") as proxy:
            endpoint.proxy = proxy
            try:
                await endpoint.start()
                environment = {
                    key: value for key, value in os.environ.items() if not key.startswith("PLAN_")
                }
                environment.update(homes)
                environment.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:9")
                source = root / "node.json"
                source.write_text(
                    json.dumps(
                        dict(
                            root=str(mac_root),
                            origin=proxy.origin,
                            fixture_origin=proxy.origin,
                            allocated_port=urlsplit(proxy.origin).port,
                            ca_path=str(proxy.ca_path),
                            environment=environment,
                            revision="a" * 40,
                            early_event=early_event,
                        )
                    )
                )
                log = root / "node.log"
                with log.open("wb") as output:
                    process = await asyncio.create_subprocess_exec(
                        "node",
                        "server/executor/mac/testing/end_to_end.ts",
                        str(source),
                        cwd=ROOT,
                        env=environment,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=output,
                        start_new_session=True,
                    )
                peer = NodePeer(process, log)
                harness = Harness(root, mac_root, endpoint, proxy, peer, system, app, ticket)
                assert await asyncio.wait_for(peer.line(), 20) == {"ready": True}

                async def leased() -> bool:
                    return (await harness.state())[
                        "leased"
                    ] and endpoint.health().presence == "present"

                await eventually(leased, "Node Mac was not leased")
                fate = await start_ticket_conversation(
                    harness.system, str(database), root, ticket, "c_scripted"
                )
                from planner.conversation.contracts import PromptDeliveryStarted

                assert isinstance(fate, PromptDeliveryStarted), fate
                await harness.wait_calls(starts=1, prompts=1)
                await harness.wait_settled()
                yield harness
            finally:
                for owned in [endpoint] if harness is None else harness.endpoints:
                    owned.input_release.set()
                    owned.poll_release.set()
                try:
                    if peer is not None:
                        await peer.close()
                finally:
                    for owned in [endpoint] if harness is None else harness.endpoints:
                        await owned.close()
                    for owned_system in (
                        [system] if harness is None else [*harness.old_systems, harness.system]
                    ):
                        for state in owned_system._conversations.values():
                            state.child = None
                        await owned_system.shutdown()
