"""One capturing TLS fixture for real clean broker and source processes."""

from __future__ import annotations

import base64
import http.server
import json
import ssl
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from tests.support.measure_runtime import admission, fixture_broker_command, fixture_certificate

from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialMetadata,
    CredentialReference,
    CredentialScheme,
    HeaderSecret,
    ResolvedCredential,
)
from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import (
    DestinationGrant,
    FailureCode,
    HttpMethod,
    PathMatch,
    RuntimeBudget,
)
from planner.measure_sources.runtime import Runtime, supervisor

SECRET = "synthetic-fixture-key"


@pytest.fixture
def tls_fixture(tmp_path: Path) -> Iterator[tuple[str, Path, list[tuple[str, str, str | None]]]]:
    cert_path, key_path = fixture_certificate(tmp_path)
    captured: list[tuple[str, str, str | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.do_GET()

        def do_GET(self) -> None:
            captured.append(
                (
                    self.command,
                    self.path,
                    self.headers.get("Authorization") or self.headers.get("X-Api-Key"),
                )
            )
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            body: dict[str, object] = {"value": 42}
            status = 200
            extra: list[tuple[str, str]] = []
            if path.path == "/api/page":
                time.sleep(float(query.get("delay", ["0"])[0]))
                body = {"value": int(query.get("n", ["0"])[0])}
            elif path.path == "/api/large":
                body = {"padding": "x" * int(query.get("size", ["1800000"])[0])}
            elif path.path == "/api/reflection":
                body = {
                    "value": 42,
                    "secret": SECRET,
                    "escaped": SECRET,
                    "base64": base64.b64encode(SECRET.encode()).decode(),
                }
                extra = [("X-Echo", SECRET)]
            elif path.path == "/api/redirect":
                status = 302
                extra = [
                    (
                        "Location",
                        f"https://localhost:{server.server_port}/capture?token={SECRET}",
                    )
                ]
            raw = b"invalid-json" if path.path == "/api/invalid" else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for header, value in extra:
                self.send_header(header, value)
            self.end_headers()
            try:
                if path.path == "/api/trickle":
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    self.wfile.write(raw)
            except OSError:
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", cert_path, captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_mediation_auth_redaction_and_second_source_without_capability_changes(
    tls_fixture: tuple[str, Path, list[tuple[str, str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, certificate, captured = tls_fixture
    production_command = supervisor._broker_command
    monkeypatch.setattr(
        supervisor,
        "_broker_command",
        lambda value: fixture_broker_command(production_command(value), certificate),
    )
    reference = CredentialReference(CredentialScheme.local, "provider-production")
    get = DestinationGrant(
        "provider-production", origin, HttpMethod.GET, "/api", PathMatch.prefix, reference
    )
    post = replace(
        get,
        id="provider-query",
        method=HttpMethod.POST,
        path="/api/query",
        path_match=PathMatch.exact,
    )
    script = f"""
a=request('provider-production',{origin + "/api/reflection"!r})
assert a['json_body']['secret']=='[REDACTED]' and a['json_body']['base64']=='[REDACTED]'
assert dict(a['headers'])['X-Echo']=='[REDACTED]'
b=request('provider-production',{origin + "/api/redirect"!r})
assert b['status']==302 and '[REDACTED]' in dict(b['headers'])['Location']
c=request('provider-query',{origin + "/api/query"!r},
 method='POST',json_body={{'query':'synthetic-read'}})
record('m','2026-10-27',c['json_body']['value'])
"""
    value = admission(script, grants=(get, post))
    metadata = CredentialMetadata(
        "key", value.scope, reference, CredentialAuthKind.bearer, (origin,), 1, 0, 0
    )
    credential = ResolvedCredential(metadata, BearerSecret(SECRET))
    result = Runtime().execute(value, (credential,))
    assert result.failure is None, result
    assert result.observations[0].value == 42
    assert [item[0] for item in captured] == ["GET", "GET", "POST"]
    assert all(item[2] == "Bearer " + SECRET for item in captured)
    assert not any("/capture" in item[1] for item in captured)
    assert SECRET not in repr(result)
    for kind, secret, expected in (
        (CredentialAuthKind.header, HeaderSecret("X-Api-Key", SECRET), SECRET),
        (
            CredentialAuthKind.basic,
            BasicSecret("user", SECRET),
            "Basic " + base64.b64encode(("user:" + SECRET).encode()).decode(),
        ),
    ):
        second = admission(
            f"response=request('second-provider',{origin + '/api/value'!r})\n"
            "record('m','2026-10-27',response['json_body']['value'])",
            grants=(replace(get, id="second-provider"),),
            run_id="second-source",
        )
        second_key = ResolvedCredential(replace(metadata, auth_kind=kind), secret)
        assert Runtime().execute(second, (second_key,)).failure is None
        assert captured[-1][2] == expected
    before = len(captured)
    denied = admission(f"request('provider-production',{origin + '/capture'!r})", grants=(get,))
    failure = Runtime().execute(denied, (credential,)).failure
    assert failure is not None and failure.code == FailureCode.unauthorized_request
    assert len(captured) == before


def test_production_entry_rejects_loopback_even_with_environment_override(
    tls_fixture: tuple[str, Path, list[tuple[str, str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, _, captured = tls_fixture
    monkeypatch.setenv("MEASURE_ALLOW_LOCAL_NETWORK", "true")
    monkeypatch.setenv("HTTPS_PROXY", origin)
    grant = DestinationGrant("public-only", origin, HttpMethod.GET, "/api", PathMatch.prefix, None)
    result = Runtime().execute(
        admission(f"request('public-only',{origin + '/api/value'!r})", grants=(grant,)), ()
    )
    assert result.failure is not None and result.failure.code == FailureCode.unauthorized_request
    assert not captured


def test_slow_pages_near_limit_json_and_deadline_have_measured_budgets(
    tls_fixture: tuple[str, Path, list[tuple[str, str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, certificate, _ = tls_fixture
    original = supervisor._broker_command
    monkeypatch.setattr(
        supervisor,
        "_broker_command",
        lambda value: fixture_broker_command(original(value), certificate),
    )
    grant = DestinationGrant("pages", origin, HttpMethod.GET, "/api", PathMatch.prefix, None)
    script = f"""
total=0
for n in range(3):
 total+=request('pages',{origin!r}+'/api/page?delay=0.1&n='+str(n))['json_body']['value']
large=request('pages',{origin + "/api/large"!r})
assert len(large['json_body']['padding'])==1800000
record('m','2026-10-27',total)
"""
    result = Runtime().execute(admission(script, grants=(grant,)), ())
    assert result.failure is None, result
    assert result.observations[0].value == 3 and result.metrics.request_count == 4
    assert result.metrics.response_bytes > 1_800_000
    assert result.metrics.elapsed_milliseconds >= 300
    print("slow-pages-and-json", result.metrics)
    oversized = Runtime().execute(
        admission(
            f"request('pages',{origin + '/api/large?size=2200000'!r})",
            grants=(grant,),
        ),
        (),
    )
    assert (
        oversized.failure is not None and oversized.failure.budget == RuntimeBudget.response_bytes
    )
    timed = Runtime().execute(
        admission(
            f"request('pages',{origin + '/api/trickle'!r})",
            grants=(grant,),
            limits=replace(DEFAULT_LIMITS, wall_seconds=0.3),
        ),
        (),
    )
    assert timed.failure is not None and timed.failure.budget == RuntimeBudget.wall_seconds
    assert timed.metrics.elapsed_milliseconds < 1500


def test_actual_broker_request_and_cumulative_response_budgets(
    tls_fixture: tuple[str, Path, list[tuple[str, str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, certificate, _ = tls_fixture
    original = supervisor._broker_command
    monkeypatch.setattr(
        supervisor,
        "_broker_command",
        lambda value: fixture_broker_command(original(value), certificate),
    )
    grant = DestinationGrant("pages", origin, HttpMethod.GET, "/api", PathMatch.prefix, None)
    source = f"for i in range(3): request('pages',{origin + '/api/large?size=800'!r})"
    for limits, budget in (
        (replace(DEFAULT_LIMITS, request_count=2), RuntimeBudget.request_count),
        (replace(DEFAULT_LIMITS, total_response_bytes=1500), RuntimeBudget.total_response_bytes),
    ):
        result = Runtime().execute(admission(source, grants=(grant,), limits=limits), ())
        assert result.failure is not None and result.failure.budget == budget
        assert not result.observations


def test_invalid_provider_json_discards_earlier_candidate(
    tls_fixture: tuple[str, Path, list[tuple[str, str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, certificate, _ = tls_fixture
    original = supervisor._broker_command
    monkeypatch.setattr(
        supervisor,
        "_broker_command",
        lambda value: fixture_broker_command(original(value), certificate),
    )
    grant = DestinationGrant("json", origin, HttpMethod.GET, "/api", PathMatch.prefix, None)
    source = "record('m','2026-10-27',42)\n" + f"request('json',{origin + '/api/invalid'!r})"
    result = Runtime().execute(admission(source, grants=(grant,)), ())
    assert result.failure is not None and result.failure.code == FailureCode.invalid_json
    assert not result.observations
