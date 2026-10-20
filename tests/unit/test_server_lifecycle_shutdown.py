"""The guard must fail closed without losing ordinary development cleanup."""

from __future__ import annotations

import os
import signal
from dataclasses import replace
from pathlib import Path

import pytest

from planner.server_lifecycle.contracts import ProcessIdentity, ShutdownPolicy
from planner.server_lifecycle.logic.shutdown import decide_shutdown, select_shutdown_policy
from planner.server_lifecycle.process_identity import read_process_identity
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease, ServerSupervisor

MANAGER = ProcessIdentity(1168, 12345, "/usr/lib/systemd/systemd", 8, 100)


def test_launch_policy_preserves_ordinary_cleanup_and_guards_unknown_parents() -> None:
    ordinary = replace(MANAGER, executable_path="/usr/bin/bash")
    assert select_shutdown_policy(1168, ordinary, parent_is_systemd=False).mode == "dogfood"
    assert select_shutdown_policy(1168, None, parent_is_systemd=False) == ShutdownPolicy(
        "guarded", 1168, None
    )
    assert select_shutdown_policy(1168, MANAGER, parent_is_systemd=True) == ShutdownPolicy(
        "guarded", 1168, MANAGER
    )
    # A verified systemd PID 1 at launch is distinct from later reparenting.
    init = replace(MANAGER, pid=1)
    assert select_shutdown_policy(1, init, parent_is_systemd=True).authorized_manager == init


@pytest.mark.parametrize(
    ("sender", "parent", "identity", "reason"),
    [
        (None, 1168, MANAGER, "sender identity is unavailable"),
        (0, 1168, MANAGER, "sender identity is unavailable"),
        (55, 1168, MANAGER, "sender is not"),
        (1168, 1, MANAGER, "parent changed"),
        (1168, 1168, None, "manager identity is unavailable"),
        (1168, 1168, replace(MANAGER, start_time=999), "identity changed"),
        (1168, 1168, replace(MANAGER, executable_inode=999), "identity changed"),
    ],
)
def test_guard_refuses_uncertain_or_wrong_identity(
    sender: int | None, parent: int, identity: ProcessIdentity | None, reason: str
) -> None:
    decision = decide_shutdown(
        ShutdownPolicy("guarded", 1168, MANAGER),
        sender_pid=sender,
        current_parent_pid=parent,
        manager_identity=identity,
    )
    assert not decision.accepted
    assert reason in decision.reason


def test_same_identity_manager_reexec_retains_authority_and_unknown_never_gains_it() -> None:
    assert decide_shutdown(
        ShutdownPolicy("guarded", 1168, MANAGER),
        sender_pid=1168,
        current_parent_pid=1168,
        manager_identity=replace(MANAGER),
    ).accepted
    assert not decide_shutdown(
        ShutdownPolicy("guarded", 44, None),
        sender_pid=1,
        current_parent_pid=1,
        manager_identity=replace(MANAGER, pid=1),
    ).accepted
    assert decide_shutdown(
        ShutdownPolicy("dogfood", 44, None),
        sender_pid=None,
        current_parent_pid=1,
        manager_identity=None,
    ).accepted


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="Linux proc identity")
def test_process_identity_reads_kernel_start_time_and_executable() -> None:
    identity = read_process_identity(os.getpid())
    assert identity is not None
    assert identity.pid == os.getpid()
    assert identity.start_time > 0
    executable = Path("/proc/self/exe")
    assert identity.executable_path == os.readlink(executable)
    assert identity.executable_inode == executable.stat().st_ino
    assert read_process_identity(-1) is None


def test_refusal_is_visible_and_names_emergency_exact_pid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = ServerSupervisor(
        launch_root=tmp_path,
        interpreter="unused",
        environ={},
        control_socket_path=tmp_path / "control.sock",
        lease=PortScopedServerLifecycleLease(tmp_path / "lease", 9999),
    )
    supervisor._shutdown_policy = ShutdownPolicy("guarded", 1168, None)
    monkeypatch.setattr("planner.server_lifecycle.supervisor.os.getppid", lambda: 1168)
    supervisor._dispatch_signal(signal.SIGTERM, 71)
    first = capsys.readouterr().err
    assert "refused SIGTERM from sender PID 71" in first
    assert "launch parent identity was not verifiable" in first
    assert f"SIGKILL to exact supervisor PID {os.getpid()}" in first
    assert not supervisor._operator_shutdown_requested
    supervisor._dispatch_signal(signal.SIGINT, None)
    assert "refused SIGINT from unknown sender" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["/usr/lib/systemd/systemd (deleted)", "/tmp/systemd"])
