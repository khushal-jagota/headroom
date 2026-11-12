"""One hosted proof, reused private fixture: HTTP grace, signal exit, failure and hard bound."""

from __future__ import annotations

import ctypes
import fcntl
import http.client
import json
import os
import selectors
import shutil
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.server_fixtures import _start_server, _stop_server

ROOT = Path(__file__).resolve().parents[3]


def _state(root: Path, name: str) -> Any:
    path = root / f"{name}.json"
    until = time.monotonic() + 20
    while not path.exists():
        assert time.monotonic() < until, f"Missing owned-host state: {name}"
        time.sleep(0.01)
    return json.loads(path.read_text())


def _owner(path: Path, held: bool) -> None:
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            assert held
        else:
            assert not held


def _native_identity(pid: int) -> tuple[int, int]:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), int(fields[19])


def _pin_native(identity: dict[str, int], parent: int) -> int:
    descriptor = os.pidfd_open(identity["pid"])
    try:
        # Pin FIRST, then validate the recorded birth/parent while APP is held idle.
        assert identity["parent"] == parent
        assert _native_identity(identity["pid"]) == (parent, identity["start_time"])
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _native_dead(handles: dict[int, int]) -> None:
    with selectors.DefaultSelector() as deaths:
        for descriptor in handles.values():
            deaths.register(descriptor, selectors.EVENT_READ)
        until = time.monotonic() + 10
        while deaths.get_map() and time.monotonic() < until:
            for key, _mask in deaths.select(timeout=max(0, until - time.monotonic())):
                deaths.unregister(key.fd)
        assert not deaths.get_map(), "Hard stop did not kill both owned group members"


