"""Stored scripts use ordinary HTTP, named keys, pagination, and provider errors."""

from __future__ import annotations

import gzip
import json
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from tests.support.measure_runtime import MemoryCredentials, admission

from planner.credentials.contracts import (
    BearerSecret,
    CredentialAuthKind,
    CredentialMetadata,
    CredentialReference,
    CredentialScheme,
    CredentialScope,
    ResolvedCredential,
)
from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import FailureCode, RuntimeBudget
from planner.measure_sources.runtime import Runtime


@pytest.fixture
def provider() -> Iterator[tuple[str, list[dict[str, object]]]]:
    calls: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.reply()

        def do_POST(self) -> None:
            self.reply()

        def reply(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append(
                {
                    "path": self.path,
                    "method": self.command,
                    "authorization": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/page")
                self.end_headers()
                return
            status = 401 if self.path == "/error" else 200
            if self.path == "/big":
                raw = gzip.compress(json.dumps({"payload": "x" * (3 * 1024 * 1024)}).encode())
            else:
                raw = json.dumps({"count": 1, "provider_error": "SECRET-RESPONSE"}).encode()
            self.send_response(status)
            # A normal script can parse JSON even when this MIME header is inaccurate.
            self.send_header("Content-Type", "text/plain")
            if self.path == "/big":
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                if self.path == "/trickle":
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def credentials() -> MemoryCredentials:
    return MemoryCredentials(
        ResolvedCredential(
            CredentialMetadata(
                "key",
                CredentialScope("synthetic-project", "synthetic-account"),
                CredentialReference(CredentialScheme.local, "project production analytics"),
                CredentialAuthKind.bearer,
                1,
                0,
                0,
            ),
            BearerSecret("TOKEN-DO-NOT-RECORD"),
        )
    )


def test_named_key_pages_json_post_and_standard_response(
    provider: tuple[str, list[dict[str, object]]],
) -> None:
    url, calls = provider
    resolver = credentials()
    script = f"""
total = 0
for page in range(65):
    response = request({url!r} + '/page?page=' + str(page), 'project production analytics')
    response.raise_for_status()
    total += response.json()['count']
response = request({url!r} + '/query', {{'scheme':'local','name':'project production analytics'}},
                   method='POST', json_body={{'query':'provider-computed count'}})
response.raise_for_status()
record('m', '2026-10-27', total + response.json()['count'])
"""
    result = Runtime().execute(admission(script), resolver)
    assert result.failure is None
    assert result.observations[0].value == 66
    assert result.metrics.request_count == 66
    assert all(call["authorization"] == "Bearer TOKEN-DO-NOT-RECORD" for call in calls)
    assert json.loads(calls[-1]["body"]) == {"query": "provider-computed count"}  # type: ignore[arg-type]
    assert len(resolver.calls) == 66
    assert "TOKEN-DO-NOT-RECORD" not in repr(result)


def test_compression_redirects_and_response_above_old_cap(
    provider: tuple[str, list[dict[str, object]]],
) -> None:
    url, _ = provider
    script = f"""
assert request({url!r} + '/redirect').json()['count'] == 1
response = request({url!r} + '/big')
record('m', '2026-10-27', len(response.json()['payload']))
"""
    result = Runtime().execute(admission(script), MemoryCredentials())
    assert result.failure is None
    assert result.observations[0].value == 3 * 1024 * 1024


def test_script_controls_provider_error_handling(
    provider: tuple[str, list[dict[str, object]]],
) -> None:
    url, _ = provider
    handled = Runtime().execute(
        admission(
            f"response=request({url!r}+'/error')\nrecord('m','2026-10-27',response.status_code)"
        ),
        MemoryCredentials(),
    )
    assert handled.failure is None and handled.observations[0].value == 401
    failed = Runtime().execute(
        admission(f"response=request({url!r}+'/error')\nresponse.raise_for_status()"),
        MemoryCredentials(),
    )
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.http_error
    assert failed.failure.http_status == 401 and failed.failure.source_line == 2
    assert "SECRET-RESPONSE" not in repr(failed)


def test_parent_timeout_covers_slow_response(provider: tuple[str, list[dict[str, object]]]) -> None:
    url, _ = provider
    result = Runtime().execute(
        admission(
            f"request({url!r}+'/trickle')",
            limits=replace(DEFAULT_LIMITS, wall_seconds=0.8),
        ),
        MemoryCredentials(),
    )
    assert result.failure is not None and result.failure.budget is RuntimeBudget.wall_seconds
    assert result.metrics.elapsed_milliseconds < 3000
