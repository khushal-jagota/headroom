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
from types import FrameType
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

_CONTROL_SOCKET_DRAIN_BYTES: Final = 4096


class _ControlRequestSuperseded(RuntimeError):
    """Operator shutdown or child exit took priority over an incomplete request."""


def resolve_planner_launch_root() -> Path:
    """Resolve the repository root that owns the imported planner package."""
    return Path(__file__).resolve().parents[3]


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

    def run(self) -> int:
        self._lease.acquire()
        try:
            self._bind_control_socket()
            return self._run_lifecycle_loop()
        finally:
            self._cleanup_control_socket()
            self._lease.release()

    def _run_lifecycle_loop(self) -> int:
        signal_read, signal_write = os.pipe()
        os.set_blocking(signal_read, False)
        os.set_blocking(signal_write, False)
        previous_wakeup_fd = signal.set_wakeup_fd(signal_write)
        watched_signals = (signal.SIGINT, signal.SIGTERM, signal.SIGCHLD)
        previous_handlers = {watched: signal.getsignal(watched) for watched in watched_signals}
        signal.signal(signal.SIGINT, self._handle_operator_signal)
        signal.signal(signal.SIGTERM, self._handle_operator_signal)
        signal.signal(signal.SIGCHLD, self._handle_child_signal)
        selector = selectors.DefaultSelector()
        assert self._control_listener is not None
        selector.register(self._control_listener, selectors.EVENT_READ, "control")
        selector.register(signal_read, selectors.EVENT_READ, "signal")
        try:
            self._spawn_application_child()
            while True:
                if self._operator_shutdown_requested:
                    self._stop_application_child()
                    return 0
                if self._application_child_has_exited():
                    return 1

                events = selector.select()
                if any(key.data == "signal" for key, _ in events):
                    self._drain_signal_pipe(signal_read)
                if self._operator_shutdown_requested:
                    self._stop_application_child()
                    return 0
                if self._application_child_has_exited():
                    return 1

                restart_requested = False
                for key, _ in events:
                    if key.data == "control":
                        restart_requested = (
                            self._accept_control_request(signal_read) or restart_requested
                        )

                if self._operator_shutdown_requested:
                    self._stop_application_child()
                    return 0
                if restart_requested:
                    self._stop_application_child()
                    if self._operator_shutdown_requested:
                        return 0
                    self._spawn_application_child()
                elif self._application_child_has_exited():
                    return 1
        finally:
            selector.close()
            for watched, handler in previous_handlers.items():
                signal.signal(watched, handler)
            signal.set_wakeup_fd(previous_wakeup_fd)
            os.close(signal_read)
            os.close(signal_write)
            child = self._application_child
            if child is not None and child.poll() is None:
                self._stop_application_child()

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
        source_path = str(self._launch_root / "src")
        existing_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            source_path
            if not existing_pythonpath
            else os.pathsep.join((source_path, existing_pythonpath))
        )
        environment["PLAN_SERVER_CONTROL_SOCKET"] = str(self._control_socket_path)
        try:
            self._application_child = subprocess.Popen(
                [self._interpreter, "-m", "planner.server_lifecycle.application"],
                cwd=self._launch_root,
                env=environment,
                start_new_session=True,
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
                for key, _ in selector.select():
                    if key.data == "signal":
                        self._drain_signal_pipe(signal_read)
                        if (
                            self._operator_shutdown_requested
                            or self._application_child_has_exited()
                        ):
                            raise _ControlRequestSuperseded
                    elif key.data == "client":
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
        child.wait()
        self._application_child = None

    def _application_child_has_exited(self) -> bool:
        child = self._application_child
        return child is not None and child.poll() is not None

    def _handle_operator_signal(self, _signum: int, _frame: FrameType | None) -> None:
        self._operator_shutdown_requested = True

    @staticmethod
    def _handle_child_signal(_signum: int, _frame: FrameType | None) -> None:
        return

    @staticmethod
    def _drain_signal_pipe(signal_read: int) -> None:
        try:
            while os.read(signal_read, 4096):
                pass
        except BlockingIOError:
            return


def run_server_supervisor() -> int:
    """Capture launch identity once and run the foreground supervisor."""
    launch_root = resolve_planner_launch_root()
    interpreter = sys.executable
    environ = dict(os.environ)
    config = load_config(str(launch_root / "config.yaml"), environ)
    control_socket_path = resolve_server_control_socket_path(
        config.port,
        environ,
        launch_root,
    )
    lease = PortScopedServerLifecycleLease(
        resolve_server_lifecycle_lease_path(config.port),
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