def _reap_native(handles: dict[int, int]) -> None:
    # APP was already waited. Wait BOTH pinned exits before accepting ECHILD:
    # the leader's exit reparents its descendant to this subreaper first.
    _native_dead(handles)
    remaining = set(handles)
    until = time.monotonic() + 10
    while remaining and time.monotonic() < until:
        for pid in tuple(remaining):
            try:
                found, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                remaining.remove(pid)  # Already reaped by its original parent.
            else:
                if found:
                    remaining.remove(pid)
        if remaining:
            time.sleep(0.01)
    assert not remaining, f"Owned native children were not reaped: {remaining}"


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux",
    reason="Owned HTTP/signal lifecycle proof runs on hosted CI only",
)
def test_unit21_stop_retains_disconnected_work_and_failed_cleanup(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    began = time.perf_counter()
    selected_node = shutil.which("node")
    assert selected_node, "The selected Node executable is required"
    # Adopt only this fixture's orphans so its exact recorded handles can be reaped.
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0

    def restore_subreaper() -> None:
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0

    request.addfinalizer(restore_subreaper)
    receipts: dict[str, Any] = {}
    for mode in ("http", "failure", "hard"):
        mode_started = time.perf_counter()
        runtime = tmp_path / mode
        entry = tmp_path / f"{mode}-proof.mjs"
        companion = ROOT / "server/lifecycle/testing/stop.ts"
        entry.write_text(
            f"import{{stop_host_proof}}from{json.dumps(companion.as_uri())};"
            f"await stop_host_proof({json.dumps({'root': str(runtime), 'mode': mode})});\n"
        )
        server = _start_server(
            runtime,
            server_module="tests.compare.ts_launcher",
            trusted_ingress_env={
                "PANELS_COMPARE_TS_ENTRY": str(entry),
                "PATH": str(Path(selected_node).parent) + os.pathsep + os.defpath,
                "PLAN_SERVER_LIFECYCLE_LEASE_PATH": str(runtime / "run/lifecycle.lock"),
            },
        )
        clients: list[socket.socket] = []
        native_handles: dict[int, int] = {}
        owner = Path(str(server.db_path) + ".conversation-owner.lock")

        def get(
            route: str, port: int = server.port, owned: list[socket.socket] = clients
        ) -> socket.socket:
            client = socket.create_connection(("127.0.0.1", port), timeout=20)
            owned.append(client)
            client.sendall(f"GET {route} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
            return client

        try:
            if mode == "http":
                held = get("/proof/held")
                tail = get("/proof/tail")
                slow = get("/proof/slow")
                late = get("/proof/late")
                events = get("/api/changes")
                event_response = http.client.HTTPResponse(events)
                event_response.begin()
                assert event_response.status == 200
                for name in ("handler", "iterator", "slow", "late"):
                    _state(runtime, f"{name}-entered")
                held.close()
                tail.close()
                _state(runtime, "handler-disconnected")
                _state(runtime, "iterator-disconnected")
                assert httpx.post(server.base + "/proof/stop", timeout=20).json() == {
                    "accepted": True
                }
                stopping = _state(runtime, "stopping")
                assert stopping["state"]["kind"] == "stopping" and not stopping["closed"]
                # The real changes stream closes while ordinary response work remains live.
                try:
                    event_response.read()
                except http.client.IncompleteRead:
                    pass  # Closing an event stream need not send a final HTTP chunk.
                (runtime / "release-slow").touch()
                response = http.client.HTTPResponse(slow)
                response.begin()
                assert response.status == 200
                assert json.loads(response.read()) == {"complete": "ordinary response during grace"}
                (runtime / "release-late").touch()
                refused = http.client.HTTPResponse(late)
                refused.begin()
                assert refused.status == 503
                refused.read()
                _owner(owner, True)
                server.proc.send_signal(signal.SIGTERM)
                _state(runtime, "first-term")
                server.proc.send_signal(signal.SIGTERM)
                (runtime / "ping").touch()
                assert _state(runtime, "pong") is True
                assert server.proc.poll() is None
                assert not (runtime / "closed.json").exists()
                for name in ("handler", "iterator"):
                    (runtime / f"release-{name}").touch()
                    assert _state(runtime, f"{name}-settled") == {"alive": 1}
                assert _state(runtime, "iterator-returned") is True
                assert _state(runtime, "closed")["kind"] == "closed"
                _state(runtime, "idle-ready")
                # This new lifetime's FIRST stop is a signal, not an earlier close call.
                server.proc.send_signal(signal.SIGTERM)
                assert server.proc.wait(timeout=20) == 0
                assert _state(runtime, "exit")["state"]["kind"] == "closed"
            elif mode == "failure":
                assert httpx.post(server.base + "/proof/stop", timeout=20).json() == {
                    "accepted": True
                }
                stopping = _state(runtime, "stopping")
                assert stopping["reentry"] and not stopping["closed"]
                assert "backend services" in stopping["state"]["pending"]
                assert _state(runtime, "backend-fenced") is True
                _owner(owner, True)
                assert not (runtime / "failed.json").exists()
                (runtime / "release-backend").touch()
                failed = _state(runtime, "failed")
                assert failed["state"]["kind"] == "closed"
                assert "video transfers" in failed["error"]
                assert server.proc.wait(timeout=20) == 1
                assert "closed" in _state(runtime, "exit")["database"]
            else:
                assert httpx.post(server.base + "/proof/native", timeout=20).json() == {
                    "accepted": True
                }
                native = _state(runtime, "owned-native")
                identities = _state(runtime, "native-identities")
                for name, parent in (("leader", server.proc.pid), ("descendant", native["pid"])):
                    identity = identities[name]
                    native_handles[identity["pid"]] = _pin_native(identity, parent)
                assert identities["leader"]["pid"] == native["pid"]
                server.proc.send_signal(signal.SIGTERM)
                _state(runtime, "first-term")
                server.proc.send_signal(signal.SIGTERM)
                _owner(owner, True)
                # Real production45 trigger. This fixture is never run on the VPS.
                assert server.proc.wait(timeout=75) == 1
                _native_dead(native_handles)
                exited = _state(runtime, "exit")
                assert exited["database"] == {"alive": 1}
                assert exited["state"]["kind"] == "stopping"
                assert "native processes and worker units" in exited["state"]["pending"]
                log = server.log_path.read_text()
                assert log.count("Shutdown grace expired;") == 1
                assert log.count("Shutdown hard bound;") == 1
                assert "native processes and worker units" in log
                assert not (runtime / "closed.json").exists()
            _owner(owner, False)
            receipts[mode] = {
                "pid": server.proc.pid,
                "exit_code": server.proc.returncode,
                "seconds": time.perf_counter() - mode_started,
                "exit": _state(runtime, "exit"),
                "native_group_dead": bool(native_handles),
            }
        finally:
            for client in clients:
                client.close()
            try:
                # Capture any remaining recorded child before APP cleanup/reparenting.
                marker = runtime / "native-identities.json"
                if mode == "hard" and marker.exists():
                    identities = json.loads(marker.read_text())
                    for name, parent in (
                        ("leader", server.proc.pid),
                        ("descendant", identities["leader"]["pid"]),
                    ):
                        identity = identities[name]
                        if identity["pid"] not in native_handles:
                            try:
                                native_handles[identity["pid"]] = _pin_native(identity, parent)
                            except ProcessLookupError:
                                pass
            finally:
                try:
                    _stop_server(server)
                finally:
                    try:
                        for descriptor in native_handles.values():
                            try:
                                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        _reap_native(native_handles)
                    finally:
                        for descriptor in native_handles.values():
                            os.close(descriptor)
    report = request.config.getoption("compare_report_dir", default=None)
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-21-stop-coherence.json").write_text(
            json.dumps(
                {
                    "seconds": time.perf_counter() - began,
                    "processes": receipts,
                    "boundary": (
                        "Existing fixture, three owned terminal processes: combined HTTP grace/"
                        "disconnect ownership plus new idle signal lifetime; "
                        "settled-failure disposal; real45 hard phase with native ownership "
                        "and no database finalization. No browser."
                    ),
                },
                indent=2,
            )
            + "\n"
        )