def test_ambiguous_manager_executable_never_becomes_dogfood(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import planner.server_lifecycle.process_identity as identities

    monkeypatch.setattr(identities, "read_authenticated_manager_identity", lambda _pid: None)

    monkeypatch.setattr("planner.server_lifecycle.process_identity.os.getppid", lambda: 1168)
    monkeypatch.setattr(
        identities, "read_process_identity", lambda _pid: replace(MANAGER, executable_path=path)
    )
    assert identities.capture_shutdown_policy() == ShutdownPolicy("guarded", 1168, None)


def test_reparent_during_capture_and_unverified_pid_one_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import planner.server_lifecycle.process_identity as identities

    monkeypatch.setattr(identities, "read_authenticated_manager_identity", lambda _pid: None)

    parents = iter([1168, 1])
    monkeypatch.setattr(
        "planner.server_lifecycle.process_identity.os.getppid", lambda: next(parents)
    )
    monkeypatch.setattr(identities, "read_process_identity", lambda _pid: MANAGER)
    assert identities.capture_shutdown_policy().authorized_manager is None
    monkeypatch.setattr("planner.server_lifecycle.process_identity.os.getppid", lambda: 1)
    monkeypatch.setattr(
        identities,
        "read_process_identity",
        lambda _pid: replace(MANAGER, pid=1, executable_path="/usr/bin/unknown-init"),
    )
    assert identities.capture_shutdown_policy() == ShutdownPolicy("guarded", 1, None)


def test_signal_setup_refuses_preexisting_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    from planner.server_lifecycle.contracts import ServerLifecycleError
    from planner.server_lifecycle.signals import SupervisorSignals

    receiver = SupervisorSignals()
    monkeypatch.setattr("planner.server_lifecycle.signals.threading.active_count", lambda: 2)
    with pytest.raises(ServerLifecycleError, match="single main thread"):
        receiver.start()
    assert receiver.read_fd == -1


@pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self/stat").exists(), reason="Linux siginfo"
)
def test_waiter_keeps_sender_and_discards_pending_termination_before_mask_restore() -> None:
    import subprocess
    import sys
    import textwrap

    # Signals target only this recorded subprocess itself, never a pattern or pytest PID.
    proof = textwrap.dedent("""
        import os, signal, threading, time
        from planner.server_lifecycle.signals import SupervisorSignals, WATCHED_SIGNALS
        before = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        handlers = {item: signal.getsignal(item) for item in WATCHED_SIGNALS}
        receiver = SupervisorSignals()
        receiver.start()
        pid = os.getpid()
        os.kill(pid, signal.SIGTERM)
        events = []
        deadline = time.monotonic() + 2
        while not events and time.monotonic() < deadline:
            events.extend(receiver.drain())
            time.sleep(0.01)
        assert (signal.SIGTERM, pid) in events, events
        receiver._stop.set()
        receiver._thread.join()
        # This signal is kernel-pending after the waiter exits. stop must consume it
        # rather than unblock the default SIGTERM disposition and kill this process.
        assert signal.SIGTERM in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        os.kill(pid, signal.SIGTERM)
        assert (signal.SIGTERM, pid) in receiver.stop()
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before
        assert {item: signal.getsignal(item) for item in WATCHED_SIGNALS} == handlers
        assert not receiver._thread.is_alive()
        assert threading.active_count() == 1
        print('sender, pending teardown, masks, handlers, thread verified')
    """)
    result = subprocess.run(
        [sys.executable, "-c", proof],
        env={key: value for key, value in os.environ.items() if not key.startswith("PLAN_")},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert "pending teardown, masks, handlers, thread verified" in result.stdout


def test_dbus_credentials_preserve_manager_identity_without_fabricated_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import subprocess

    import planner.server_lifecycle.process_identity as identities

    calls: list[tuple[list[str], dict[str, object]]] = []

    def busctl(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "type": "a{sv}",
                    "data": [
                        {
                            "ProcessID": {"type": "u", "data": os.getpid()},
                            "UnixUserID": {"type": "u", "data": os.getuid()},
                        }
                    ],
                }
            ),
        )

    monkeypatch.setattr("planner.server_lifecycle.process_identity.subprocess.run", busctl)
    manager = identities.read_authenticated_manager_identity(os.getpid())
    assert manager is not None
    assert manager.identity_source == "dbus_manager"
    assert manager.executable_path is None
    assert manager.executable_device is None
    assert manager.executable_inode is None
    assert manager.manager_bus == "user"
    assert manager.manager_uid == os.getuid()
    assert identities.revalidate_manager_identity(manager) == manager
    assert calls[0][0][0] == "/usr/bin/busctl"
    assert f"--address=unix:path=/run/user/{os.getuid()}/bus" in calls[0][0]
    assert "GetConnectionCredentials" in calls[0][0]
    assert calls[0][1]["timeout"] == 1.0
    assert "shell" not in calls[0][1]


@pytest.mark.parametrize("failure", ["pid", "uid", "timeout", "malformed", "replacement"])
def test_dbus_unavailable_or_changed_identity_fails_closed(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import subprocess

    import planner.server_lifecycle.process_identity as identities

    def busctl(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        data = json.dumps(
            {
                "type": "a{sv}",
                "data": [
                    {
                        "ProcessID": {"type": "u", "data": os.getpid() + (failure == "pid")},
                        "UnixUserID": {"type": "u", "data": os.getuid() + (failure == "uid")},
                    }
                ],
            }
        )
        return subprocess.CompletedProcess(command, 0, "{}" if failure == "malformed" else data)

    monkeypatch.setattr("planner.server_lifecycle.process_identity.subprocess.run", busctl)
    if failure == "replacement":
        start_times = iter([123, 124])

        def stat(_path: Path) -> str:
            return "0 (manager) " + " ".join(["S"] + ["0"] * 18 + [str(next(start_times))])

        monkeypatch.setattr(Path, "read_text", stat)
    assert identities.read_authenticated_manager_identity(os.getpid()) is None
