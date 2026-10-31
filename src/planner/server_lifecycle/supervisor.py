"""Stable foreground supervisor for one replaceable Panels application process."""

from __future__ import annotations

import errno
import fcntl
import os
import selectors
import signal
import socket
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from planner.core.config import load_config
from planner.server_lifecycle.contracts import (
    SERVER_CONTROL_PROTOCOL_VERSION,
    ServerControlResponse,
    ServerLifecycleAlreadyOwnedError,
    ServerLifecycleError,
    ServerRestartProtocolError,
)
from planner.server_lifecycle.control import (
    decode_server_control_request,
    encode_server_control_response,
    receive_server_control_message,
    resolve_server_control_socket_path,
    resolve_server_lifecycle_lease_path,
)
from planner.server_lifecycle.logic.shutdown import decide_shutdown
from planner.server_lifecycle.process_identity import (
    capture_shutdown_policy,
    revalidate_manager_identity,
)
from planner.server_lifecycle.signals import SupervisorSignals

_CONTROL_SOCKET_DRAIN_BYTES: Final = 4096


class _ControlRequestSuperseded(RuntimeError):
    """Operator shutdown or child exit took priority over an incomplete request."""


def resolve_planner_launch_root() -> Path:
    """Resolve the checkout or application that owns the imported package."""
    module_path = Path(__file__).resolve()
    if module_path.parts[-4:] == ("src", "planner", "server_lifecycle", "supervisor.py"):
        return module_path.parents[3]

    application_identity = os.environ.get("PLAN_APP_ROOT", "")
    if application_identity and Path(application_identity).is_absolute():
        application_root = Path(application_identity).resolve()
        virtualenv_root = (application_root / ".venv").resolve()
        if (
            virtualenv_root != application_root
            and virtualenv_root.is_relative_to(application_root)
            and module_path.is_relative_to(virtualenv_root)
        ):
            package_parts = module_path.relative_to(virtualenv_root).parts
            if (
                len(package_parts) == 6
                and package_parts[0] == "lib"
                and package_parts[1].startswith("python")
                and package_parts[2:]
                == ("site-packages", "planner", "server_lifecycle", "supervisor.py")
            ):
                return application_root

    raise ServerLifecycleError(
        f"Cannot resolve the application that owns Panels module {module_path} "
        f"with PLAN_APP_ROOT={application_identity!r}. "
        "Use the owning application's bin/panels-launcher serve to start Panels, "
        "or bin/panels restart to restart it."
    )


class PortScopedServerLifecycleLease:
    """Retained advisory lock proving one supervisor owns a local port."""

    def __init__(self, path: Path, port: int) -> None:
        self._path = path
        self._port = port
        self._descriptor: int | None = None

    def acquire(self) -> None:
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ServerLifecycleAlreadyOwnedError(
                    f"Port {self._port} already has a Panels supervisor."
                ) from exc
            raise ServerLifecycleError(f"Could not acquire Panels lifecycle lease: {exc}") from exc
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor is None:
            return
        fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        os.close(self._descriptor)
        self._descriptor = None


