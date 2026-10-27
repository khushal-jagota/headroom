"""The real clean launcher, kernel boundary, bounded pipes, and exact cancellation."""

from __future__ import annotations

import concurrent.futures
import os
import socket
import stat
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from tests.support.measure_runtime import admission

from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import FailureCode, RuntimeBudget
from planner.measure_sources.runtime import Runtime, supervisor


def test_actual_launcher_descriptor_inventory_and_kernel_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protected = tmp_path / "protected"
    protected.write_text("SYNTHETIC-NOT-FOR-SOURCE")
    monkeypatch.setenv("SYNTHETIC_HOST_SECRET", "SYNTHETIC-NOT-FOR-SOURCE")
    launched: dict[str, supervisor._Child] = {}
    source_inodes: dict[int, int] = {}
    original = supervisor._spawn

    def capture(command: list[str], pass_fds: tuple[int, ...] = ()) -> supervisor._Child:
        child = original(command, pass_fds)
        launched["source" if command[0] == "/usr/bin/bwrap" else "broker"] = child
        if command[0] == "/usr/bin/bwrap":
            for fd, stream in enumerate((child.stdin, child.stdout, child.stderr)):
                source_inodes[fd] = os.fstat(stream.fileno()).st_ino
        return child

    monkeypatch.setattr(supervisor, "_spawn", capture)
    outputs = tuple(f"fd{fd}_{field}" for fd in range(32) for field in ("kind", "inode", "access"))
    outputs += ("denied", "memory", "timezone", "mounts")
    script = f"""
import os,stat,ctypes,socket,pathlib,resource
from zoneinfo import ZoneInfo
from datetime import datetime
for fd in range(32):
 try:
  info=os.fstat(fd)
  kind=stat.S_IFMT(info.st_mode)
  inode=info.st_ino
  import fcntl
  access=fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE
  assert os.readlink('/proc/self/fd/'+str(fd))=='pipe:['+str(inode)+']'
 except OSError:
  kind=inode=access=-1
 for field,value in [('kind',kind),('inode',inode),('access',access)]:
  record('fd'+str(fd)+'_'+field,'2026-10-27',value)
assert 'SYNTHETIC_HOST_SECRET' not in os.environ
assert not pathlib.Path({str(protected)!r}).exists()
assert not pathlib.Path('/proc/1/root'+{str(protected)!r}).exists()
denied=0
for family in [socket.AF_INET,socket.AF_INET6,socket.AF_UNIX]:
 try: socket.socket(family,socket.SOCK_STREAM)
 except PermissionError: denied+=1
for path in ['/scratch/x','/x','/dev/x','/dev/shm/x',
             '/proc/sys/kernel/hostname','/usr/lib/python3.13/x']:
 try: pathlib.Path(path).write_text('x')
 except OSError as error:
  assert error.errno==30
  denied+=1
for operation in [os.fork,lambda:os.unshare(os.CLONE_NEWUSER)]:
 try: operation()
 except PermissionError: denied+=1
libc=ctypes.CDLL(None,use_errno=True)
# Native x86_64: legacy AIO, pidfd descriptor access, memfd, and shared memory.
for number,args in [(206,(1,0)),(434,(os.getpid(),0)),(438,(0,0,0)),(319,(0,0)),(29,(0,1,0o1600))]:
 assert libc.syscall(number,*args)==-1 and ctypes.get_errno()==1
 denied+=1
try: resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,)*2)
except (ValueError,OSError): denied+=1
try: bytearray(400*1024*1024)
except MemoryError: record('memory','2026-10-27',1)
record('denied','2026-10-27',denied)
mounts=pathlib.Path('/proc/self/mountinfo').read_text().splitlines()
record('mounts','2026-10-27',sum('rw' in line.split()[5].split(',') for line in mounts))
z=ZoneInfo('Europe/Berlin')
record('timezone','2026-10-27',(datetime(2026,10,26,tzinfo=z).timestamp()-datetime(2026,10,25,tzinfo=z).timestamp())/3600)
"""
    with protected.open("rb") as handle, socket.socket() as parent_socket:
        os.set_inheritable(handle.fileno(), True)
        parent_socket.set_inheritable(True)
        result = Runtime().execute(admission(script, outputs=outputs), ())
    assert result.failure is None, result
    values = {item.measure_id: item.value for item in result.observations}
    for fd in range(32):
        if fd < 3:
            assert values[f"fd{fd}_kind"] == stat.S_IFIFO
            assert values[f"fd{fd}_access"] == (os.O_RDONLY if fd == 0 else os.O_WRONLY)
            assert values[f"fd{fd}_inode"] == source_inodes[fd]
        else:
            assert (
                values[f"fd{fd}_kind"] == values[f"fd{fd}_inode"] == values[f"fd{fd}_access"] == -1
            )
    assert len({values[f"fd{fd}_inode"] for fd in range(3)}) == 3
    assert values["denied"] == 17
    assert values["memory"] == 1 and values["timezone"] == 25 and values["mounts"] == 0
    assert all(child.status is not None for child in launched.values())
    assert "SYNTHETIC-NOT-FOR-SOURCE" not in repr(result)


