"""Production HTTPS transport: public DNS, pinned sockets, original TLS hostname."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit

from planner.measure_sources.contracts import (
    AuthenticatedHttpRequest,
    FailureCode,
    RuntimeBudget,
    TransportResponse,
)
from planner.measure_sources.logic.protocol import refuse


def public_addresses(host: str, port: int) -> tuple[tuple[int, str], ...]:
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    result: list[tuple[int, str]] = []
    for family, _, _, _, address in answers:
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise refuse(FailureCode.dns_policy_refused)
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise refuse(FailureCode.dns_policy_refused)
        if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
            raise refuse(FailureCode.dns_policy_refused)
        pair = (family, str(ip))
        if pair not in result:
            result.append(pair)
    if not result:
        raise refuse(FailureCode.transport_failure)
    return tuple(result)


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise refuse(FailureCode.budget_exhausted, RuntimeBudget.wall_seconds)
    return seconds


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: tuple[int, str], deadline: float) -> None:
        self.tls_context = ssl.create_default_context()
        super().__init__(host, port, timeout=remaining(deadline), context=self.tls_context)
        self.address = address
        self.deadline = deadline

    def connect(self) -> None:
        family, address = self.address
        raw = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(remaining(self.deadline))
            # No second DNS lookup: connect only the address already validated.
            raw.connect((address, self.port))
            self.sock = self.tls_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class PublicHttpsTransport:
    def send(
        self,
        request: AuthenticatedHttpRequest,
        *,
        deadline_monotonic: float,
        max_response_bytes: int,
    ) -> TransportResponse:
        url = urlsplit(request.url)
        if url.scheme != "https" or url.hostname is None:
            raise refuse(FailureCode.unauthorized_request)
        port = url.port if url.port is not None else 443
        addresses = public_addresses(url.hostname, port)
        # One attempt, in resolver order. Never replay a request or try another
        # address after a failure, including an uncertain POST delivery.
        connection = _PinnedHTTPS(url.hostname, port, addresses[0], deadline_monotonic)
        try:
            target = url.path or "/"
            if url.query:
                target += "?" + url.query
            connection.request(request.method.value, target, request.body, dict(request.headers))
            response = connection.getresponse()
            headers = tuple(response.getheaders())
            header_bytes = sum(len(k.encode()) + len(v.encode()) + 4 for k, v in headers)
            if header_bytes > max_response_bytes:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.response_bytes)
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise refuse(FailureCode.invalid_json)
            body = bytearray()
            while True:
                if connection.sock is not None:
                    connection.sock.settimeout(remaining(deadline_monotonic))
                chunk = response.read1(min(8192, max_response_bytes - header_bytes - len(body) + 1))
                if not chunk:
                    break
                body.extend(chunk)
                if header_bytes + len(body) > max_response_bytes:
                    raise refuse(FailureCode.budget_exhausted, RuntimeBudget.response_bytes)
            return TransportResponse(response.status, headers, bytes(body))
        finally:
            connection.close()