class ServerSupervisor:
    """Own one application child and serialize its controlled replacement."""

    def __init__(
        self,
        *,
        launch_root: Path,
        interpreter: str,
        environ: Mapping[str, str],
        control_socket_path: Path,
        lease: PortScopedServerLifecycleLease,
    ) -> None:
        self._launch_root = launch_root
        self._interpreter = interpreter
        self._environ = dict(environ)
        self._control_socket_path = control_socket_path
        self._lease = lease
        self._application_child: subprocess.Popen[bytes] | None = None
        self._control_listener: socket.socket | None = None
        self._operator_shutdown_requested = False
        self._shutdown_policy = capture_shutdown_policy()
        self._signals = SupervisorSignals()

    def run(self) -> int:
        self._lease.acquire()
        try:
            self._bind_control_socket()
            return self._run_lifecycle_loop()
        finally:
            self._cleanup_control_socket()
            self._lease.release()

    def _run_lifecycle_loop(self) -> int:
        selector = selectors.DefaultSelector()
        try:
            self._signals.start()
        except BaseException:
            selector.close()
            raise
        signal_read = self._signals.read_fd
        assert self._control_listener is not None
        try:
            selector.register(self._control_listener, selectors.EVENT_READ, "control")
            selector.register(signal_read, selectors.EVENT_READ, "signal")
            self._spawn_application_child()
            while True:
                if self._operator_shutdown_requested:
                    self._stop_application_child()
                    return 0
                if self._application_child_has_exited():
                    return 0 if self._operator_shutdown_requested else 1

                events = selector.select()
                if any(key.data == "signal" for key, _ in events):
                    self._drain_signal_pipe(signal_read)
                if self._operator_shutdown_requested:
                    self._stop_application_child()
                    return 0
                if self._application_child_has_exited():
                    return 0 if self._operator_shutdown_requested else 1

                restart_requested = False
                for key, _ in events:
                    if key.data == "control":
                        restart_requested = (
                            self._accept_control_request(signal_read) or restart_requested
                        )

                if self._operator_shutdown_requested:
                    self._stop_application_child()
                    return 0
                if self._application_child_has_exited():
                    return 0 if self._operator_shutdown_requested else 1
                if restart_requested:
                    self._stop_application_child()
                    if self._operator_shutdown_requested:
                        return 0
                    self._spawn_application_child()
        finally:
            selector.close()
            try:
                child = self._application_child
                if child is not None and child.poll() is None:
                    self._stop_application_child()
            finally:
                for signum, sender_pid in self._signals.stop():
                    self._dispatch_signal(signum, sender_pid)

    def _bind_control_socket(self) -> None:
        self._control_socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._control_socket_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self._control_socket_path))
            listener.listen()
            listener.setblocking(False)
        except BaseException:
            listener.close()
            self._control_socket_path.unlink(missing_ok=True)
            raise
        self._control_listener = listener

    def _cleanup_control_socket(self) -> None:
        if self._control_listener is not None:
            self._control_listener.close()
            self._control_listener = None
        self._control_socket_path.unlink(missing_ok=True)

    def _spawn_application_child(self) -> None:
        environment = dict(self._environ)
        environment["PLAN_SERVER_CONTROL_SOCKET"] = str(self._control_socket_path)
        original_mask = sorted(int(item) for item in self._signals.child_signal_mask)
        try:
            listener_fd = environment.get("PLAN_SERVER_LISTENER_FD")
            pass_fds = (int(listener_fd),) if listener_fd is not None else ()
            self._application_child = subprocess.Popen(
                [
                    self._interpreter,
                    "-c",
                    "import os,signal,sys; "
                    f"signal.pthread_sigmask(signal.SIG_SETMASK, {original_mask!r}); "
                    "os.execv(sys.executable, [sys.executable, '-m', "
                    "'planner.server_lifecycle.application'])",
                ],
                cwd=self._launch_root,
                env=environment,
                start_new_session=True,
                pass_fds=pass_fds,
            )
        except OSError as exc:
            raise ServerLifecycleError(f"Could not start the Panels application: {exc}") from exc

    def _accept_control_request(self, signal_read: int) -> bool:
        assert self._control_listener is not None
        try:
            connection, _ = self._control_listener.accept()
        except BlockingIOError:
            return False
        with connection:
            connection.setblocking(True)
            try:
                request = self._receive_control_request(connection, signal_read)
                if request is None:
                    return False
                decode_server_control_request(request)
            except (OSError, ServerRestartProtocolError):
                return False
            if self._operator_shutdown_requested or self._application_child_has_exited():
                return False
            connection.setblocking(True)
            response = ServerControlResponse(
                version=SERVER_CONTROL_PROTOCOL_VERSION,
                status="accepted",
            )
            try:
                connection.sendall(encode_server_control_response(response))
            except OSError:
                return False
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                return False
            return self._wait_for_accepted_client_close(connection, signal_read)

    def _wait_for_accepted_client_close(
        self,
        connection: socket.socket,
        signal_read: int,
    ) -> bool:
        try:
            while self._receive_control_bytes_until_lifecycle_change(
                connection,
                signal_read,
                _CONTROL_SOCKET_DRAIN_BYTES,
            ):
                pass
        except _ControlRequestSuperseded:
            return False
        return True

    def _receive_control_request(
        self,
        connection: socket.socket,
        signal_read: int,
    ) -> bytes | None:
        def receive(maximum_bytes: int) -> bytes:
            return self._receive_control_bytes_until_lifecycle_change(
                connection,
                signal_read,
                maximum_bytes,
            )

        try:
            return receive_server_control_message(receive)
        except _ControlRequestSuperseded:
            return None

    def _receive_control_bytes_until_lifecycle_change(
        self,
        connection: socket.socket,
        signal_read: int,
        maximum_bytes: int,
    ) -> bytes:
        connection.setblocking(False)
        selector = selectors.DefaultSelector()
        selector.register(connection, selectors.EVENT_READ, "client")
        selector.register(signal_read, selectors.EVENT_READ, "signal")
        try:
            while True:
                events = selector.select()
                if any(key.data == "signal" for key, _ in events):
                    self._drain_signal_pipe(signal_read)
                # Lifecycle changes outrank client readiness within one selector batch.
                if self._operator_shutdown_requested or self._application_child_has_exited():
                    raise _ControlRequestSuperseded
                for key, _ in events:
                    if key.data == "client":
                        try:
                            return connection.recv(maximum_bytes)
                        except BlockingIOError:
                            continue
        finally:
            selector.close()

    def _stop_application_child(self) -> None:
        child = self._application_child
        if child is None:
            return
        if child.poll() is None:
            child.terminate()
        while True:
            try:
                child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                self._drain_signal_pipe(self._signals.read_fd)
        self._drain_signal_pipe(self._signals.read_fd)
        self._application_child = None

    def _application_child_has_exited(self) -> bool:
        child = self._application_child
        if child is None:
            return False
        if child.poll() is not None:
            self._drain_signal_pipe(self._signals.read_fd)
            return True
        # Linux can expose a zombie before waitpid reports it after the supervisor resumes.
        try:
            process_state = Path(f"/proc/{child.pid}/stat").read_text().rpartition(") ")[2]
        except OSError:
            return False
        if process_state.startswith("Z "):
            self._drain_signal_pipe(self._signals.read_fd)
            return True
        return False

    def _dispatch_signal(self, signum: int, sender_pid: int | None) -> None:
        if signum not in {signal.SIGINT, signal.SIGTERM}:
            return
        policy = self._shutdown_policy
        manager = policy.authorized_manager
        current_parent_pid = os.getppid()
        manager_identity = None
        if (
            manager is not None
            and sender_pid == manager.pid
            and current_parent_pid == policy.launch_parent_pid
        ):
            manager_identity = revalidate_manager_identity(manager)
            current_parent_pid = os.getppid()
        decision = decide_shutdown(
            policy,
            sender_pid=sender_pid,
            current_parent_pid=current_parent_pid,
            manager_identity=manager_identity,
        )
        if decision.accepted:
            self._operator_shutdown_requested = True
            return
        sender = f"sender PID {sender_pid}" if sender_pid and sender_pid > 0 else "unknown sender"
        print(
            f"Panels supervisor refused {signal.Signals(signum).name} from {sender}: "
            f"{decision.reason}. To force shutdown, send SIGKILL "
            f"to exact supervisor PID {os.getpid()}.",
            file=sys.stderr,
            flush=True,
        )

    def _drain_signal_pipe(self, _signal_read: int) -> None:
        for signum, sender_pid in self._signals.drain():
            self._dispatch_signal(signum, sender_pid)


def run_server_supervisor() -> int:
    """Capture launch identity once and run the foreground supervisor."""
    launch_root = resolve_planner_launch_root()
    interpreter = sys.executable
    environ = dict(os.environ)
    config = load_config(
        os.environ.get("PLAN_CONFIG_PATH", str(launch_root / "config.yaml")), environ
    )
    control_socket_path = resolve_server_control_socket_path(
        config.port,
        environ,
        launch_root,
    )
    explicit_lease_path = environ.get("PLAN_SERVER_LIFECYCLE_LEASE_PATH")
    lease = PortScopedServerLifecycleLease(
        Path(explicit_lease_path).resolve()
        if explicit_lease_path
        else resolve_server_lifecycle_lease_path(config.port),
        config.port,
    )
    supervisor = ServerSupervisor(
        launch_root=launch_root,
        interpreter=interpreter,
        environ=environ,
        control_socket_path=control_socket_path,
        lease=lease,
    )
    return supervisor.run()
