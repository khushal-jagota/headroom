"""Closed categories and the process-lifetime diagnostic output budget."""

import ssl

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from planner.executor.diagnostics import ConnectionDiagnostics, ConnectionPhase


def forbidden_handshake() -> aiohttp.WSServerHandshakeError:
    url = URL("https://fixture.invalid/api/executor/connect")
    request = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    return aiohttp.WSServerHandshakeError(request, (), status=403)


def test_each_category_logs_its_first_phase_and_class_without_messages() -> None:
    class CustomFailure(ValueError):
        pass

    lines: list[str] = []
    diagnostics = ConnectionDiagnostics(lines.append)
    diagnostics.report(CustomFailure("secret frame"), phase=ConnectionPhase.hello)
    for _ in range(100):
        diagnostics.report(ValueError("secret credential"), phase=ConnectionPhase.steady_state)
    for error in (
        aiohttp.InvalidURL("secret"),
        ssl.SSLError("secret"),
        ssl.SSLError("secret"),
        forbidden_handshake(),
        TimeoutError("secret"),
        OSError("secret"),
    ):
        diagnostics.report(error, phase=ConnectionPhase.app_connect)
    assert lines == [
        "executor connection failure: connection_or_protocol phase=hello exception=CustomFailure",
        "executor connection failure: configuration phase=app_connect exception=InvalidURL",
        "executor connection failure: tls phase=app_connect exception=SSLError",
        "executor connection failure: authentication phase=app_connect"
        " exception=WSServerHandshakeError",
        "executor connection failure: timeout phase=app_connect exception=TimeoutError",
        "executor connection failure: network phase=app_connect exception=OSError",
    ]
    assert "secret" not in " ".join(lines).lower()
