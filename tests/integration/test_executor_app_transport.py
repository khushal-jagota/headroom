"""Native HTTP/WS boundaries through the isolated trusted TLS proxy."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from starlette.responses import StreamingResponse
from tests.integration.test_executor_end_to_end import boot, eventually

from planner.executor import app_transport
from planner.executor.app_transport import ExecutorAppClient
from planner.executor.configuration import MAX_ENVELOPE_BYTES
from planner.executor.contracts import FileManifest, FileScope, FileSnapshot
from planner.executor.wire import encode_frame


def test_native_admission_and_generation_file_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            identity = harness.endpoint.identity
            assert identity is not None
            client = ExecutorAppClient(
                harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
            )
            # The production session never trusts the fixture certificate.
            production_client = ExecutorAppClient(harness.proxy.origin, "test-executor", identity)
            try:
                with pytest.raises(aiohttp.ClientConnectorCertificateError):
                    async with production_client.websocket(
                        "/api/executor/connect", MAX_ENVELOPE_BYTES + 4
                    ):
                        pytest.fail("untrusted TLS certificate was accepted")
            finally:
                await production_client.close()
            service = harness.endpoint._transports[identity]
            data = bytes(range(256)) * 1000
            manifest = FileManifest(
                "publication",
                FileScope.ticket,
                "ticket",
                "binary.dat",
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
            slot = service.create_upload(manifest)
            url = harness.proxy.origin + "/api/executor/files/" + slot.transfer_id
            try:
                for headers in (
                    {},
                    client.headers() | {"X-Panels-Executor-ID": "other"},
                    client.headers() | {"Origin": harness.proxy.origin},
                ):
                    async with client.session.put(url, headers=headers, data=data) as response:
                        assert response.status == 403
                duplicate = list(client.headers().items()) + [
                    ("X-Panels-Executor-ID", "test-executor")
                ]
                async with client.session.put(url, headers=duplicate, data=data) as response:
                    assert response.status == 403
                stale = client.headers() | {"X-Panels-Executor-Generation": "999"}
                async with client.session.put(url, headers=stale, data=data) as response:
                    assert response.status == 409
                encoded = client.headers() | {"Content-Encoding": "gzip"}
                async with client.session.put(
                    url, headers=encoded, data=gzip.compress(data)
                ) as response:
                    assert response.status == 409
                async with client.session.get(url, headers=client.headers()) as response:
                    assert response.status == 409
                async with client.session.put(url, headers=client.headers(), data=data) as response:
                    assert response.status == 204
                assert service.upload_path(slot.transfer_id).read_bytes() == data
                async with client.session.put(url, headers=client.headers(), data=data) as response:
                    assert response.status == 409
                service.release(slot.transfer_id)
                for body in (data[:-1], data + b"extra", b"x" * len(data)):
                    bad = service.create_upload(manifest)
                    async with client.session.put(
                        harness.proxy.origin + "/api/executor/files/" + bad.transfer_id,
                        headers=client.headers(),
                        data=body,
                    ) as response:
                        assert response.status == 409
                    assert bad.transfer_id not in service.transfers
                source = tmp_path / "input"
                source.write_bytes(data)
                download = await service.create_download_async(FileSnapshot(manifest, source))
                download_url = harness.proxy.origin + "/api/executor/files/" + download.transfer_id
                destination = tmp_path / "download"
                await client.download(download, destination)
                assert destination.read_bytes() == data
                async with client.session.get(download_url, headers=client.headers()) as response:
                    assert response.status == 409
                encoded_download = await service.create_download_async(
                    FileSnapshot(manifest, source)
                )
                encoded_destination = tmp_path / "encoded-download"

                def encoded_response(
                    chunks: AsyncIterator[bytes], *, media_type: str
                ) -> StreamingResponse:
                    # Label raw bytes deliberately: decompression stays disabled,
                    # and the downloader must refuse the header before body acceptance.
                    return StreamingResponse(
                        chunks, media_type=media_type, headers={"Content-Encoding": "gzip"}
                    )

                with monkeypatch.context() as response_patch:
                    response_patch.setattr(app_transport, "StreamingResponse", encoded_response)
                    with pytest.raises(ConnectionError, match="executor download refused"):
                        await client.download(encoded_download, encoded_destination)
                assert encoded_destination.read_bytes() == b""
                async with client.session.get(
                    harness.proxy.origin + "/api/executor/files/" + "f" * 32,
                    headers=client.headers(),
                ) as response:
                    assert response.status == 409
            finally:
                await client.close()
        finally:
            await harness.close()

    asyncio.run(run())


def test_stalled_canceled_upload_keeps_control_live_and_revokes_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            identity = harness.endpoint.identity
            assert identity is not None
            client = ExecutorAppClient(
                harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
            )
            service = harness.endpoint._transports[identity]
            data = b"x" * 1000000
            manifest = FileManifest(
                "publication",
                FileScope.ticket,
                "ticket",
                "binary.dat",
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
            slot = service.create_upload(manifest)
            prefix_sent = asyncio.Event()

            async def body() -> AsyncIterator[bytes]:
                yield data[:100]
                prefix_sent.set()
                await asyncio.Event().wait()

            async def put() -> None:
                async with client.session.put(
                    harness.proxy.origin + "/api/executor/files/" + slot.transfer_id,
                    headers=client.headers(),
                    data=body(),
                ) as response:
                    await response.read()

            task = asyncio.create_task(put())
            try:
                await asyncio.wait_for(prefix_sent.wait(), 2)
                await eventually(lambda: bool(service.active), "upload did not reach native app")
                generation = harness.mac.identity.connection_generation
                # Native health request traverses control while file body stalls.
                from planner.executor.contracts import WireMessageKind

                response = await asyncio.wait_for(
                    harness.endpoint._request(
                        WireMessageKind.health, {"clock_probe": {"vps_sent_at_ms": 1}}
                    ),
                    2,
                )
                assert response.kind is WireMessageKind.health
                assert harness.mac.identity.connection_generation == generation
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await eventually(
                    lambda: slot.transfer_id not in service.transfers,
                    "canceled HTTP upload retained its reservation",
                )
                assert not (service.root / slot.transfer_id).exists()
                assert harness.endpoint.ready
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.close()
        finally:
            await harness.close()

    asyncio.run(run())


@pytest.mark.parametrize("malformed", ["text", "trailing", "empty", "oversized"])
def test_malformed_control_cannot_fence_current_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed: str
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        try:
            identity = harness.endpoint.identity
            assert identity is not None
            async with harness.proxy.session_factory() as session:
                async with session.ws_connect(
                    "wss" + harness.proxy.origin[5:] + "/api/executor/connect",
                    headers={"X-Panels-Executor-ID": "test-executor"},
                    compress=0,
                ) as socket:
                    if malformed == "text":
                        await socket.send_str("hello")
                    elif malformed == "trailing":
                        await socket.send_bytes(encode_frame({"kind": "hello"}) + b"extra")
                    elif malformed == "empty":
                        await socket.send_bytes(b"\0\0\0\0")
                    else:
                        await socket.send_bytes(b"x" * (MAX_ENVELOPE_BYTES + 5))
                    reply = await asyncio.wait_for(socket.receive(), 2)
                    assert reply.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
            assert harness.endpoint.identity == identity and harness.endpoint.ready
        finally:
            await harness.close()

    asyncio.run(run())


def test_duplicate_hello_preserves_active_file_and_preview_reservations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor.codec import encode_value
    from planner.executor.contracts import WireMessage, WireMessageKind

    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)

        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                writer.write(await reader.read(16))
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        listener = await asyncio.start_server(echo, "127.0.0.1", 0)
        try:
            identity = harness.endpoint.identity
            assert identity is not None
            writer = harness.endpoint.writer
            service = harness.endpoint._transports[identity]
            manifest = FileManifest(
                "publication",
                FileScope.ticket,
                "ticket",
                "empty",
                hashlib.sha256(b"").hexdigest(),
                0,
            )
            slot = service.create_upload(manifest)
            port = int(listener.sockets[0].getsockname()[1])
            async with harness.endpoint.preview_forward(port, "t_scripted") as local:
                forwards = harness.endpoint._preview_forwards
                assert forwards is not None
                lease = next(iter(forwards.leases.values()))
                payload = encode_value(harness.mac.health())
                assert isinstance(payload, dict)
                async with harness.proxy.session_factory() as session:
                    async with session.ws_connect(
                        "wss" + harness.proxy.origin[5:] + "/api/executor/connect",
                        headers={"X-Panels-Executor-ID": "test-executor"},
                    ) as socket:
                        await socket.send_bytes(
                            encode_frame(
                                WireMessage(
                                    WireMessageKind.hello,
                                    "duplicate",
                                    payload,
                                )
                            )
                        )
                        reply = await asyncio.wait_for(socket.receive(), 2)
                        assert reply.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
                assert harness.endpoint.ready and harness.endpoint.identity == identity
                assert harness.endpoint.writer is writer
                assert harness.endpoint._transports[identity] is service and service.valid()
                assert slot.transfer_id in service.transfers
                assert harness.endpoint._preview_forwards is forwards and not forwards.closed
                assert lease.server.is_serving() and local > 0
                native_client = ExecutorAppClient(
                    harness.proxy.origin, "test-executor", identity, harness.proxy.session_factory
                )
                try:
                    source = tmp_path / "empty-source"
                    source.write_bytes(b"")
                    await native_client.upload(slot, source)
                    assert service.upload_path(slot.transfer_id).read_bytes() == b""
                finally:
                    await native_client.close()
                reader, stream_writer = await asyncio.open_connection("127.0.0.1", local)
                try:
                    stream_writer.write(b"still usable")
                    await stream_writer.drain()
                    assert await asyncio.wait_for(reader.readexactly(12), 2) == b"still usable"
                finally:
                    stream_writer.close()
                    await stream_writer.wait_closed()
        finally:
            listener.close()
            await listener.wait_closed()
            await harness.close()

    asyncio.run(run())
