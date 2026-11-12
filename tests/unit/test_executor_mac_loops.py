"""The Mac's loops against a scripted VPS that follows the protocol-22 routes.

Risks: one child's slow command stalls another child, or the poll; a prompt runs before
its child's start; a replaced process keeps working; the cursor stays above a restored
mailbox; update work delays the lease; a transfer is accepted with the wrong bytes or
retried forever, or waits behind another conversation's. Layer: the real MacService
(or the real HTTPS client) over TLS to an aiohttp stub in this process. Native agents
are scripted; nothing else is started.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ssl
import subprocess
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from aiohttp import web
from tests.unit.test_executor_native_runtime import canonical_snapshot
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendPromptAccepted,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.message_content import MessageFile, text_message_content
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.configuration import (
    EXECUTOR_FILE_SHA256_HEADER,
    EXECUTOR_FILE_UPLOAD_HEADER,
    EXECUTOR_ID_HEADER,
    EXECUTOR_INCARNATION_HEADER,
    EXECUTOR_PROTOCOL_HEADER,
    MAX_MAILBOX_POLL_BYTES,
    ExecutorPaths,
)
from planner.executor.contracts import MacHttpClient as MacHttpClientApi
from planner.executor.mac_files import InputDownloadRefused, MacFiles, UploadRefused
from planner.executor.mac_http import MacHttpClient
from planner.executor.mac_live import MacLiveSender
from planner.executor.mac_outbox import OutboxEntry
from planner.executor.mac_service import MacService
from planner.executor.protocol import (
    AcceptanceReceipt,
    BackendEvent,
    BackendEventMethod,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    CommandOutcomeReply,
    CommandOutcomeReport,
    CommandResult,
    EnvelopeKind,
    FileManifest,
    FileScope,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    LeaseRefused,
    LeaseReply,
    LeaseRequest,
    LiveEventBatch,
    MailboxCommand,
    MailboxPollReply,
    ReceiptDisposition,
    StopRun,
    WritePrompt,
    decode_bounded,
    decode_command_arguments,
    encode_bounded,
    encode_value,
)
from planner.executor.update_contracts import MacUpdateCheck, MacUpdateTarget

EXECUTOR_ID = "test-mac"


@pytest.fixture(scope="module")
def tls(tmp_path_factory: pytest.TempPathFactory) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """A certificate for 127.0.0.1, trusted only by this module's clients."""
    root = tmp_path_factory.mktemp("tls")
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(cert, key)
    return server, ssl.create_default_context(cafile=str(cert))