@pytest.mark.parametrize(
    ("script", "budget", "code"),
    [
        ("import time;time.sleep(10)", RuntimeBudget.wall_seconds, FailureCode.budget_exhausted),
        (
            "import os;os.write(2,b'x'*300000)",
            RuntimeBudget.child_output_bytes,
            FailureCode.budget_exhausted,
        ),
        (
            "import os;os.write(1,b'x'*70000)",
            RuntimeBudget.request_frame_bytes,
            FailureCode.budget_exhausted,
        ),
        ("print('invalid-frame',flush=True)", None, FailureCode.malformed_protocol),
        (
            'print(\'{"op":"latest","id":1,"measure_id":"foreign"}\',flush=True)',
            None,
            FailureCode.invalid_output,
        ),
    ],
)
def test_adversarial_protocol_is_bounded(
    script: str, budget: RuntimeBudget | None, code: FailureCode
) -> None:
    result = Runtime().execute(
        admission(script, limits=replace(DEFAULT_LIMITS, wall_seconds=0.5)), ()
    )
    assert result.failure is not None
    assert result.failure.code == code and result.failure.budget == budget
    assert not result.observations
    assert result.metrics.elapsed_milliseconds < 2000
    assert "invalid-frame" not in repr(result)


def test_cpu_limit_uses_owned_child_usage_and_cancellation_reaps_children() -> None:
    runtime = Runtime()
    cpu = runtime.execute(admission("while True: pass"), ())
    assert cpu.failure is not None, cpu
    assert cpu.failure.code == FailureCode.budget_exhausted, cpu
    assert cpu.failure.budget == RuntimeBudget.script_cpu_seconds, cpu
    assert 1800 <= cpu.metrics.script_cpu_milliseconds <= 3500, cpu
    pending = admission("import time;time.sleep(30)", run_id="cancel-me")
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(runtime.execute, pending, ())
        time.sleep(0.1)
        runtime.cancel(pending.run.id)
        cancelled = future.result(timeout=2)
    assert cancelled.failure is not None
    assert cancelled.failure.code == FailureCode.interrupted
    runtime.cancel("cancel-before-start")
    cancelled_before_start = runtime.execute(admission("pass", run_id="cancel-before-start"), ())
    assert cancelled_before_start.failure is not None
    assert cancelled_before_start.failure.code == FailureCode.interrupted


def test_no_plain_subprocess_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("planner.measure_sources.sandbox.platform.system", lambda: "Unsupported")
    result = Runtime().execute(admission("record('m','2026-10-27',1)"), ())
    assert result.failure is not None
    assert result.failure.code == FailureCode.sandbox_unavailable
    assert not result.observations


def test_unhandled_memory_and_exit_with_buffered_failure_remain_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = Runtime().execute(admission("bytearray(400*1024*1024)"), ())
    assert memory.failure is not None
    assert memory.failure.budget == RuntimeBudget.script_memory_bytes
    # A broker can exit between a selector snapshot and wait4. Its buffered fixed
    # FailureReply must win over an unexplained process-exit classification.
    entry = tmp_path / "broker_failure.py"
    entry.write_text(
        "import sys,json\n"
        "json.loads(sys.stdin.buffer.readline())\n"
        'sys.stdout.write(\'{"op":"failure","id":null,"failure":{"code":"budget_exhausted","budget":"response_bytes"}}\\n\')\n'
        "sys.stdout.flush()\n"
    )
    original = supervisor._broker_command
    monkeypatch.setattr(
        supervisor, "_broker_command", lambda value: [original(value)[0], "-I", str(entry)]
    )
    result = Runtime().execute(admission("import time;time.sleep(10)"), ())
    assert result.failure is not None and result.failure.budget == RuntimeBudget.response_bytes


def test_cancellation_reaps_exact_namespace_pid1_and_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    children: list[supervisor._Child] = []
    identified = threading.Event()
    identify = supervisor._Child.identify_namespace

    def capture(self: supervisor._Child, pid: int) -> None:
        identify(self, pid)
        children.append(self)
        identified.set()

    monkeypatch.setattr(supervisor._Child, "identify_namespace", capture)
    runtime = Runtime()
    value = admission("import time;time.sleep(30)", run_id="owned-cleanup")
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(runtime.execute, value, ())
        assert identified.wait(2)
        child = children[0]
        assert child.namespace_pid is not None and child.namespace_pidfd is not None
        runtime.cancel(value.run.id)
        result = future.result(timeout=2)
    assert result.failure is not None and result.failure.code == FailureCode.interrupted
    assert child.status is not None and child.namespace_pidfd is None
    assert not Path(f"/proc/{child.namespace_pid}").exists()
    assert not Path(f"/proc/{child.process.pid}").exists()
