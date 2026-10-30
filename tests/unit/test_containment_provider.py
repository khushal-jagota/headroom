"""Disposable profile validation and injected provider destination policy proofs."""

from __future__ import annotations

import os
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_containment.provider import (
    ProviderRefusal,
    profile_mount_args,
    validate_profile,
)
from test_containment.proxy import (
    DestinationPolicy,
    HostProxy,
    ProxyRefusal,
    _connect_request,
)


@pytest.fixture
def short_socket_directory() -> Iterator[Path]:
    # The pytest root itself lives below the runner scratch tree. AF_UNIX paths
    # need a separate short path inside the already private /tmp namespace.
    with tempfile.TemporaryDirectory(prefix="pc-", dir="/tmp") as name:
        yield Path(name)


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "canonical" / ".claude"
    (profile / "skills").mkdir(parents=True)
    (profile / "skills" / "sentinel").write_text("protected")
    (profile / "credentials.json").write_text("synthetic-auth")
    return profile


def test_provider_mount_preserves_canonical_identity(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    before = profile.stat()
    validated = validate_profile(profile, [profile / "skills"])
    args = profile_mount_args(validated)
    assert args[:3] == ["--bind", str(profile), str(profile)]
    assert args[3:] == ["--ro-bind", str(profile / "skills"), str(profile / "skills")]
    assert validated.profile.stat().st_ino == before.st_ino
    assert (profile / "credentials.json").read_text() == "synthetic-auth"


@pytest.mark.parametrize("layout", ["absent", "symlink", "dangling", "profile-alias"])
def test_provider_rejects_unsafe_skill_roots(tmp_path: Path, layout: str) -> None:
    profile = _profile(tmp_path)
    skills = profile / "skills"
    (skills / "sentinel").unlink()
    skills.rmdir()
    if layout in {"symlink", "dangling"}:
        target = tmp_path / "target"
        if layout == "symlink":
            target.mkdir()
        skills.symlink_to(target)
    if layout == "profile-alias":
        skills.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(profile, target_is_directory=True)
        profile = alias
    with pytest.raises(ProviderRefusal):
        validate_profile(profile, [])


@pytest.mark.parametrize("alias", ["hardlink", "symlink", "target-overlap", "ancestor"])
def test_provider_refuses_protected_writable_alias(tmp_path: Path, alias: str) -> None:
    profile = _profile(tmp_path)
    sentinel = profile / "skills" / "sentinel"
    protected = [profile / "skills"]
    if alias == "hardlink":
        os.link(sentinel, profile / "alias")
    elif alias == "symlink":
        (profile / "alias").symlink_to(sentinel)
    elif alias == "target-overlap":
        (profile / "skills" / "alias").symlink_to(profile / "credentials.json")
    else:
        protected.append(profile.parent)
    with pytest.raises(ProviderRefusal):
        validate_profile(profile, protected)


def test_provider_projects_external_target_without_other_accounts(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    target = tmp_path / "managed"
    target.mkdir()
    (target / "SKILL.md").write_text("sentinel")
    (profile / "skills" / "native").symlink_to(target, target_is_directory=True)
    other = tmp_path / "other-account" / "skills"
    other.mkdir(parents=True)
    validated = validate_profile(profile, [profile / "skills", other])
    assert validated.protected_targets == (target,)
    assert str(other) not in profile_mount_args(validated)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:8.8.8.8",
        "2002:0808:0808::1",
        "224.0.0.1",
    ],
)
def test_proxy_rejects_nonpublic_and_translated_addresses(address: str) -> None:
    def unexpected_dial(host: str, port: int) -> socket.socket:
        pytest.fail("forbidden address reached dialer")

    policy = DestinationPolicy(
        "claude", resolver=lambda host, port: [address], dialer=unexpected_dial
    )
    with pytest.raises(ProxyRefusal):
        policy.connect("api.anthropic.com:443")


