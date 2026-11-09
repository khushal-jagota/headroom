"""The executor routes over real HTTPS, through the isolated trusted TLS proxy.

Risk: a route that works when called in process but not over the wire: the ingress
gate (login, executor ID, Origin, duplicated security headers) admits a request it
should refuse; the work-route fence answers the wrong status or body; a status, body
shape or header the Mac reads (lease 200/409, poll hold, 413, 204, the upload header,
the download's Content-Length and digest) differs once uvicorn and the proxy are in
between; response compression is applied to an input download; or the old WebSocket is
accepted instead of refused and recorded.
Gap: the typed adapters are proved without HTTP in test_executor_command_mailbox.py
and the inbox tests; nothing else crosses uvicorn and TLS with raw requests.
Layer: integration. One real uvicorn behind the isolated TLS proxy
(tests/support/executor_https.serve_executor_app), the real VpsExecutor over a real
SQLite database, raw aiohttp requests. No Mac and no conversation core run: the core's
side of MacRunConversations is a recording fake, and model-list refreshes are off.
Setup reused: the transport app built as test_executor_end_to_end.boot builds it, and
tests/support/executor_presence for identities and leases. The whole file boots the
server once, because the boot (uvicorn, openssl and the proxy process) is its cost.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
from fastapi import FastAPI, Request
from tests.support.executor_https import serve_executor_app
from tests.support.executor_presence import lease_request, mac_identity

from planner.conversation.backends.contracts import BackendChild, TurnToken
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.message_content import MessageFile, text_message_content
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.core.response_compression import (
    CompressExceptMarkedRoutes,
    uncompressed_route_patterns,
)
from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import FileUpdateAttemptStore
from planner.execution_configuration import SPOOL_FORMAT_VERSION
from planner.execution_identity import MAC_NEEDS_UPDATE, ChildReference, ExecutorIdentity
from planner.executor.app_transport import build_executor_router
from planner.executor.configuration import MAX_ITEM_BATCH_BYTES
from planner.executor.protocol import (
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
    ItemSubmission,
    LeaseRefused,
    LeaseReply,
    LiveEventBatch,
    LiveReadRequest,
    MailboxPollReply,
    QueuedEnvelope,
    ReceiptDisposition,
    as_object,
    decode_bounded,
    encode_bounded,
)
from planner.executor.update_contracts import MacUpdateCheck
from planner.executor.vps_transport import VpsExecutor

EXECUTOR_ID = "test-executor"
SETTLES = 5.0


class _Core:
    """The conversation core's side of MacRunConversations; nothing here reaches it."""

    async def emit_answer(
        self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, object]]
    ) -> None:
        await emit()

    async def retire_replaced_remote_child(
        self, reference: ChildReference, *, replaced_by: str
    ) -> None:
        raise AssertionError("no process is replaced here")

    async def confirm_remote_child_termination(self, reference: ChildReference) -> None:
        raise AssertionError("no child is retired here")

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: BackendChild
    ) -> None:
        raise AssertionError("no cancel fails here")


def _transport_app(endpoint: VpsExecutor, updates: MacUpdateService) -> FastAPI:
    """As test_executor_end_to_end.boot builds it, plus the update service."""
    app = FastAPI()
    app.state.executor_id = EXECUTOR_ID
    app.state.executor_endpoint = endpoint
    app.state.mac_updates = updates
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
    return app


def _reads_app() -> FastAPI:
    """What an allowlisted read reaches: it answers with the principal it was asked as."""
    app = FastAPI()

    @app.get("/api/tickets")
    async def tickets(request: Request) -> dict[str, object]:
        return {
            "actor": request.headers.get("X-Plan-Actor"),
            "ticket": request.headers.get("X-Plan-Ticket-ID"),
            "status": request.query_params.get("status"),
        }

    return app


def _error_code(body: bytes) -> str:
    error = json.loads(body)["error"]
    assert set(error) == {"code", "message", "detail"}
    return str(error["code"])


