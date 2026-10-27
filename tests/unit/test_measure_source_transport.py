"""Pure grants and production network policy; no public network dependency."""

from __future__ import annotations

import socket
import time
from dataclasses import replace

import pytest
from tests.support.measure_runtime import admission

from planner.credentials.contracts import (
    BearerSecret,
    CredentialAuthKind,
    CredentialMetadata,
    CredentialReference,
    CredentialScheme,
    CredentialScope,
    ResolvedCredential,
)
from planner.measure_sources.contracts import (
    BrokerConfiguration,
    DestinationGrant,
    FailureCode,
    HostRequest,
    HttpMethod,
    PathMatch,
)
from planner.measure_sources.logic.protocol import Refusal, decode
from planner.measure_sources.logic.transport import authenticate, normalize_url
from planner.measure_sources.transport import _PinnedHTTPS, public_addresses


def configuration() -> BrokerConfiguration:
    value = admission("pass")
    reference = CredentialReference(CredentialScheme.local, "provider-production")
    credential = ResolvedCredential(
        CredentialMetadata(
            "credential",
            value.scope,
            reference,
            CredentialAuthKind.bearer,
            ("https://api.example.com",),
            1,
            0,
            0,
        ),
        BearerSecret("synthetic-key"),
    )
    grant = DestinationGrant(
        "provider-production",
        "https://api.example.com",
        HttpMethod.GET,
        "/api",
        PathMatch.prefix,
        reference,
    )
    return BrokerConfiguration(
        value.scope, (grant,), (credential,), value.limits, value.deadline_monotonic
    )


def test_url_authorization_rejects_ambiguous_paths_and_has_no_unauthenticated_fallback() -> None:
    config = configuration()
    valid = HostRequest(
        "provider-production", "https://api.example.com/api/value?page=2", HttpMethod.GET
    )
    request = authenticate(config, valid)
    assert dict(request.headers)["Authorization"] == "Bearer synthetic-key"
    for url in (
        "https://api.example.com:0/api",
        "https://api.example.com/api/%ff",
        "https://api.example.com/api/%2fsecret",
        "https://api.example.com/api/%252e%252e",
        "https://api.example.com/api/../admin",
        "https://api.example.com/api/%2e%2e/admin",
        "https://api.example.com/api-other",
        "https://api.example.com/api\\admin",
        "https://api.example.com@other.example/api",
        "https://other.example/api",
        "http://api.example.com/api",
        "https://api.example.com/api#fragment",
        "https://api.example.com/api/%",
        "https://api.example.com/api/%00",
    ):
        with pytest.raises(Refusal) as denied:
            authenticate(config, replace(valid, url=url))
        assert denied.value.failure.code == FailureCode.unauthorized_request, url
    for changed in (
        replace(config, credentials=()),
        replace(config, scope=CredentialScope("foreign-project", "synthetic-account")),
        replace(
            config,
            credentials=(
                replace(
                    config.credentials[0],
                    metadata=replace(
                        config.credentials[0].metadata,
                        allowed_origins=("https://other.example",),
                    ),
                ),
            ),
        ),
    ):
        with pytest.raises(Refusal):
            authenticate(changed, valid)
    with pytest.raises(Refusal):
        authenticate(config, replace(valid, method=HttpMethod.POST))
    assert normalize_url("https://API.example.com:443/api/value") == (
        "https://api.example.com/api/value",
        "https://api.example.com",
        "/api/value",
    )


def test_public_dns_rejects_mixed_answers_and_every_nonpublic_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for address in (
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "::ffff:8.8.8.8",
    ):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, family=family, address=address, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                (family, socket.SOCK_STREAM, 6, "", (address, 443)),
            ],
        )
        with pytest.raises(Refusal) as denied:
            public_addresses("api.example.com", 443)
        assert denied.value.failure.code == FailureCode.unauthorized_request
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ],
    )
    assert public_addresses("api.example.com", 443) == ((socket.AF_INET, "8.8.8.8"),)


def test_connection_uses_pinned_address_and_original_tls_name_without_second_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class FakeSocket:
        def settimeout(self, timeout: float) -> None:
            assert timeout > 0

        def connect(self, target: object) -> None:
            calls.append(target)

        def close(self) -> None:
            pass

    class FakeTLS:
        def wrap_socket(self, raw: FakeSocket, *, server_hostname: str) -> FakeSocket:
            calls.append(server_hostname)
            return raw

    def no_dns(*args: object, **kwargs: object) -> None:
        pytest.fail("DNS was repeated after pinning")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSocket())
    monkeypatch.setattr("planner.measure_sources.transport.ssl.create_default_context", FakeTLS)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    connection = _PinnedHTTPS(
        "api.example.com", 443, (socket.AF_INET, "8.8.8.8"), time.monotonic() + 1
    )
    connection.connect()
    connection.close()
    assert calls == [("8.8.8.8", 443), "api.example.com"]


def test_json_rejects_duplicate_keys_nonfinite_numbers_and_excess_depth() -> None:
    for data in (
        b'{"op":1,"op":2}',
        b'{"value":NaN}',
        b'{"value":1e9999}',
        b"[" * 33 + b"0" + b"]" * 33,
    ):
        with pytest.raises(Refusal):
            decode(data)
