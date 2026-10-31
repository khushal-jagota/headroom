"""Closed categories and the process-lifetime diagnostic output budget."""

import ssl

import aiohttp
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from planner.executor.diagnostics import ConnectionDiagnostics


def forbidden_handshake() -> aiohttp.WSServerHandshakeError:
    url = URL("https://fixture.invalid/api/executor/connect")
    request = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    return aiohttp.WSServerHandshakeError(request, (), status=403)


def test_context_is_finite_and_never_exposes_custom_names_or_messages() -> None:
    from planner.executor.protocol import ConnectionFailureOrigin, ConnectionPhase

    class SecretCredentialName(ValueError):
        pass

    lines: list[str] = []
    diagnostics = ConnectionDiagnostics(lines.append)
    diagnostics.report(ValueError("secret frame"))
    for _ in range(100):
        diagnostics.report(
            SecretCredentialName("secret credential"),
            phase=ConnectionPhase.hello,
            origin=ConnectionFailureOrigin.session,
        )
    assert lines == [
        "executor connection failure: connection_or_protocol",
        "executor connection failure: connection_or_protocol phase=hello"
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
