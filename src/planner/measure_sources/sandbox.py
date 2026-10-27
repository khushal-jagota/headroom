"""Actual Linux boundary. Missing prerequisites refuse execution without fallback."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO

from planner.measure_sources.configuration import SANDBOX_RUNTIME_ROOTS
from planner.measure_sources.contracts import FailureCode, RuntimeLimits
from planner.measure_sources.logic.protocol import refuse

# Enumerated denylist, not a default-deny guarantee against future kernel APIs.
DENIED_SYSCALLS = (
    "socket",
    "socketpair",
    "connect",
    "bind",
    "listen",
    "accept",
    "accept4",
    "clone",
    "clone3",
    "fork",
    "vfork",
    "unshare",
    "setns",
    "mount",
    "umount2",
    "pivot_root",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "bpf",
    "keyctl",
    "add_key",
    "request_key",
    "perf_event_open",
    "userfaultfd",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "memfd_create",
    "memfd_secret",
    "shmget",
    "shmat",
    "shmdt",
    "shmctl",
    "msgget",
    "msgsnd",
    "msgrcv",
    "msgctl",
    "semget",
    "semop",
    "semtimedop",
    "semctl",
    "mq_open",
    "mq_unlink",
    "mq_timedsend",
    "mq_timedreceive",
    "mq_notify",
    "mq_getsetattr",
    "io_setup",
    "io_destroy",
    "io_getevents",
    "io_submit",
    "io_cancel",
    "io_pgetevents",
    "pidfd_open",
    "pidfd_getfd",
    "pidfd_send_signal",
    "process_madvise",
    "process_mrelease",
)


def write_filter(handle: BinaryIO) -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise refuse(FailureCode.sandbox_unavailable)
    try:
        lib = ctypes.CDLL("libseccomp.so.2", use_errno=True)
        lib.seccomp_init.argtypes = [ctypes.c_uint32]
        lib.seccomp_init.restype = ctypes.c_void_p
        lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
        lib.seccomp_rule_add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.seccomp_release.argtypes = [ctypes.c_void_p]
        context = lib.seccomp_init(0x7FFF0000)
        if not context:
            raise refuse(FailureCode.sandbox_unavailable)
        try:
            for name in DENIED_SYSCALLS:
                number = lib.seccomp_syscall_resolve_name(name.encode())
                if number < 0 or lib.seccomp_rule_add(context, 0x50000 | errno.EPERM, number, 0):
                    raise refuse(FailureCode.sandbox_unavailable)
            # libseccomp's default bad-architecture action is KILL; only native ABI is loaded.
            if lib.seccomp_export_bpf(context, handle.fileno()):
                raise refuse(FailureCode.sandbox_unavailable)
        finally:
            lib.seccomp_release(context)
    except OSError:
        raise refuse(FailureCode.sandbox_unavailable) from None
    handle.seek(0)


def command(source: Path, filter_fd: int, limits: RuntimeLimits, info_fd: int) -> list[str]:
    bootstrap = source.parent / "bootstrap.py"
    if not all(Path(path).exists() for path in ("/usr/bin/bwrap", *SANDBOX_RUNTIME_ROOTS)):
        raise refuse(FailureCode.sandbox_unavailable)
    result = [
        "/usr/bin/bwrap",
        "--unshare-all",
        "--unshare-user",
        "--disable-userns",
        "--assert-userns-disabled",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--new-session",
        "--die-with-parent",
        "--as-pid-1",
        "--info-fd",
        str(info_fd),
    ]
    for path in SANDBOX_RUNTIME_ROOTS:
        result.extend(("--ro-bind", path, path))
    result.extend(
        (
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--dir",
            "/scratch",
            "--ro-bind",
            str(bootstrap),
            "/run/bootstrap.py",
            "--ro-bind",
            str(source),
            "/run/source.py",
            "--chdir",
            "/scratch",
            "--remount-ro",
            "/",
            "--remount-ro",
            "/dev",
            "--remount-ro",
            "/proc",
            "--seccomp",
            str(filter_fd),
            "/usr/bin/python3.13",
            "-I",
            "-S",
            "/run/bootstrap.py",
            json.dumps(asdict(limits), separators=(",", ":")),
        )
    )
    # --remount-ro is nonrecursive. The private device tree has nested mounts.
    index = result.index("--seccomp")
    for path in (
        "/dev/null",
        "/dev/zero",
        "/dev/full",
        "/dev/random",
        "/dev/urandom",
        "/dev/tty",
        "/dev/pts",
    ):
        result[index:index] = ["--remount-ro", path]
        index += 2
    return result


def private_source(directory: Path, source: str) -> Path:
    path = directory / "source.py"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(source)
    bootstrap = path.with_name("bootstrap.py")
    descriptor = os.open(bootstrap, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(Path(__file__).with_name("bootstrap.py").read_bytes())
    return path
