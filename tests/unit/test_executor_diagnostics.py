"""Closed categories and the process-lifetime diagnostic output budget."""

from collections.abc import AsyncIterator
from pathlib import Path

import asyncssh
import pytest

from planner.executor.contracts import (
    ConnectionFailureCategory,
    SshConfigurationError,
    TailnetValidationError,
)
from planner.executor.diagnostics import ConnectionDiagnostics, classify_connection_failure


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (SshConfigurationError("secret"), ConnectionFailureCategory.configuration),
        (TailnetValidationError("secret"), ConnectionFailureCategory.tailnet_validation),
        (asyncssh.HostKeyNotVerifiable("secret"), ConnectionFailureCategory.host_key),
        (asyncssh.PermissionDenied("secret"), ConnectionFailureCategory.authentication),
        (TimeoutError("secret"), ConnectionFailureCategory.timeout),
        (OSError("secret"), ConnectionFailureCategory.network),
        (ValueError("secret"), ConnectionFailureCategory.connection_or_protocol),
    ],
)
def test_fixed_categories(error: Exception, category: ConnectionFailureCategory) -> None:
    assert classify_connection_failure(error) is category


def test_seven_fixed_lines_for_arbitrary_repetitions() -> None:
    lines: list[str] = []
    diagnostics = ConnectionDiagnostics(lines.append)
    errors = [
        SshConfigurationError("private path"),
        TailnetValidationError("private json"),
        asyncssh.HostKeyNotVerifiable("private host"),
        asyncssh.PermissionDenied("private user"),
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
            phase=ConnectionPhase.sftp_open,
            origin=ConnectionFailureOrigin.session,
        )
    assert lines == [
        "executor connection failure: connection_or_protocol",
        "executor connection failure: connection_or_protocol phase=sftp_open"
        " origin=session exception=other",
    ]
    for error in (
        SshConfigurationError("secret"),
        TailnetValidationError("secret"),
        asyncssh.HostKeyNotVerifiable("secret"),
        asyncssh.PermissionDenied("secret"),
        TimeoutError("secret"),
        OSError("secret"),
    ):
        diagnostics.report(error)
    assert len(lines) == 8
    assert "secret" not in " ".join(lines).lower()


@pytest.mark.parametrize(
    "error,tag",
    [
        (asyncssh.ChannelOpenError(1, "secret"), "ChannelOpenError"),
        (asyncssh.SFTPError(4, "secret"), "SFTPError"),
        (asyncssh.SFTPFailure("secret"), "SFTPFailure"),
        (asyncssh.SFTPConnectionLost("secret"), "SFTPConnectionLost"),
        (asyncssh.SFTPOpUnsupported("secret"), "SFTPOpUnsupported"),
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
        "outer_connect",
        "relay_start",
        "hello",
        "transport_open",
        "sftp_open",
        "preview_setup",
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
        ExecutorSshConfig,
        SshAuthenticationMode,
        WireMessage,
        WireMessageKind,
    )

    async def run() -> None:
        executor = mac.MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_config=ExecutorSshConfig(
                "unused",
                1,
                "unused",
                tmp_path / "key",
                tmp_path / "hosts",
                SshAuthenticationMode.public_key,
            ),
            panels_server_url="http://unused",
        )
        failure = ValueError("secret key and frame bytes")
        lines: list[str] = []
        monkeypatch.setattr(mac, "connection_diagnostics", ConnectionDiagnostics(lines.append))
        reader = asyncio.StreamReader()
        process = SimpleNamespace(
            stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()), stdout=reader
        )
        closed: list[bool] = []

        def fail(phase: str) -> None:
            if failed_phase == phase:
                raise failure

        async def create_process(*args: object, **kwargs: object) -> object:
            fail("relay_start")
            return process

        outer = SimpleNamespace(create_process=create_process)

        async def connect(config: object) -> object:
            fail("outer_connect")
            return outer

        async def close(connection: object) -> None:
            closed.append(True)
            reader.feed_eof()

        @asynccontextmanager
        async def sftp(*args: object) -> AsyncIterator[None]:
            fail("sftp_open")
            yield None

        def preview(*args: object) -> object:
            fail("preview_setup")
            return SimpleNamespace(close=AsyncMock())

        async def request(kind: WireMessageKind, payload: object) -> WireMessage:
            if kind is WireMessageKind.hello:
                fail("hello")
                return WireMessage(kind, "reply", mac.as_object(executor.identity))
            if kind is WireMessageKind.transport_open:
                fail("transport_open")
                return WireMessage(
                    WireMessageKind.transport_opened,
                    "reply",
                    {
                        "endpoint": {
                            "socket_path": "/unused",
                            "server_public_key": "unused",
                            "username": "unused",
                        },
                        "preview_root": "/unused",
                    },
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

        monkeypatch.setattr(mac, "connect_outer", connect)
        monkeypatch.setattr(mac, "close_connection", close)
        monkeypatch.setattr(mac, "connect_sftp", sftp)
        monkeypatch.setattr(mac, "MacPreviewForwards", preview)
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
            assert bool(closed) is (failed_phase != "outer_connect")
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
    from planner.executor.contracts import ExecutorSshConfig, SshAuthenticationMode

    async def run() -> None:
        executor = mac.MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_config=ExecutorSshConfig(
                "unused",
                1,
                "unused",
                tmp_path / "key",
                tmp_path / "hosts",
                SshAuthenticationMode.public_key,
            ),
            panels_server_url="http://unused",
        )
        from types import SimpleNamespace

        lines: list[str] = []
        monkeypatch.setattr(mac, "connection_diagnostics", ConnectionDiagnostics(lines.append))
        failure = asyncio.IncompleteReadError(b"secret credentials", 10)
        outer = SimpleNamespace(
            create_process=AsyncMock(
                return_value=SimpleNamespace(
                    stdin=None,
                    stdout=None,
                )
            )
        )
        monkeypatch.setattr(mac, "connect_outer", AsyncMock(return_value=outer))
        monkeypatch.setattr(mac, "close_connection", AsyncMock())

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
