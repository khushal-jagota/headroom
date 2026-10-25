"""Closed categories and the process-lifetime diagnostic output budget."""

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
