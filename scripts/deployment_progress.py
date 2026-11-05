"""Foreground GitHub step shell with owned command cleanup."""
from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

POLL_INTERVAL_SECONDS = 0.05
SHUTDOWN_GRACE_SECONDS = 2.0


@dataclass(frozen=True)
class Timings:
    """Tests inject short timings through Python, never through runner environment."""

    poll: float = POLL_INTERVAL_SECONDS
    shutdown_grace: float = SHUTDOWN_GRACE_SECONDS


DEFAULT_TIMINGS = Timings()


def process_identity(pid: int) -> str | None:
    """Linux process start tick distinguishes a replaced PID from the runner parent."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (OSError, IndexError):
        return None


def _enable_child_reaping() -> None:
    # Adopt orphaned grandchildren so shutdown reaps descendants, even after Bash exits.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "could not enable child reaping")


def _foreign_child_is_unsignalable(pid: int, identity: str) -> bool:
    """Only a stable, proved foreign-UID child can remain under runner ownership."""
    if sys.platform != "linux":
        raise RuntimeError("Deployment descendant ownership requires Linux pidfds.")
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
        uids = [int(value) for value in uid_line.split()[1:4]]
        if len(uids) != 3 or {os.getuid(), os.geteuid()}.intersection(uids):
            return False
        descriptor = os.pidfd_open(pid)
    except (OSError, StopIteration, ValueError):
        return False
    try:
        if process_identity(pid) != identity or pid not in _direct_children():
            return False
        try:
            signal.pidfd_send_signal(descriptor, 0)
        except PermissionError:
            print(
                f"Deployment descendant {pid} belongs to another user and refuses signals; "
                "cleanup remains with the runner or system owner.",
                file=sys.stderr, flush=True,
            )
            return True
        except ProcessLookupError:
            return False
        return False
    finally:
        os.close(descriptor)


def _shutdown(process: subprocess.Popen[bytes], timings: Timings) -> None:
    # The group belongs to this exact child (start_new_session), never the live service.
    permission_denied = False
    for shutdown_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, shutdown_signal)
        except ProcessLookupError:
            break
        except PermissionError:
            # No member accepts this signal. Detached same-user descendants still
            # need the exact-child cleanup below, even if privileged peers remain.
            permission_denied = True
            break
        deadline = time.monotonic() + timings.shutdown_grace
        while time.monotonic() < deadline:
            process.poll()
            # Popen must reap its leader before a group wait can reap descendants.
            # Otherwise a killed writer can appear to have exited successfully.
            while process.returncode is not None:
                try:
                    if os.waitpid(-process.pid, os.WNOHANG)[0] == 0:
                        break
                except ChildProcessError:
                    break
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            except PermissionError:
                break
            time.sleep(timings.poll)
    if process.poll() is None:
        identity = process_identity(process.pid)
        if identity is not None and _foreign_child_is_unsignalable(process.pid, identity):
            return
        if permission_denied:
            raise PermissionError("owned command refuses signals without proved foreign ownership")
    process.wait()
    # Only adopted children in this owned process group are eligible for reaping.
    while True:
        try:
            if os.waitpid(-process.pid, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break


def _direct_children() -> list[int]:
    # This dedicated supervisor starts only the owned command.
    # As a subreaper, its other direct children can only be their orphaned descendants.
    path = Path(f"/proc/self/task/{os.getpid()}/children")
    return [int(value) for value in path.read_text().split()]


def _reap_exited_adopted_children(excluded: set[int]) -> None:
    # Popen owns the command status. Only adopted children belong here.
    for pid in _direct_children():
        if pid in excluded:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass


def _signal_adopted_child(pid: int, identity: str, signum: signal.Signals) -> None:
    if sys.platform != "linux":
        raise RuntimeError("Deployment descendant signals require Linux pidfds.")
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        if process_identity(pid) == identity and pid in _direct_children():
            # A pidfd cannot signal an unrelated process if this PID is later reused.
            signal.pidfd_send_signal(descriptor, signum)
    except ProcessLookupError:
        pass
    finally:
        os.close(descriptor)


def _shutdown_adopted_children(timings: Timings) -> None:
    """Reap descendants that made new sessions, without touching service-manager apps."""
    grace_deadline = time.monotonic() + timings.shutdown_grace
    final_deadline = grace_deadline + max(1.0, timings.shutdown_grace)
    notified: set[tuple[int, str]] = set()
    while True:
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        children = _direct_children()
        if not children:
            return
        force = time.monotonic() >= grace_deadline
        managed_children = False
        for pid in children:
            identity = process_identity(pid)
            if identity is None:
                continue
            if _foreign_child_is_unsignalable(pid, identity):
                continue
            managed_children = True
            if force or (pid, identity) not in notified:
                _signal_adopted_child(pid, identity, signal.SIGKILL if force else signal.SIGTERM)
                notified.add((pid, identity))
        if not managed_children:
            return
        if time.monotonic() >= final_deadline:
            raise RuntimeError("owned deployment descendants did not stop before shutdown deadline")
        # Killing an adopted parent can adopt another detached descendant: enumerate again.
        time.sleep(timings.poll)


def supervise(script: Path, *, timings: Timings = DEFAULT_TIMINGS) -> int:
    """Run Bash and preserve command failures and cancellation."""
    if os.environ.get("PANELS_DEPLOYMENT_PROGRESS_ACTIVE") != "true":
        # Bootstrap keeps the runner's normal shell ownership.
        os.execvp("bash", ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)])
    parent_pid = os.getppid()
    parent_identity = process_identity(parent_pid)
    cancelled = 0

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal cancelled
        cancelled = signum

    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    command: subprocess.Popen[bytes] | None = None
    def reap_adopted_children() -> None:
        _reap_exited_adopted_children({command.pid} if command is not None else set())

    def interrupted() -> bool:
        return bool(
            cancelled or parent_identity is None or os.getppid() != parent_pid
            or process_identity(parent_pid) != parent_identity
        )

    try:
        _enable_child_reaping()
        if interrupted():
            raise InterruptedError
        command = subprocess.Popen(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            start_new_session=True,
        )
        while command.poll() is None:
            reap_adopted_children()
            if interrupted():
                raise InterruptedError
            time.sleep(timings.poll)
        if interrupted():
            raise InterruptedError
        status = command.wait()
        return (128 - status if status < 0 else status)
    except InterruptedError:
        return 128 + (cancelled or signal.SIGTERM)
    finally:
        if command is not None:
            _shutdown(command, timings)
        _shutdown_adopted_children(timings)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path)
    arguments = parser.parse_args()
    return supervise(arguments.script)


if __name__ == "__main__":
    raise SystemExit(main())
