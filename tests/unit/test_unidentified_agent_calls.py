"""Socket ownership and admission without a live server or database."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from planner.core import unidentified_agent_calls as calls
from planner.core.authctx import AUTHENTICATED_OWNER_SCOPE_KEY
from planner.core.config import load_config

PEER = ("127.0.0.1", 40001)
SERVER = ("127.0.0.1", 8767)
SETTING = "PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS"
SESSION = "/user.slice/user-1000.slice/session-42.scope"
LIVE = "/user.slice/user-1000.slice/user@1000.service/app.slice/panels-live.service"
WORKER = "/user.slice/user-1000.slice/user@1000.service/panels-workers.slice/worker.service"


def socket_row(address: str = "0100007F", *, uid: int | None = None) -> str:
    return (
        f"0: {address}:9C41 {address}:223F 01 00000000:00000000 "
        f"00:00000000 00000000 {os.getuid() if uid is None else uid} 0 12345\n"
    )


@pytest.fixture
def proc(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    (root / "net").mkdir(parents=True)
    (root / "net/tcp").write_text("header\n" + socket_row())
    (root / "net/tcp6").write_text("header\n")
    holder(root, 101, SESSION)
    return root


def holder(proc: Path, pid: int, cgroup: str) -> Path:
    directory = proc / str(pid)
    (directory / "fd").mkdir(parents=True)
    (directory / "cgroup").write_text(f"0::{cgroup}\n")
    descriptor = directory / "fd/4"
    descriptor.symlink_to("socket:[12345]")
    return descriptor


@pytest.mark.parametrize(
    ("host", "address", "table"),
    [
        ("127.0.0.1", "0100007F", "tcp"),
        ("::1", "00000000000000000000000001000000", "tcp6"),
        ("::ffff:127.0.0.1", "0000000000000000FFFF00000100007F", "tcp6"),
    ],
)
def test_login_session_socket_formats_are_owner(
    proc: Path, host: str, address: str, table: str
) -> None:
    (proc / "net/tcp").write_text("header\n")
    (proc / "net" / table).write_text("header\n" + socket_row(address))
    assert calls.resolve_loopback_peer((host, PEER[1]), SERVER, proc=proc).is_owner


@pytest.mark.parametrize("cgroup", [LIVE, WORKER])
def test_panels_agent_cgroups_are_refused(proc: Path, cgroup: str) -> None:
    (proc / "101/cgroup").write_text(f"0::{cgroup}\n")
    assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


def test_any_agent_holder_refuses_a_shared_socket(proc: Path) -> None:
    holder(proc, 202, WORKER)
    assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


def test_socket_uid_and_established_endpoint_must_match(proc: Path) -> None:
    table = proc / "net/tcp"
    for row in (
        socket_row(uid=os.getuid() + 1),
        socket_row().replace(":223F", ":2240"),
        socket_row().replace(":9C41", ":9C42"),
        socket_row().replace(" 01 ", " 06 "),
    ):
        table.write_text("header\n" + row)
        assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


def test_unresolved_or_nonloopback_peer_is_refused(proc: Path) -> None:
    assert not calls.resolve_loopback_peer(("192.0.2.1", PEER[1]), SERVER, proc=proc).is_owner
    (proc / "101/fd/4").unlink()
    assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


@pytest.mark.parametrize("unreadable", ["net/tcp", "101/cgroup"])
def test_unreadable_socket_or_holder_data_refuses(
    proc: Path, monkeypatch: pytest.MonkeyPatch, unreadable: str
) -> None:
    original = Path.read_text

    def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == proc / unreadable:
            raise PermissionError("synthetic unreadable proc file")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


def test_holder_socket_is_rechecked_after_cgroup_read(
    proc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.read_text

    def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        value = original(path, *args, **kwargs)
        if path == proc / "101/cgroup":
            descriptor = proc / "101/fd/4"
            descriptor.unlink()
            descriptor.symlink_to("socket:[99999]")
        return value

    monkeypatch.setattr(Path, "read_text", read_text)
    assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


def test_unreadable_same_user_process_cannot_hide_another_holder(
    proc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder(proc, 202, WORKER)
    deny_descriptor_directory(proc, monkeypatch)
    assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


def deny_descriptor_directory(proc: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.iterdir

    def iterdir(path: Path) -> Iterator[Path]:
        if path == proc / "202/fd":
            raise PermissionError("synthetic unreadable descriptors")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)


def test_unreadable_outside_process_is_skipped_with_a_readable_owner_holder(
    proc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder(proc, 202, SESSION)
    deny_descriptor_directory(proc, monkeypatch)
    result = calls.resolve_loopback_peer(PEER, SERVER, proc=proc)
    assert result == calls.PeerLookupResult(True, skipped_unreadable_processes=1)


def test_unreadable_fd_and_cgroup_never_qualify_for_skip(
    proc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder(proc, 202, SESSION)
    deny_descriptor_directory(proc, monkeypatch)
    original = Path.read_text

    def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == proc / "202/cgroup":
            raise PermissionError("synthetic unreadable cgroup")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert not calls.resolve_loopback_peer(PEER, SERVER, proc=proc).is_owner


def test_skipped_outside_process_cannot_replace_a_readable_holder(
    proc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder(proc, 202, SESSION)
    (proc / "101/fd/4").unlink()
    deny_descriptor_directory(proc, monkeypatch)
    result = calls.resolve_loopback_peer(PEER, SERVER, proc=proc)
    assert result == calls.PeerLookupResult(False, skipped_unreadable_processes=1)


def test_unreadable_outside_fd_link_is_skipped_and_counted_once(
    proc: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = holder(proc, 202, SESSION)
    (descriptor.parent / "5").symlink_to("socket:[99999]")
    original = os.readlink

    def readlink(path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path).parent == descriptor.parent:
            raise PermissionError("synthetic unreadable fd link")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", readlink)
    result = calls.resolve_loopback_peer(PEER, SERVER, proc=proc)
    assert result == calls.PeerLookupResult(True, skipped_unreadable_processes=1)


def request_scope(kind: str = "http") -> dict[str, Any]:
    return {
        "type": kind,
        "method": "GET",
        "path": "/api/meta",
        "client": PEER,
        "server": SERVER,
        "headers": [],
    }


async def invoke(scope: dict[str, Any], *, enabled: bool) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "forwarded"})

    async def receive() -> dict[str, Any]:
        raise AssertionError("admission must not read a request body")

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await calls.UnidentifiedAgentCallsMiddleware(downstream, enabled=enabled)(scope, receive, send)
    return messages


def test_setting_absent_or_yaml_only_never_looks_up_or_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = tmp_path / "config.yaml"
    configuration.write_text("refuse_unidentified_agent_calls: true\n")
    config = load_config(str(configuration), env={})
    assert config.refuse_unidentified_agent_calls is False

    def unexpected(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("disabled rule must never inspect proc")

    monkeypatch.setattr(calls, "resolve_loopback_peer", unexpected)
    for kind in ("http", "websocket"):
        sent = asyncio.run(
            invoke(request_scope(kind), enabled=config.refuse_unidentified_agent_calls)
        )
        assert sent == [{"type": "forwarded"}]


@pytest.mark.parametrize(
    "headers",
    [
        [(b"x-plan-actor", b"unattributed")],
        [(b"X-Plan-Ticket-ID", b"t_worker")],
        [(b"x-plan-manager-id", b"manager")],
    ],
)
def test_any_identity_skips_lookup(
    monkeypatch: pytest.MonkeyPatch, headers: list[tuple[bytes, bytes]]
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("identity calls must bypass proc")

    monkeypatch.setattr(calls, "resolve_loopback_peer", unexpected)
    scope = request_scope()
    scope["headers"] = headers
    assert asyncio.run(invoke(scope, enabled=True)) == [{"type": "forwarded"}]


def test_trusted_owner_skips_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("trusted owner calls must bypass proc")

    monkeypatch.setattr(calls, "resolve_loopback_peer", unexpected)
    scope = request_scope()
    scope[AUTHENTICATED_OWNER_SCOPE_KEY] = True
    assert asyncio.run(invoke(scope, enabled=True)) == [{"type": "forwarded"}]


def test_blank_identity_headers_do_not_bypass_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calls, "resolve_loopback_peer", lambda *args: calls.PeerLookupResult(False))
    scope = request_scope()
    scope["headers"] = [
        (b"x-plan-actor", b"\xa0"),
        (b"x-plan-ticket-id", b" \t"),
        (b"x-plan-manager-id", b" "),
    ]
    sent = asyncio.run(invoke(scope, enabled=True))
    assert sent[0]["status"] == 403


def test_unidentified_lookup_runs_off_event_loop_and_can_admit_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = threading.get_ident()

    def resolver(*args: Any, **kwargs: Any) -> calls.PeerLookupResult:
        assert threading.get_ident() != thread
        return calls.PeerLookupResult(True)

    monkeypatch.setattr(calls, "resolve_loopback_peer", resolver)
    assert asyncio.run(invoke(request_scope(), enabled=True)) == [{"type": "forwarded"}]


def test_unidentified_http_and_websocket_refusal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        calls, "resolve_loopback_peer", lambda *args: calls.PeerLookupResult(False, 2)
    )
    caplog.set_level(logging.DEBUG, logger=calls.__name__)
    http = asyncio.run(invoke(request_scope(), enabled=True))
    assert http[0]["status"] == 403
    error = json.loads(http[1]["body"])["error"]
    assert error["code"] == "agent_forbidden"
    assert "PLAN_ACTOR" in error["message"]
    assert error["detail"] == {"skipped_unreadable_processes": 2}
    assert any(
        getattr(record, "skipped_unreadable_processes", None) == 2 for record in caplog.records
    )
    websocket = asyncio.run(invoke(request_scope("websocket"), enabled=True))
    assert websocket[0]["type"] == "websocket.close"
    assert websocket[0]["code"] == 1008
    assert "PLAN_ACTOR" in websocket[0]["reason"]


def test_live_unit_activates_the_rule() -> None:
    root = Path(__file__).resolve().parents[2]
    unit = (root / "ops/panels-environments/panels-live.service").read_text()
    assert f"Environment={SETTING}=1" in unit.splitlines()