@dataclass
class VpsStub:
    """Routes 1-6 and 8 as §1 defines them, scripted by each test."""

    mailbox: list[MailboxCommand] = field(default_factory=list)
    high_sequence: int | None = None  # None: the highest command in the mailbox.
    lease_refusal: LeaseRefused | None = None
    update: MacUpdateCheck = field(default_factory=lambda: MacUpdateCheck(1, None))
    poll_hold_seconds: float = 0.2
    leases: list[LeaseRequest] = field(default_factory=list)
    polls: list[int] = field(default_factory=list)
    results: dict[str, CommandResult] = field(default_factory=dict)
    items: list[ItemBatch] = field(default_factory=list)
    uploads: list[tuple[FileUploadRequest, bytes]] = field(default_factory=list)
    upload_status: int = 204
    files: dict[str, bytes] = field(default_factory=dict)
    file_failures: int = 0
    # Transfers of files whose name starts with "slow" wait for this, counted in holding.
    slow_release: asyncio.Event = field(default_factory=asyncio.Event)
    holding: int = 0
    live: list[LiveEventBatch] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def add(self, command: MailboxCommand) -> None:
        self.mailbox.append(command)
        self.changed.set()

    def _check(self, request: web.Request) -> None:
        assert request.headers[EXECUTOR_ID_HEADER] == EXECUTOR_ID
        assert request.headers[EXECUTOR_PROTOCOL_HEADER] == "22"
        assert request.headers[EXECUTOR_INCARNATION_HEADER]

    async def lease(self, request: web.Request) -> web.Response:
        self._check(request)
        lease = decode_bounded(await request.read(), LeaseRequest)
        self.leases.append(lease)
        if self.lease_refusal is not None:
            return web.Response(status=409, body=encode_bounded(self.lease_refusal))
        high = self.high_sequence
        if high is None:
            high = max((command.command_sequence for command in self.mailbox), default=0)
        reply = LeaseReply(1, 60.0, self.update, high)
        return web.Response(body=encode_bounded(reply), content_type="application/json")

    async def commands(self, request: web.Request) -> web.Response:
        self._check(request)
        after = int(request.query["after"])
        assert 0 <= int(request.query["wait"]) <= 50
        self.polls.append(after)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.poll_hold_seconds
        while not (ready := [c for c in self.mailbox if c.command_sequence > after]):
            self.changed.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self.changed.wait(), remaining)
            except TimeoutError:
                break
        reply = MailboxPollReply(tuple(sorted(ready, key=lambda c: c.command_sequence)[:32]))
        return web.Response(
            body=encode_bounded(reply, limit=MAX_MAILBOX_POLL_BYTES),
            content_type="application/json",
        )

    async def outcomes(self, request: web.Request) -> web.Response:
        self._check(request)
        report = decode_bounded(await request.read(), CommandOutcomeReport)
        for result in report.outcomes:
            self.results.setdefault(result.command_id, result)  # The first write wins.
        reply = CommandOutcomeReply(tuple(r.command_id for r in report.outcomes))
        return web.Response(body=encode_bounded(reply), content_type="application/json")

    async def post_items(self, request: web.Request) -> web.Response:
        self._check(request)
        batch = decode_bounded(await request.read(), ItemBatch)
        assert {i.envelope.conversation_id for i in batch.items} == {
            request.match_info["conversation_id"]
        }
        self.items.append(batch)
        receipts = tuple(
            AcceptanceReceipt(
                batch.sender_id, item.envelope.submission_id, ReceiptDisposition.accepted, {}, 1
            )
            for item in batch.items
        )
        return web.Response(body=encode_bounded(ItemBatchReply(receipts)))

    async def upload(self, request: web.Request) -> web.Response:
        self._check(request)
        described = request.headers[EXECUTOR_FILE_UPLOAD_HEADER]
        upload = decode_bounded(
            base64.urlsafe_b64decode(described + "=" * (-len(described) % 4)), FileUploadRequest
        )
        body = await request.read()
        assert request.match_info["sha256"] == upload.manifest.sha256
        assert int(request.headers["Content-Length"]) == upload.manifest.byte_count == len(body)
        self.uploads.append((upload, body))
        await self._slow(upload.manifest.relative_path)
        if self.upload_status != 204:
            error = {"error": {"code": "agent_forbidden", "message": "not yours", "detail": {}}}
            return web.json_response(error, status=self.upload_status)
        return web.Response(status=204)

    async def command_file(self, request: web.Request) -> web.Response:
        self._check(request)
        if self.file_failures:
            self.file_failures -= 1
            return web.json_response({"error": {"code": "retry_later"}}, status=503)
        content = self.files.get(request.match_info["stored_file_id"])
        if content is None:
            return web.json_response({"error": {"code": "not_found"}}, status=404)
        await self._slow(request.match_info["stored_file_id"])
        return web.Response(
            body=content,
            content_type="application/octet-stream",
            headers={EXECUTOR_FILE_SHA256_HEADER: hashlib.sha256(content).hexdigest()},
        )

    async def _slow(self, name: str) -> None:
        if name.startswith("slow"):
            self.holding += 1
            await self.slow_release.wait()

    async def post_live(self, request: web.Request) -> web.Response:
        self._check(request)
        self.live.append(decode_bounded(await request.read(), LiveEventBatch))
        return web.Response(status=204)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/executor/lease", self.lease)
        app.router.add_get("/api/executor/commands", self.commands)
        app.router.add_post("/api/executor/commands/outcomes", self.outcomes)
        app.router.add_post("/api/executor/conversations/{conversation_id}/items", self.post_items)
        app.router.add_put("/api/executor/uploads/{sha256}", self.upload)
        app.router.add_get(
            "/api/executor/commands/{command_id}/files/{stored_file_id}", self.command_file
        )
        app.router.add_post("/api/executor/conversations/{conversation_id}/live", self.post_live)
        return app


@asynccontextmanager
async def serving(stub: VpsStub, server_tls: ssl.SSLContext) -> AsyncIterator[str]:
    runner = web.AppRunner(stub.app(), shutdown_timeout=0.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_tls)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"https://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


async def until(predicate: Callable[[], bool], timeout: float = 10) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


@pytest.fixture
def paths(executor_transport_home: Path) -> Iterator[ExecutorPaths]:
    yield ExecutorPaths(executor_transport_home / "executor")