@pytest.mark.parametrize(
    "authority",
    [
        "localhost:443",
        "api.anthropic.com:80",
        "api.anthropic.com:0443",
        "API.ANTHROPIC.COM:443",
        "api.anthropic.com.evil:443",
        "8.8.8.8:443",
        "user@api.anthropic.com:443",
        "api.anthropic.com.:443",
    ],
)
def test_proxy_rejects_unlisted_authorities(authority: str) -> None:
    def unexpected_resolve(host: str, port: int) -> list[str]:
        pytest.fail("unlisted destination reached DNS")

    with pytest.raises(ProxyRefusal):
        DestinationPolicy("claude", resolver=unexpected_resolve).connect(authority)


def test_proxy_pins_once_and_rejects_mixed_or_host_addresses() -> None:
    observed: list[tuple[str, int]] = []
    left, right = socket.socketpair()

    def dial(address: str, port: int) -> socket.socket:
        observed.append((address, port))
        return left

    policy = DestinationPolicy("claude", resolver=lambda host, port: ["8.8.8.8"], dialer=dial)
    try:
        assert policy.connect("api.anthropic.com:443") is left
        assert observed == [("8.8.8.8", 443)]
        for addresses, assigned in [(["8.8.8.8", "127.0.0.1"], []), (["8.8.8.8"], ["8.8.8.8"])]:

            def resolve(host: str, port: int, values: list[str] = addresses) -> list[str]:
                return values

            denied = DestinationPolicy(
                "claude", resolver=resolve, dialer=dial, host_addresses=assigned
            )
            with pytest.raises(ProxyRefusal):
                denied.connect("api.anthropic.com:443")
        assert len(observed) == 1
    finally:
        left.close()
        right.close()


def test_proxy_connect_transport_uses_only_injected_socket(short_socket_directory: Path) -> None:
    upstream, peer = socket.socketpair()
    policy = DestinationPolicy(
        "claude", resolver=lambda host, port: ["8.8.8.8"], dialer=lambda address, port: upstream
    )
    with HostProxy(short_socket_directory / "proxy.sock", policy):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(3)
            peer.settimeout(3)
            client.connect(str(short_socket_directory / "proxy.sock"))
            client.sendall(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n")
            assert client.recv(1024) == b"HTTP/1.1 200 Connection Established\r\n\r\n"
            client.sendall(b"synthetic-client")
            assert peer.recv(1024) == b"synthetic-client"
            peer.sendall(b"synthetic-server")
            assert client.recv(1024) == b"synthetic-server"
            client.shutdown(socket.SHUT_WR)
            peer.shutdown(socket.SHUT_WR)
    peer.close()


def test_connect_parser_refuses_plain_http_and_preserves_tunnel_bytes() -> None:
    for request, succeeds in [
        (b"GET http://localhost HTTP/1.1\r\n\r\n", False),
        (b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\nTLS", True),
    ]:
        left, right = socket.socketpair()
        try:
            right.sendall(request)
            if succeeds:
                assert _connect_request(left) == "api.anthropic.com:443"
                assert left.recv(3) == b"TLS"
            else:
                with pytest.raises(ProxyRefusal):
                    _connect_request(left)
        finally:
            left.close()
            right.close()


def test_namespace_loopback_bridge_relays_disposable_unix_endpoint(
    short_socket_directory: Path,
) -> None:
    import subprocess
    import sys
    import threading
    import time

    endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = short_socket_directory / "echo.sock"
    endpoint.bind(str(path))
    endpoint.listen(3)
    endpoint.settimeout(4)
    errors: list[str] = []

    def echo() -> None:
        try:
            # Readiness probe and then the actual transport connection.
            for _ in range(2):
                connection, _ = endpoint.accept()
                with connection:
                    connection.settimeout(3)
                    data = connection.recv(1024)
                    if data:
                        connection.sendall(data.upper())
        except OSError as exc:
            errors.append(str(exc))

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    worker = threading.Thread(target=echo, daemon=True)
    worker.start()
    bridge = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/test_containment/proxy.py"),
            "bridge",
            "--socket",
            str(path),
            "--port",
            str(port),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                assert bridge.poll() is None, "bridge child exited before readiness"
                time.sleep(0.02)
        else:
            pytest.fail("bridge readiness timed out")
        with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
            client.sendall(b"disposable-transport")
            assert client.recv(1024) == b"DISPOSABLE-TRANSPORT"
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert not errors
    finally:
        bridge.terminate()
        bridge.communicate(timeout=5)
        endpoint.close()
