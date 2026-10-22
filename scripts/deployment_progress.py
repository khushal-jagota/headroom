"""Foreground GitHub step shell: freshness belongs to an active, owned command."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

RENEW_INTERVAL_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.05
SHUTDOWN_GRACE_SECONDS = 2.0
TERMINAL_PHASES = {"succeeded", "failed", "rolled_back"}
LIFECYCLE_WRITER = (
    Path(__file__).resolve().parents[1] / "src/planner/environments/deployment_lifecycle.py"
)


@dataclass(frozen=True)
class Timings:
    """Tests inject short timings through Python, never through runner environment."""

    renewal: float = RENEW_INTERVAL_SECONDS
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


def _shutdown(process: subprocess.Popen[bytes], timings: Timings) -> None:
    # The group belongs to this exact child (start_new_session), never the live service.
    for shutdown_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, shutdown_signal)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + timings.shutdown_grace
        while time.monotonic() < deadline:
            process.poll()
            while True:
                try:
                    if os.waitpid(-process.pid, os.WNOHANG)[0] == 0:
                        break
                except ChildProcessError:
                    break
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(timings.poll)
    process.wait()
    # Only adopted children in this owned process group are eligible for reaping.
    while True:
        try:
            os.waitpid(-process.pid, 0)
        except ChildProcessError:
            break


def _direct_children() -> list[int]:
    # This dedicated supervisor starts only the command and renewal subprocesses.
    # As a subreaper, its other direct children can only be their orphaned descendants.
    path = Path(f"/proc/self/task/{os.getpid()}/children")
    return [int(value) for value in path.read_text().split()]


def _signal_adopted_child(pid: int, identity: str, signum: signal.Signals) -> None:
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
        for pid in children:
            identity = process_identity(pid)
            if identity is None:
                continue
            if force or (pid, identity) not in notified:
                _signal_adopted_child(pid, identity, signal.SIGKILL if force else signal.SIGTERM)
                notified.add((pid, identity))
        if time.monotonic() >= final_deadline:
            raise RuntimeError("owned deployment descendants did not stop before shutdown deadline")
        # Killing an adopted parent can adopt another detached descendant: enumerate again.
        time.sleep(timings.poll)


def supervise(
    script: Path, *, cleanup: bool = False, timings: Timings = DEFAULT_TIMINGS,
) -> int:
    """Run Bash with bounded, interruptible renewal; preserve command failures."""
    parent_pid = os.getppid()
    parent_identity = process_identity(parent_pid)
    cancelled = 0

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal cancelled
        cancelled = signum

    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    command: subprocess.Popen[bytes] | None = None
    renewal: subprocess.Popen[bytes] | None = None

    def interrupted() -> bool:
        return bool(
            cancelled or parent_identity is None or os.getppid() != parent_pid
            or process_identity(parent_pid) != parent_identity
        )

    def renew() -> bool:
        """False means terminal evidence; failures refuse further renewals."""
        nonlocal renewal
        if interrupted():
            raise InterruptedError
        renewal = subprocess.Popen(
            [sys.executable, str(LIFECYCLE_WRITER), "renew", "--path",
             os.environ["PANELS_LIFECYCLE_PATH"], "--deployment-id",
             os.environ["PANELS_DEPLOYMENT_ID"]],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        # The CLI returns one bounded record, so pipe capacity cannot block its exit.
        while renewal.poll() is None:
            if interrupted():
                raise InterruptedError
            if command is not None and command.poll() is not None:
                _shutdown(renewal, timings)
                renewal = None
                return False
            time.sleep(timings.poll)
        stdout, stderr = renewal.communicate()
        status = renewal.returncode
        renewal = None
        if interrupted():
            raise InterruptedError
        if status:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "renewal failed")
        payload = json.loads(stdout)
        if payload["phase"] in TERMINAL_PHASES:
            return False
        # Log only bounded public fields from the canonical writer's persisted record.
        evidence = {
            field: payload[field]
            for field in ("deployment_id", "phase", "updated_at", "expires_at")
        }
        print(
            "Deployment lifecycle renewed: " + json.dumps(evidence, separators=(",", ":")),
            flush=True,
        )
        return True

    try:
        _enable_child_reaping()
        if interrupted():
            raise InterruptedError
        active = os.environ.get("PANELS_DEPLOYMENT_PROGRESS_ACTIVE") == "true"
        renewal_failed = False
        if active:
            try:
                active = renew()
            except InterruptedError:
                raise
            except (RuntimeError, ValueError, KeyError, OSError) as exc:
                print(f"Deployment freshness renewal refused: {exc}", file=sys.stderr, flush=True)
                renewal_failed = True
                active = False
                if not cleanup:
                    return 1
        if interrupted():
            raise InterruptedError
        command = subprocess.Popen(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            start_new_session=True,
        )
        next_renewal = time.monotonic() + timings.renewal
        while command.poll() is None:
            if interrupted():
                raise InterruptedError
            if active and time.monotonic() >= next_renewal:
                # Recheck immediately before renew; a completed command earns no lease.
                if command.poll() is not None:
                    break
                try:
                    active = renew()
                except InterruptedError:
                    raise
                except (RuntimeError, ValueError, KeyError, OSError) as exc:
                    print(
                        f"Deployment freshness renewal failed: {exc}", file=sys.stderr, flush=True,
                    )
                    renewal_failed = True
                    active = False
                next_renewal = time.monotonic() + timings.renewal
            time.sleep(timings.poll)
        if interrupted():
            raise InterruptedError
        status = command.wait()
        return (128 - status if status < 0 else status) or int(renewal_failed)
    except InterruptedError:
        return 128 + (cancelled or signal.SIGTERM)
    finally:
        if renewal is not None:
            _shutdown(renewal, timings)
        if command is not None:
            _shutdown(command, timings)
        _shutdown_adopted_children(timings)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("script", type=Path)
    arguments = parser.parse_args()
    return supervise(arguments.script, cleanup=arguments.cleanup)


if __name__ == "__main__":
    raise SystemExit(main())
