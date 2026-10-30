"""Closed categories and the process-lifetime diagnostic output budget."""

import ssl
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from planner.executor.contracts import (
    ConnectionFailureCategory,
)
from planner.executor.diagnostics import ConnectionDiagnostics, classify_connection_failure


def forbidden_handshake() -> aiohttp.WSServerHandshakeError:
    url = URL("https://fixture.invalid/api/executor/connect")
    request = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    return aiohttp.WSServerHandshakeError(request, (), status=403)


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (aiohttp.InvalidURL("secret"), ConnectionFailureCategory.configuration),
        (ssl.SSLError("secret"), ConnectionFailureCategory.tls),
        (
            forbidden_handshake(),
            ConnectionFailureCategory.authentication,
        ),
        (TimeoutError("secret"), ConnectionFailureCategory.timeout),
        (OSError("secret"), ConnectionFailureCategory.network),
        (ValueError("secret"), ConnectionFailureCategory.connection_or_protocol),
    ],
)
def test_fixed_categories(error: Exception, category: ConnectionFailureCategory) -> None:
    assert classify_connection_failure(error) is category


def test_six_fixed_lines_for_arbitrary_repetitions() -> None:
    lines: list[str] = []
    diagnostics = ConnectionDiagnostics(lines.append)
    errors = [
        aiohttp.InvalidURL("private path"),
        ssl.SSLError("private host"),
        forbidden_handshake(),
        TimeoutError("secret command"),
        OSError("secret credential"),
        ValueError("remote output"),
    ]
    for _ in range(100):
        for error in errors:
            diagnostics.report(error)
    assert lines == [
        f"executor connection failure: {category.value}" for category in ConnectionFailureCategory
    ]


def test_retry_boundary_keeps_wait_and_propagates_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from collections.abc import Awaitable, Coroutine
    from typing import cast
    from unittest.mock import AsyncMock, MagicMock

    from planner.executor import mac
    from planner.executor.configuration import RECONNECT_INTERVAL_SECONDS, ExecutorPaths

    paths = ExecutorPaths(tmp_path)
    executor = mac.MacExecutor.__new__(mac.MacExecutor)
    executor.paths = paths
    executor._stop = asyncio.Event()
    executor.spool = MagicMock()
    executor.registry = MagicMock()
    executor._publications = {}
    executor._ipc_lookups = set()
    errors: list[BaseException] = [ValueError("secret") for _ in range(10)]
    errors.append(asyncio.CancelledError())
    lines: list[str] = []
    waits: list[float] = []
    monkeypatch.setattr(mac, "connection_diagnostics", ConnectionDiagnostics(lines.append))

    async def connected() -> None:
        raise errors.pop(0)

    async def wait(awaitable: Awaitable[object], timeout: float) -> object:
        waits.append(timeout)
        assert isinstance(awaitable, Coroutine)
        awaitable.close()
        raise TimeoutError

    server = MagicMock(spec=asyncio.AbstractServer)
    server.__aenter__ = AsyncMock(return_value=server)
    server.__aexit__ = AsyncMock(return_value=None)

    async def start_server(callback: object, *, path: Path) -> asyncio.AbstractServer:
        path.touch()
        return cast(asyncio.AbstractServer, server)

    monkeypatch.setattr(executor, "_connected", connected)
    monkeypatch.setattr(asyncio, "wait_for", wait)
    monkeypatch.setattr(asyncio, "start_unix_server", start_server)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(executor.run())
    assert waits == [RECONNECT_INTERVAL_SECONDS] * 10
    assert lines == ["executor connection failure: connection_or_protocol"]
    assert not paths.ipc_socket.exists()
    executor.spool.close.assert_called_once_with()


def test_context_is_finite_and_never_exposes_custom_names_or_messages() -> None:
    from planner.executor.contracts import ConnectionFailureOrigin, ConnectionPhase

    class SecretCredentialName(ValueError):
        pass

    lines: list[str] = []
    diagnostics = ConnectionDiagnostics(lines.append)
    diagnostics.report(ValueError("secret frame"))
    for _ in range(100):
        diagnostics.report(
            SecretCredentialName("secret credential"),
            phase=ConnectionPhase.transport_open,
            origin=ConnectionFailureOrigin.session,
        )
    assert lines == [
        "executor connection failure: connection_or_protocol",
        "executor connection failure: connection_or_protocol phase=transport_open"
        " origin=session exception=other",
    ]
    for error in (
        aiohttp.InvalidURL("secret"),
        ssl.SSLError("secret"),
        ssl.SSLError("secret"),
        forbidden_handshake(),
        TimeoutError("secret"),
        OSError("secret"),
    ):
        diagnostics.report(error)
    assert len(lines) == 7
    assert "secret" not in " ".join(lines).lower()