def service(paths: ExecutorPaths, origin: str, client_tls: ssl.SSLContext) -> MacService:
    return MacService(
        paths=paths,
        revision="a" * 40,
        executor_id=EXECUTOR_ID,
        panels_server_url=origin,
        ssl_context=client_tls,
    )


async def stopped(mac: MacService, running: asyncio.Task[None]) -> None:
    mac._stop.set()
    await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), 10)


class GatedChild:
    """A scripted native child whose start can be held."""

    def __init__(self, calls: list[str], name: str, gate: asyncio.Event) -> None:
        self.calls, self.name, self.gate = calls, name, gate

    async def start(self, resolved: ResolvedConversationStart, **kwargs: object) -> None:
        self.calls.append(f"{self.name}:start")
        await self.gate.wait()

    async def write_prompt(self, turn: TurnToken, *args: object, **kwargs: object) -> object:
        self.calls.append(f"{self.name}:prompt")
        return BackendPromptAccepted(True)

    async def stop(self) -> None:
        return None


def start_command(child: ChildReference, workspace: Path, ticket_id: str) -> ChildCommand:
    resolved = ResolvedConversationStart(
        child.conversation_id,
        ConversationBackendKey.codex,
        "test",
        None,
        ConversationRoleMaterials("worker", (("PLAN_TICKET_ID", ticket_id),)),
        workspace,
        ConversationAccess.full,
    )
    return ChildCommand(
        f"start_{child.child_id}",
        child,
        ChildOperation.start,
        None,
        decode_command_arguments(
            ChildOperation.start,
            {
                "canonical_skill_snapshot": encode_value(canonical_snapshot()),
                "resolved_start": encode_value(resolved),
            },
        ),
    )


def prompt_command(child: ChildReference, *, files: tuple[str, ...] = ()) -> ChildCommand:
    content = (
        *text_message_content("hello"),
        *(MessageFile(name, "text/plain", name, 1) for name in files),
    )
    return ChildCommand(
        f"prompt_{child.child_id}",
        child,
        ChildOperation.write_prompt,
        TurnToken(child.conversation_id, 1),
        WritePrompt(
            content=content,
            sender_content=content,
            sender_label="Owner",
            mode=PromptDeliveryMode.queue,
        ),
    )


def test_each_child_runs_in_order_and_no_child_or_the_poll_waits_on_another(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    paths: ExecutorPaths,
    tmp_path: Path,
) -> None:
    async def run() -> None:
        stub = VpsStub()
        async with serving(stub, tls[0]) as origin:
            mac = service(paths, origin, tls[1])
            calls: list[str] = []
            held = asyncio.Event()
            released = asyncio.Event()
            released.set()

            def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
                name = "a" if resolved.conversation_id == "c_a" else "b"

                def create(**kwargs: object) -> BackendChild:
                    return cast(
                        BackendChild, GatedChild(calls, name, held if name == "a" else released)
                    )

                return cast(BackendChildFactory, create)

            mac.runner.factory = factory
            incarnation = mac.identity.executor_incarnation
            a = ChildReference(incarnation, str(uuid4()), "c_a")
            b = ChildReference(incarnation, str(uuid4()), "c_b")
            commands = (
                start_command(a, tmp_path, "t_a"),
                prompt_command(a),
                start_command(b, tmp_path, "t_b"),
                prompt_command(b),
            )
            for sequence, command in enumerate(commands, 1):
                stub.add(MailboxCommand(sequence, command.command_id, 1, child_command=command))
            running = asyncio.create_task(mac.run())
            try:
                await until(lambda: f"prompt_{b.child_id}" in stub.results)
                # A's start is still held: its prompt has not run, B's has, and the poll
                # went on from the recorded cursor.
                assert calls.count("a:prompt") == 0 and "b:prompt" in calls
                await until(lambda: 4 in stub.polls)
                assert f"start_{a.child_id}" not in stub.results
                held.set()
                await until(lambda: f"prompt_{a.child_id}" in stub.results)
                a_calls = [call for call in calls if call.startswith("a:")]
                assert a_calls == ["a:start", "a:prompt"]
                for command in commands:
                    outcome = stub.results[command.command_id].child_outcome
                    assert outcome is not None
                    assert outcome.disposition is CommandDisposition.succeeded
            finally:
                await stopped(mac, running)
            # Every command ran once, though the poll answered all four each time.
            assert sorted(calls) == ["a:prompt", "a:start", "b:prompt", "b:start"]

    asyncio.run(run())


