"""Closed categories and the rate limit on repeated connection-failure lines."""

import ssl

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from planner.executor.diagnostics import ConnectionDiagnostics, ConnectionPhase


def forbidden_handshake() -> aiohttp.WSServerHandshakeError:
    url = URL("https://fixture.invalid/api/executor/connect")
    request = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    return aiohttp.WSServerHandshakeError(request, (), status=403)


def test_each_kind_logs_first_then_repeats_once_per_interval_without_messages() -> None:
    class CustomFailure(ValueError):
        pass

    lines: list[str] = []
    now = [0.0]
    diagnostics = ConnectionDiagnostics(lines.append, interval_seconds=60, monotonic=lambda: now[0])
    diagnostics.report(CustomFailure("secret frame"), phase=ConnectionPhase.hello)
    # A reconnect loop every 5 s for 10 minutes: the first line, then one a minute.
    for _ in range(120):
        diagnostics.report(ValueError("secret credential"), phase=ConnectionPhase.steady_state)
        now[0] += 5
    for error in (
        aiohttp.InvalidURL("secret"),
        ssl.SSLError("secret"),
        ssl.SSLError("secret"),
        forbidden_handshake(),
        TimeoutError("secret"),
        OSError("secret"),
    ):
        diagnostics.report(error, phase=ConnectionPhase.app_connect)
    steady = (
        "executor connection failure: connection_or_protocol phase=steady_state"
        " exception=ValueError"
    )
    assert lines == [
        "executor connection failure: connection_or_protocol phase=hello exception=CustomFailure",
        steady,
        *[f"{steady} unlogged_repeats=11"] * 9,
        "executor connection failure: configuration phase=app_connect exception=InvalidURL",
        "executor connection failure: tls phase=app_connect exception=SSLError",
        "executor connection failure: authentication phase=app_connect"
        " exception=WSServerHandshakeError",
        "executor connection failure: timeout phase=app_connect exception=TimeoutError",
        "executor connection failure: network phase=app_connect exception=OSError",
    ]
    assert "secret" not in " ".join(lines).lower()