def test_every_executor_route_over_real_https(tmp_path: Path) -> None:
    db_path = str(tmp_path / "vps" / "panels.sqlite")
    Path(db_path).parent.mkdir()
    connection = connect(db_path)
    create_schema(connection)
    connection.close()
    endpoint = VpsExecutor(db_path, RealClock())
    endpoint.runs.conversations = _Core()
    # Model-list refreshes would add mailbox rows; the poll below counts them.
    endpoint.presence.returned = None
    endpoint.app = _reads_app()
    updates = MacUpdateService(
        tmp_path / "mac-updates",
        FileUpdateAttemptStore(tmp_path / "update-attempt.json"),
        None,
        "a" * 40,
        lambda: None,
    )
    identity = mac_identity()
    child = ChildReference(identity.executor_incarnation, "child", "c")

    async def exercise() -> None:
        try:
            async with serve_executor_app(
                _transport_app(endpoint, updates), tmp_path / "tls"
            ) as proxy:
                origin = proxy.origin
                only_id = {"X-Panels-Executor-ID": EXECUTOR_ID}
                work = only_id | {
                    "X-Panels-Executor-Incarnation": identity.executor_incarnation,
                    "X-Panels-Executor-Protocol": "22",
                }
                async with proxy.session_factory() as session:

                    async def call(
                        method: str, path: str, headers: Any = None, data: Any = None
                    ) -> tuple[int, bytes, Any]:
                        async with session.request(
                            method,
                            origin + path,
                            headers=work if headers is None else headers,
                            data=data,
                        ) as response:
                            return response.status, await response.read(), response.headers

                    # Ingress: the trusted login alone is not enough.
                    lease_body = encode_bounded(lease_request(identity))
                    for headers in (
                        {},
                        {"X-Panels-Executor-ID": "another-executor"},
                        only_id | {"Origin": origin},
                        [*only_id.items(), *only_id.items()],
                    ):
                        status, _, _ = await call(
                            "POST", "/api/executor/lease", headers, lease_body
                        )
                        assert status == 403, headers
                    assert endpoint.health().presence == "never"

                    # A client that verifies certificates by default, as the Mac's sessions
                    # do without an explicit context, never trusts the fixture certificate.
                    async with aiohttp.ClientSession(trust_env=False) as default_client:
                        try:
                            async with default_client.get(origin + "/api/executor/update"):
                                raise AssertionError("the fixture certificate was trusted")
                        except aiohttp.ClientConnectorCertificateError:
                            pass

                    # Route 10: the old WebSocket closes before accept (403), and the
                    # refusal is kept, so the Mac page says it needs an update.
                    try:
                        async with session.ws_connect(
                            "wss" + origin[5:] + "/api/executor/connect", headers=only_id
                        ):
                            raise AssertionError("the old WebSocket was accepted")
                    except aiohttp.WSServerHandshakeError as refused:
                        assert refused.status == 403
                    health = endpoint.health()
                    assert health.presence == "refused"
                    assert health.refusal is not None and health.refusal.reason == MAC_NEEDS_UPDATE
                    assert health.refusal.peer_identity is None

                    # Route 9 serves a protocol-21 Mac with only the ID header, uncompressed.
                    status, body, headers = await call(
                        "GET", "/api/executor/update", only_id | {"Accept-Encoding": "gzip"}
                    )
                    assert status == 200 and "Content-Encoding" not in headers
                    assert decode_bounded(body, MacUpdateCheck).target is None

                    # Route 1: an old protocol is refused with its LeaseRefused body.
                    old = ExecutorIdentity(21, "a" * 40, SPOOL_FORMAT_VERSION, str(uuid4()))
                    status, body, _ = await call(
                        "POST", "/api/executor/lease", only_id, encode_bounded(lease_request(old))
                    )
                    assert status == 409
                    lease_refused = decode_bounded(body, LeaseRefused)
                    assert lease_refused.error["code"] == "executor_version_refused"
                    assert lease_refused.refusal is not None
                    assert lease_refused.refusal.reason == MAC_NEEDS_UPDATE
                    assert lease_refused.refusal.peer_identity == old
                    # The current process's lease is accepted and clears the refusal.
                    status, body, _ = await call("POST", "/api/executor/lease", only_id, lease_body)
                    assert status == 200
                    lease_reply = decode_bounded(body, LeaseReply)
                    assert lease_reply.mailbox_high_sequence == 0
                    assert lease_reply.update.target is None
                    assert endpoint.health().presence == "present"
                    await asyncio.to_thread(
                        endpoint.runs.store.register_child, child, "ticket", "ticket-1", 1
                    )
                    proxy_child = endpoint.runs.recover(child)
                    assert proxy_child is not None

                    # The work-route fence, from memory, with the error body.
                    for headers, code in (
                        (
                            work | {"X-Panels-Executor-Incarnation": str(uuid4())},
                            "executor_incarnation_not_current",
                        ),
                        (only_id, "executor_version_refused"),
                        (work | {"X-Panels-Executor-Protocol": "21"}, "executor_version_refused"),
                    ):
                        status, body, _ = await call(
                            "GET", "/api/executor/commands?after=0&wait=0", headers
                        )
                        assert (status, _error_code(body)) == (409, code)

                    # Route 2: a held poll answers as soon as a command is put.
                    data = bytes(range(256)) * 64
                    kept = await endpoint.message_files.keep(
                        "c", data, media_type="application/octet-stream"
                    )
                    content = (
                        *text_message_content("see attached"),
                        MessageFile(kept.stored_file_id, kept.media_type, "input.bin", len(data)),
                    )
                    held = asyncio.create_task(
                        call("GET", "/api/executor/commands?after=0&wait=30")
                    )
                    await asyncio.sleep(0.3)
                    assert not held.done()
                    put_at = time.monotonic()
                    await proxy_child.write_prompt(
                        TurnToken("c", 1),
                        content,
                        sender_label="owner",
                        sender_content=content,
                        mode=PromptDeliveryMode.queue,
                        model_change=None,
                        reasoning_effort_change=None,
                    )
                    status, body, _ = await asyncio.wait_for(held, SETTLES)
                    assert status == 200 and time.monotonic() - put_at < SETTLES
                    (command,) = decode_bounded(body, MailboxPollReply).commands
                    assert command.child_command is not None
                    assert command.child_command.operation.value == "write_prompt"
                    # Nothing more: the hold ends at its wait, answering nothing.
                    started = time.monotonic()
                    status, body, _ = await call(
                        "GET", f"/api/executor/commands?after={command.command_sequence}&wait=0.3"
                    )
                    assert status == 200 and time.monotonic() - started >= 0.3
                    assert decode_bounded(body, MailboxPollReply).commands == ()

                    # Route 6: the command's own file, with its length and digest, never
                    # compressed; a file the command does not carry is not found.
                    file_path = f"/api/executor/commands/{command.command_id}/files/"
                    status, body, headers = await call(
                        "GET",
                        file_path + kept.stored_file_id,
                        work | {"Accept-Encoding": "gzip, deflate"},
                    )
                    assert status == 200 and body == data
                    assert "Content-Encoding" not in headers
                    assert headers["Content-Length"] == str(len(data))
                    assert headers["X-Panels-File-Sha256"] == hashlib.sha256(data).hexdigest()
                    status, body, _ = await call("GET", file_path + "sf_not_in_this_command")
                    assert (status, _error_code(body)) == (404, "not_found")

                    # Route 3: recorded and rejected, one by one, in one 200.
                    outcome = CommandOutcome(
                        command.command_id, child, CommandDisposition.succeeded, {"kind": "prompt"}
                    )
                    report = CommandOutcomeReport(
                        (
                            CommandResult(command.command_id, child_outcome=outcome),
                            CommandResult("never-put", child_outcome=None),
                        )
                    )
                    status, body, _ = await call(
                        "POST", "/api/executor/commands/outcomes", data=encode_bounded(report)
                    )
                    assert status == 200
                    outcomes = decode_bounded(body, CommandOutcomeReply)
                    assert outcomes.recorded == (command.command_id,)
                    assert [(item.command_id, item.reason) for item in outcomes.rejected] == [
                        ("never-put", "unknown_command")
                    ]

                    # Route 4: an item's receipt in an ItemBatchReply; a malformed or an
                    # oversized body is refused whole.
                    envelope = QueuedEnvelope(
                        "mac-sender",
                        "c",
                        1,
                        "submission-1",
                        EnvelopeKind.backend_event,
                        {"child": as_object(child)},
                    )
                    batch = ItemBatch(
                        "mac-sender", (ItemSubmission(envelope, sender_refusal="upload refused"),)
                    )
                    items_path = "/api/executor/conversations/c/items"
                    status, body, _ = await call(
                        "POST", items_path, data=encode_bounded(batch, limit=MAX_ITEM_BATCH_BYTES)
                    )
                    assert status == 200
                    items = decode_bounded(body, ItemBatchReply, limit=MAX_ITEM_BATCH_BYTES)
                    assert items.stop is None
                    assert [
                        (receipt.submission_id, receipt.disposition) for receipt in items.receipts
                    ] == [("submission-1", ReceiptDisposition.refused)]
                    status, body, _ = await call("POST", items_path, data=b'{"sender_id":')
                    assert (status, _error_code(body)) == (400, "validation")
                    status, _, _ = await call(
                        "POST", items_path, data=b" " * (MAX_ITEM_BATCH_BYTES + 1)
                    )
                    assert status == 413

                    # Route 5: bytes into private staging, named by digest and described by
                    # the base64url header; the served tree is untouched until an item names
                    # them.
                    upload_data = b"artifact bytes " * 100
                    digest = hashlib.sha256(upload_data).hexdigest()

                    def upload_headers(owner: str) -> dict[str, str]:
                        manifest = FileManifest(
                            FileScope.ticket, owner, "out/a.bin", digest, len(upload_data)
                        )
                        described = encode_bounded(FileUploadRequest(manifest, child))
                        return work | {
                            "X-Panels-File-Upload": base64.urlsafe_b64encode(described)
                            .rstrip(b"=")
                            .decode()
                        }

                    upload_path = f"/api/executor/uploads/{digest}"
                    status, _, _ = await call(
                        "PUT", upload_path, upload_headers("ticket-1"), upload_data
                    )
                    assert status == 204
                    manifest = FileManifest(
                        FileScope.ticket, "ticket-1", "out/a.bin", digest, len(upload_data)
                    )
                    store = endpoint.inbox.store
                    assert store._staged_file(manifest).read_bytes() == upload_data
                    assert not store.file_target(manifest).exists()
                    for headers, body_bytes, expected in (
                        (upload_headers("ticket-1"), upload_data[:-1], (400, "match its manifest")),
                        (upload_headers("ticket-1"), b"x" * len(upload_data), (400, "corrupt")),
                        (
                            # Real gzip: the test proxy's aiohttp server decodes request
                            # bodies, so anything else fails there before the route.
                            upload_headers("ticket-1") | {"Content-Encoding": "gzip"},
                            gzip.compress(upload_data),
                            (400, "raw bytes"),
                        ),
                        (upload_headers("ticket-2"), upload_data, (403, "another Ticket")),
                    ):
                        status, body, _ = await call("PUT", upload_path, headers, body_bytes)
                        _error_code(body)
                        assert status == expected[0]
                        assert expected[1] in json.loads(body)["error"]["message"]

                    # Route 7: an allowlisted read, as the child's own principal.
                    read = LiveReadRequest(child, "/api/tickets", {"status": "open"})
                    status, body, _ = await call(
                        "POST", "/api/executor/reads", data=encode_bounded(read)
                    )
                    assert status == 200
                    answered = json.loads(body)
                    assert answered["ticket"] == "ticket-1" and answered["status"] == "open"
                    assert answered["actor"]
                    outside = LiveReadRequest(child, "/api/admin", {})
                    status, body, _ = await call(
                        "POST", "/api/executor/reads", data=encode_bounded(outside)
                    )
                    assert (status, _error_code(body)) == (400, "validation")

                    # Route 8: accepted even when nobody listens.
                    status, _, _ = await call(
                        "POST",
                        "/api/executor/conversations/c/live",
                        data=encode_bounded(LiveEventBatch(())),
                    )
                    assert status == 204
        finally:
            await endpoint.close()

    asyncio.run(asyncio.wait_for(exercise(), 60))