def test_replaced_incarnation_stops_admission_and_exits_1(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    paths: ExecutorPaths,
) -> None:
    async def run() -> None:
        stub = VpsStub(
            lease_refusal=LeaseRefused(
                {"code": "executor_incarnation_not_current", "message": "replaced", "detail": {}},
                None,
                None,
            )
        )
        async with serving(stub, tls[0]) as origin:
            mac = service(paths, origin, tls[1])
            with pytest.raises(SystemExit) as exited:
                await asyncio.wait_for(mac.run(), 10)
            assert exited.value.code == 1
            assert mac.runner.admission_stopped
            assert mac.presence.last_lease_failure == (
                "lease refused: executor_incarnation_not_current"
            )

    asyncio.run(run())


def test_cursor_falls_to_a_restored_mailbox_and_a_command_already_run_is_not_rerun(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    paths: ExecutorPaths,
) -> None:
    async def run() -> None:
        stub = VpsStub()
        async with serving(stub, tls[0]) as origin:
            mac = service(paths, origin, tls[1])
            incarnation = mac.identity.executor_incarnation
            gone = ChildReference(incarnation, str(uuid4()), "c_gone")
            done = ChildCommand("ran_before_restore", gone, ChildOperation.stop, None, StopRun())
            fresh = ChildCommand("after_restore", gone, ChildOperation.stop, None, StopRun())
            # This process ran sequence 40 before the VPS database was restored.
            mac.journal.record_poll_reply(
                MailboxPollReply((MailboxCommand(40, done.command_id, 1, child_command=done),)),
                incarnation,
            )
            ran = CommandResult(
                done.command_id,
                CommandOutcome(done.command_id, gone, CommandDisposition.succeeded, {}),
            )
            mac.journal.finish(done.command_id, ran, outputs_owed=False)
            mac.journal.mark_reported([done.command_id])
            stub.add(MailboxCommand(1, done.command_id, 1, child_command=done))
            stub.add(MailboxCommand(2, fresh.command_id, 1, child_command=fresh))
            running = asyncio.create_task(mac.run())
            try:
                # The repeat owes its stop proof again, so its report can follow fresh's.
                await until(lambda: {fresh.command_id, done.command_id} <= set(stub.results))
                assert 0 in stub.polls  # Lowered from 40 by the lease reply.
                # The restored copy was recorded before: reported again, never rerun.
                assert stub.results[done.command_id] == ran
                assert done.command_id not in mac.runner._commands
                assert stub.leases[0].identity == mac.identity
            finally:
                await stopped(mac, running)

    asyncio.run(run())