@pytest.mark.parametrize(
    "error,tag",
    [
        (aiohttp.InvalidURL("secret"), "InvalidURL"),
        (aiohttp.ClientConnectionError("secret"), "ClientConnectionError"),
        (aiohttp.ClientPayloadError("secret"), "ClientPayloadError"),
        (aiohttp.ServerDisconnectedError("secret"), "ServerDisconnectedError"),
        (ssl.SSLError("secret"), "SSLError"),
        (ValueError("secret"), "ValueError"),
        (KeyError("secret"), "KeyError"),
        (TypeError("secret"), "TypeError"),
        (OSError("secret"), "OSError"),
        (ConnectionError("secret"), "ConnectionError"),
    ],
)
def test_exception_tags_are_allowlisted(error: Exception, tag: str) -> None:
    from planner.executor.diagnostics import connection_exception_tag

    assert connection_exception_tag(error) == tag


@pytest.mark.parametrize(
    "failed_phase",
    [
        "app_connect",
        "hello",
        "transport_open",
        "reconcile1",
        "replay1",
        "reconcile2",
        "replay2",
        "ready_ack",
        "steady_state",
    ],
)
def test_connection_reports_precise_session_phase_before_cleanup(
    failed_phase: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from planner.executor import mac
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.contracts import (
        ConnectionState,
        WireMessage,
        WireMessageKind,
    )

    async def run() -> None:
        executor = mac.MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        failure = ValueError("secret key and frame bytes")
        lines: list[str] = []
        monkeypatch.setattr(mac, "connection_diagnostics", ConnectionDiagnostics(lines.append))
        closed: list[bool] = []

        def fail(phase: str) -> None:
            if failed_phase == phase:
                raise failure

        @asynccontextmanager
        async def websocket(*args: object) -> AsyncIterator[object]:
            fail("app_connect")
            try:
                yield SimpleNamespace(
                    closed=False,
                    receive_bytes=AsyncMock(side_effect=asyncio.CancelledError),
                    send_bytes=AsyncMock(),
                    aclose=AsyncMock(),
                )
            finally:
                closed.append(True)

        client = SimpleNamespace(websocket=websocket, close=AsyncMock())

        async def request(kind: WireMessageKind, payload: object) -> WireMessage:
            if kind is WireMessageKind.hello:
                fail("hello")
                return WireMessage(
                    WireMessageKind.hello_accepted, "reply", mac.as_object(executor.identity)
                )
            if kind is WireMessageKind.transport_open:
                fail("transport_open")
                return WireMessage(
                    WireMessageKind.transport_opened,
                    "reply",
                    {},
                )
            fail("ready_ack")
            return WireMessage(kind, "reply", {"ready": True})

        reconciles = 0
        replays = 0

        async def reconcile() -> None:
            nonlocal reconciles
            reconciles += 1
            fail(f"reconcile{reconciles}")

        async def replay() -> None:
            nonlocal replays
            replays += 1
            fail(f"replay{replays}" if replays <= 2 else "steady_state")

        monkeypatch.setattr(mac, "ExecutorAppClient", Mock(return_value=client))

        # Hold the reader open so only the selected session phase fails.
        async def read_forever(reader: object) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(executor, "_read_loop", read_forever)
        monkeypatch.setattr(executor, "_request", request)
        monkeypatch.setattr(executor, "_reconcile", reconcile)
        monkeypatch.setattr(executor, "_replay", replay)
        try:
            with pytest.raises(ValueError) as caught:
                await executor._connected()
            assert caught.value is failure
            assert lines == [
                "executor connection failure: connection_or_protocol"
                f" phase={failed_phase} origin=session exception=ValueError"
            ]
            assert executor.state is ConnectionState.disconnected
            assert not executor._handshake_accepted
            assert executor._writer is None
            assert bool(closed) is (failed_phase != "app_connect")
        finally:
            executor.spool.close()

    asyncio.run(run())


def test_reader_failure_is_reported_before_session_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from planner.executor import mac
    from planner.executor.configuration import ExecutorPaths

    async def run() -> None:
        executor = mac.MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            executor_id="owner-mac",
            panels_server_url="https://unused",
        )
        from types import SimpleNamespace

        lines: list[str] = []
        monkeypatch.setattr(mac, "connection_diagnostics", ConnectionDiagnostics(lines.append))
        failure = asyncio.IncompleteReadError(b"secret credentials", 10)
        from collections.abc import AsyncIterator
        from contextlib import asynccontextmanager
        from unittest.mock import Mock

        @asynccontextmanager
        async def websocket(*args: object) -> AsyncIterator[object]:
            yield SimpleNamespace(closed=False, send_bytes=AsyncMock(), aclose=AsyncMock())

        client = SimpleNamespace(websocket=websocket, close=AsyncMock())
        monkeypatch.setattr(mac, "ExecutorAppClient", Mock(return_value=client))

        async def read(reader: object) -> None:
            raise failure

        async def request(*args: object) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(executor, "_read_loop", read)
        monkeypatch.setattr(executor, "_request", request)
        try:
            with pytest.raises(asyncio.IncompleteReadError) as caught:
                await executor._connected()
            assert caught.value is failure
            assert lines == [
                "executor connection failure: connection_or_protocol phase=hello"
                " origin=reader exception=IncompleteReadError"
            ]
        finally:
            executor.spool.close()

    asyncio.run(run())
