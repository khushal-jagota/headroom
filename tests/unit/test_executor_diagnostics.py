"""Closed categories and the rate limit on repeated connection-failure lines."""

import ssl

import aiohttp

from planner.executor.diagnostics import ConnectionDiagnostics, ConnectionPhase
from planner.executor.mac_http import ExecutorHttpError


def test_each_kind_logs_first_then_repeats_once_per_interval_without_messages() -> None:
    class CustomFailure(ValueError):
        pass

    lines: list[str] = []
    now = [0.0]
    diagnostics = ConnectionDiagnostics(lines.append, interval_seconds=60, monotonic=lambda: now[0])
    diagnostics.report(CustomFailure("secret frame"), phase=ConnectionPhase.lease)
    # A retrying loop every 5 s for 10 minutes: the first line, then one a minute.
    for _ in range(120):
        diagnostics.report(ValueError("secret credential"), phase=ConnectionPhase.poll)
        now[0] += 5
    for error in (
        aiohttp.InvalidURL("secret"),
        ssl.SSLError("secret"),
        ssl.SSLError("secret"),
        ExecutorHttpError(403, "agent_forbidden", "secret"),
        TimeoutError("secret"),
        OSError("secret"),
    ):
        diagnostics.report(error, phase=ConnectionPhase.items)
    steady = "executor connection failure: connection_or_protocol phase=poll exception=ValueError"
    assert lines == [
        "executor connection failure: connection_or_protocol phase=lease exception=CustomFailure",
        steady,
        *[f"{steady} unlogged_repeats=11"] * 9,
        "executor connection failure: configuration phase=items exception=InvalidURL",
        "executor connection failure: tls phase=items exception=SSLError",
        "executor connection failure: authentication phase=items"
        " exception=HTTP 403 agent_forbidden",
        "executor connection failure: timeout phase=items exception=TimeoutError",
        "executor connection failure: network phase=items exception=OSError",
    ]
    assert "secret" not in " ".join(lines).lower()