def test_a_lease_reply_older_than_a_fetched_command_never_lowers_it(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    paths: ExecutorPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Risk: a lease reply whose high sequence was read before command S was put arrives
    after the poll recorded S. Lowering S makes the VPS serve it again, and its owed
    output is published again under new item numbers, which the VPS takes as new."""
    monkeypatch.setattr("planner.executor.mac_presence.LEASE_INTERVAL_SECONDS", 0.05)

    async def run() -> None:
        stub = VpsStub()
        reply_lease = stub.lease
        raced = False

        async def racing_lease(request: web.Request) -> web.Response:
            nonlocal raced
            if raced:
                return await reply_lease(request)
            raced = True
            stub.high_sequence = 0  # Read before S existed.
            stub.add(MailboxCommand(1, fetched.command_id, 1, child_command=fetched))
            await until(lambda: 1 in stub.polls)  # The Mac recorded S and polls on.
            try:
                return await reply_lease(request)
            finally:
                stub.high_sequence = None

        stub.lease = racing_lease  # type: ignore[method-assign]
        async with serving(stub, tls[0]) as origin:
            mac = service(paths, origin, tls[1])
            child = ChildReference(mac.identity.executor_incarnation, str(uuid4()), "c_one")
            fetched = ChildCommand(
                "fetched_after_read", child, ChildOperation.stop, None, StopRun()
            )
            running = asyncio.create_task(mac.run())
            try:
                await until(lambda: fetched.command_id in stub.results and len(stub.leases) >= 3)
                await until(lambda: len(stub.polls) >= stub.polls.index(1) + 3)
                assert 0 not in stub.polls[stub.polls.index(1) :]
                proofs = [
                    item.envelope.submission_id
                    for batch in stub.items
                    for item in batch.items
                    if item.envelope.kind is EnvelopeKind.child_stopped
                ]
                assert len(proofs) == 1
            finally:
                await stopped(mac, running)

    asyncio.run(run())


def test_output_an_earlier_process_owed_is_queued_before_the_first_lease(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    paths: ExecutorPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Risk: the first lease reports a conversation as empty before the owed R1 of a
    command an earlier process finished is back in the outbox, so the VPS ends the old
    run's drain and refuses that item when it arrives."""

    async def run() -> None:
        stub = VpsStub()
        async with serving(stub, tls[0]) as origin:
            mac = service(paths, origin, tls[1])
            child = ChildReference(mac.identity.executor_incarnation, str(uuid4()), "c_one")
            command = prompt_command(child)
            mac.journal.record_poll_reply(
                MailboxPollReply(
                    (MailboxCommand(1, command.command_id, 1, child_command=command),)
                ),
                mac.identity.executor_incarnation,
            )
            refused = CommandOutcome(
                command.command_id, child, CommandDisposition.refused, {"reason": "test"}
            )
            mac.journal.finish(
                command.command_id,
                CommandResult(command.command_id, child_outcome=refused),
                outputs_owed=True,
            )
            publish = mac.runner.publish_owed_outputs

            async def slow_publish(*args: object) -> None:
                await asyncio.sleep(0.3)
                await publish(*args)  # type: ignore[arg-type]

            monkeypatch.setattr(mac.runner, "publish_owed_outputs", slow_publish)
            running = asyncio.create_task(mac.run())
            try:
                await until(lambda: bool(stub.leases))
                assert [w.conversation_id for w in stub.leases[0].waiting_conversations] == [
                    "c_one"
                ]
            finally:
                await stopped(mac, running)

    asyncio.run(run())


def test_lease_reports_the_backlog_and_update_work_never_holds_it(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    paths: ExecutorPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("planner.executor.mac_presence.LEASE_INTERVAL_SECONDS", 0.05)

    async def run() -> None:
        target = MacUpdateTarget(
            "request_one",
            "b" * 40,
            "now",
            False,
            "c" * 64,
            1,
            "/api/executor/update/request_one/archive",
        )
        stub = VpsStub(update=MacUpdateCheck(1, target))
        async with serving(stub, tls[0]) as origin:
            mac = service(paths, origin, tls[1])
            applying, release = asyncio.Event(), asyncio.Event()
            applied: list[MacUpdateCheck] = []

            async def apply(check: MacUpdateCheck) -> None:
                applied.append(check)
                applying.set()
                await release.wait()

            monkeypatch.setattr(mac.updates, "apply", apply)
            # Items the VPS has not receipted yet; sending is held so they keep waiting.
            monkeypatch.setattr(mac.outbox, "send_pending", lambda *a: asyncio.Event().wait())
            for conversation in ("c_one", "c_two"):
                mac.outbox.add(OutboxEntry(conversation, EnvelopeKind.cli_write, {}))
            running = asyncio.create_task(mac.run())
            try:
                await applying.wait()
                count = len(stub.leases)
                await until(lambda: len(stub.leases) >= count + 3)
                assert len(applied) == 1  # The lease loop never waited on the update.
                lease = stub.leases[-1]
                assert lease.queue_depth == 2 and lease.oldest_item_age_ms is not None
                assert [w.conversation_id for w in lease.waiting_conversations] == [
                    "c_one",
                    "c_two",
                ]
                assert not lease.waiting_conversations_truncated
                assert not lease.going_to_sleep and not lease.admission_stopped
                assert mac.presence.leased()
                release.set()
            finally:
                release.set()
                await stopped(mac, running)

    asyncio.run(run())


def test_transfers_send_described_bytes_and_inputs_retry_until_served_or_gone(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("planner.executor.mac_files.INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS", 0.01)

    async def run() -> None:
        stub = VpsStub(files={"stored_one": b"input bytes"}, file_failures=2)
        async with serving(stub, tls[0]) as origin:
            child = ChildReference(str(uuid4()), str(uuid4()), "c_one")
            client = MacHttpClient(
                origin,
                EXECUTOR_ID,
                ExecutorIdentity(
                    PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, child.executor_incarnation
                ),
                ssl_context=tls[1],
            )
            paths = ExecutorPaths(tmp_path / "executor")
            files = MacFiles(client, paths)
            try:
                source = tmp_path / "artifact.md"
                source.write_bytes(b"artifact bytes")
                manifest = FileManifest(
                    FileScope.ticket,
                    "t_one",
                    "notes/artifact.md",
                    hashlib.sha256(b"artifact bytes").hexdigest(),
                    len(b"artifact bytes"),
                )
                await files.upload(FileUploadRequest(manifest, child), source)
                assert stub.uploads == [(FileUploadRequest(manifest, child), b"artifact bytes")]
                stub.upload_status = 403
                with pytest.raises(UploadRefused, match="not yours"):
                    await files.upload(FileUploadRequest(manifest, child), source)

                await files.download_inputs(prompt_command(child, files=("stored_one",)))
                installed = tmp_path / "executor/artifacts/files/conversations/c_one/stored_one"
                assert installed.read_bytes() == b"input bytes"
                assert stub.file_failures == 0  # Two failures were retried.
                with pytest.raises(InputDownloadRefused):
                    await files.download_inputs(prompt_command(child, files=("withdrawn",)))
                assert not list(paths.run.glob(".input-*"))
            finally:
                await client.close()

    asyncio.run(run())


def test_slow_transfers_of_other_conversations_never_hold_up_a_conversations_files(
    tls: tuple[ssl.SSLContext, ssl.SSLContext],
    tmp_path: Path,
) -> None:
    """Risk: a few slow uploads and downloads take every shared slot or connection, so
    another conversation's artifact upload, and its whole outbox, or its prompt's input
    waits behind them."""

    async def run() -> None:
        stub = VpsStub(files={"slow_input": b"slow", "quick_input": b"quick"})
        async with serving(stub, tls[0]) as origin:
            incarnation = str(uuid4())
            client = MacHttpClient(
                origin,
                EXECUTOR_ID,
                ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, incarnation),
                ssl_context=tls[1],
            )
            files = MacFiles(client, ExecutorPaths(tmp_path / "executor"))
            source = tmp_path / "artifact.md"
            source.write_bytes(b"artifact bytes")
            digest = hashlib.sha256(b"artifact bytes").hexdigest()

            def upload(name: str, child: ChildReference) -> FileUploadRequest:
                manifest = FileManifest(FileScope.ticket, "t_one", name, digest, 14)
                return FileUploadRequest(manifest, child)

            others = [ChildReference(incarnation, str(uuid4()), f"c_{n}") for n in range(8)]
            slow = [
                asyncio.create_task(files.upload(upload(f"slow_{n}.md", child), source))
                for n, child in enumerate(others[:4])
            ] + [
                asyncio.create_task(
                    files.download_inputs(prompt_command(child, files=("slow_input",)))
                )
                for child in others[4:]
            ]
            try:
                await until(lambda: stub.holding == 8)
                quick = ChildReference(incarnation, str(uuid4()), "c_quick")
                await asyncio.wait_for(files.upload(upload("quick.md", quick), source), 5)
                await asyncio.wait_for(
                    files.download_inputs(prompt_command(quick, files=("quick_input",))), 5
                )
                assert not any(task.done() for task in slow)
                stub.slow_release.set()
                await asyncio.wait_for(asyncio.gather(*slow), 5)
            finally:
                stub.slow_release.set()
                await asyncio.gather(*slow, return_exceptions=True)
                await client.close()

    asyncio.run(run())


def test_live_events_keep_one_request_in_flight_and_drop_the_oldest_waiting() -> None:
    async def run() -> None:
        release = asyncio.Event()
        sent: list[LiveEventBatch] = []
        in_flight = 0

        class Live:
            async def post_live(self, conversation_id: str, batch: LiveEventBatch) -> None:
                nonlocal in_flight
                in_flight += 1
                assert in_flight == 1
                sent.append(batch)
                await release.wait()
                in_flight -= 1
                raise ConnectionError("dropped; never retried")

        live = MacLiveSender(cast(MacHttpClientApi, Live()))
        child = ChildReference(str(uuid4()), str(uuid4()), "c_one")

        def delta(number: int) -> BackendEvent:
            return BackendEvent(
                child,
                BackendEventMethod.agent_message_delta,
                TurnToken("c_one", 1),
                {"text": str(number)},
            )

        live.send(delta(0))
        await until(lambda: len(sent) == 1)
        for number in range(1, 100):
            live.send(delta(number))  # Never waits on the VPS.
        release.set()
        await until(lambda: len(sent) == 2 and not live._senders)
        assert [e.arguments["text"] for e in sent[0].events] == ["0"]
        assert [e.arguments["text"] for e in sent[1].events] == [str(n) for n in range(36, 100)]
        await live.close()

    asyncio.run(run())
