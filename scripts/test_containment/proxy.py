"""Outbound-only provider CONNECT proxy and private-namespace loopback bridge.

The only policy seam is the constructor used by unit tests. Runtime configuration
selects a fixed provider allowlist and never supplies addresses or a resolver.
"""

from __future__ import annotations

import argparse
import ipaddress
import select
import socket
import socketserver
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

ENDPOINTS: dict[str, frozenset[str]] = {
    "codex": frozenset({"api.openai.com", "auth.openai.com", "chatgpt.com"}),
    "claude": frozenset({"api.anthropic.com", "console.anthropic.com"}),
}
Resolver = Callable[[str, int], Iterable[str]]
Dialer = Callable[[str, int], socket.socket]


class ProxyRefusal(ValueError):
    """The requested destination cannot pass the fixed egress policy."""


def resolve_addresses(host: str, port: int) -> list[str]:
    return list({str(row[4][0]) for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def dial_address(address: str, port: int) -> socket.socket:
    # Numeric inet_pton plus connect avoids a second DNS lookup and DNS rebinding.
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    socket.inet_pton(family, address)
    connection = socket.socket(family, socket.SOCK_STREAM)
    try:
        connection.settimeout(20)
        connection.connect((address, port))
        connection.settimeout(None)
        return connection
    except BaseException:
        connection.close()
        raise


class DestinationPolicy:
    def __init__(
        self,
        backend: str,
        *,
        resolver: Resolver = resolve_addresses,
        dialer: Dialer = dial_address,
        host_addresses: Iterable[str] = (),
    ) -> None:
        if backend not in ENDPOINTS:
            raise ProxyRefusal(f"unsupported provider egress: {backend}")
        self.hosts = ENDPOINTS[backend]
        self.resolver = resolver
        self.dialer = dialer
        self.host_addresses = frozenset(
            str(ipaddress.ip_address(value)) for value in host_addresses
        )

    def connect(self, authority: str) -> socket.socket:
        host, separator, raw_port = authority.rpartition(":")
        if not separator or host not in self.hosts or raw_port != "443":
            raise ProxyRefusal("CONNECT destination is not an allowed provider TLS endpoint")
        addresses = list(self.resolver(host, 443))
        if not addresses:
            raise ProxyRefusal("provider destination has no addresses")
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ProxyRefusal("resolver returned a nonnumeric address") from exc
            if not parsed.is_global or parsed.is_multicast or str(parsed) in self.host_addresses:
                raise ProxyRefusal("provider destination resolves to a forbidden address")
            if isinstance(parsed, ipaddress.IPv6Address) and (
                parsed.ipv4_mapped is not None
                or parsed.sixtofour is not None
                or parsed.teredo is not None
            ):
                raise ProxyRefusal("translated provider address is forbidden")
        return self.dialer(addresses[0], 443)


def relay(left: socket.socket, right: socket.socket) -> None:
    """Copy a tunnel without interpreting TLS or accepting a reverse connection."""
    active = [left, right]
    while active:
        ready, _, _ = select.select(active, [], [], 60)
        if not ready:
            return
        for source in ready:
            destination = right if source is left else left
            data = source.recv(65536)
            if data:
                destination.sendall(data)
            else:
                active.remove(source)
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass


class _ThreadsUnix(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class _ThreadsTCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False


def _connect_request(connection: socket.socket) -> str:
    connection.settimeout(10)
    header = bytearray()
    # No read-ahead: early TLS bytes remain in the socket for the relay.
    while not header.endswith(b"\r\n\r\n"):
        byte = connection.recv(1)
        if not byte or len(header) >= 8192:
            raise ProxyRefusal("invalid CONNECT header")
        header.extend(byte)
    connection.settimeout(None)
    try:
        lines = bytes(header).decode("ascii").split("\r\n")
        method, authority, version = lines[0].split(" ")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProxyRefusal("invalid CONNECT request") from exc
    if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ProxyRefusal("only CONNECT requests are supported")
    return authority


class HostProxy:
    """One Unix socket, reachable only through the disposable boundary mount."""

    def __init__(self, path: Path, policy: DestinationPolicy) -> None:
        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                connection: socket.socket = self.request
                try:
                    authority = _connect_request(connection)
                    with policy.connect(authority) as upstream:
                        connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                        relay(connection, upstream)
                except (OSError, ProxyRefusal):
                    try:
                        connection.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                    except OSError:
                        pass

        if len(str(path).encode()) >= 108:
            raise ProxyRefusal("provider Unix socket path exceeds the platform limit")
        self.path = path
        self.server = _ThreadsUnix(str(path), Handler)
        path.chmod(0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> HostProxy:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.path.unlink(missing_ok=True)


def serve_bridge(path: Path, port: int) -> None:
    """Expose the one policy socket on namespace-local loopback for HTTP clients."""

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
                upstream.connect(str(path))
                relay(self.request, upstream)

    with _ThreadsTCP(("127.0.0.1", port), Handler) as server:
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["bridge"])
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    serve_bridge(args.socket, args.port)


if __name__ == "__main__":
    main()
