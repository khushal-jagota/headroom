"""Bounded native Mac preview evidence, without commands or PID-only ownership.

ABI: Apple's bsd/sys/proc_info.h and bsd/sys/socket.h. The socket generation
counter occupies the shared listener_inode field. Birth uses exact microseconds.
Discovery proves one readable same-UID owner, not absence of hidden foreign holders.
"""

from __future__ import annotations

import ctypes as C
import errno
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

from planner.platform_runtime.darwin.worker_processes import _BSDInfo
from planner.previews import config
from planner.previews.contracts import PreviewError, ProcessIdentity


class _Stat(C.Structure):
    _fields_: ClassVar[Any] = (
        [
            ("dev", C.c_uint32),
            ("mode", C.c_uint16),
            ("nlink", C.c_uint16),
            ("ino", C.c_uint64),
            ("uid", C.c_uint32),
            ("gid", C.c_uint32),
        ]
        + [
            (name, C.c_int64)
            for name in (
                "atime",
                "atimensec",
                "mtime",
                "mtimensec",
                "ctime",
                "ctimensec",
                "birthtime",
                "birthtimensec",
                "size",
                "blocks",
            )
        ]
        + [
            ("blksize", C.c_int32),
            ("flags", C.c_uint32),
            ("gen", C.c_uint32),
            ("rdev", C.c_uint32),
            ("qspare", C.c_int64 * 2),
        ]
    )


class _Vnode(C.Structure):
    _fields_: ClassVar[Any] = [
        ("stat", _Stat),
        ("type", C.c_int),
        ("pad", C.c_int),
        ("fsid", C.c_int32 * 2),
    ]


class _VnodePath(C.Structure):
    _fields_: ClassVar[Any] = [("info", _Vnode), ("path", C.c_char * 1024)]


class _Paths(C.Structure):
    _fields_: ClassVar[Any] = [("cwd", _VnodePath), ("root", _VnodePath)]


class _FD(C.Structure):
    _fields_: ClassVar[Any] = [("fd", C.c_int32), ("kind", C.c_uint32)]


class _File(C.Structure):
    _fields_: ClassVar[Any] = [
        ("openflags", C.c_uint32),
        ("status", C.c_uint32),
        ("offset", C.c_int64),
        ("type", C.c_int32),
        ("guardflags", C.c_uint32),
    ]


class _IPv6(C.Structure):
    _fields_: ClassVar[Any] = [
        ("hlim", C.c_uint8),
        ("cksum", C.c_int),
        ("ifindex", C.c_uint16),
        ("hops", C.c_int16),
    ]


class _Internet(C.Structure):
    _fields_: ClassVar[Any] = [
        ("remote_port", C.c_int),
        ("local_port", C.c_int),
        ("generation", C.c_uint64),
        ("flags", C.c_uint32),
        ("flow", C.c_uint32),
        ("vflag", C.c_uint8),
        ("ttl", C.c_uint8),
        ("reserved", C.c_uint32),
        ("remote_address", C.c_ubyte * 16),
        ("local_address", C.c_ubyte * 16),
        ("v4_tos", C.c_uint8),
        ("v6", _IPv6),
    ]


class _TCP(C.Structure):
    _fields_: ClassVar[Any] = [
        ("internet", _Internet),
        ("state", C.c_int),
        ("timer", C.c_int * 4),
        ("mss", C.c_int),
        ("flags", C.c_uint32),
        ("reserved", C.c_uint32),
        ("control_block", C.c_uint64),
    ]


class _Unix(C.Structure):
    # Each sockaddr union is SOCK_MAXADDRLEN (255), with byte alignment.
    _fields_: ClassVar[Any] = [
        ("connection", C.c_uint64),
        ("control_block", C.c_uint64),
        ("address", C.c_char * 255),
        ("remote_address", C.c_char * 255),
    ]


class _Protocol(C.Union):
    # The Unix member is the largest member of Apple's socket_info union.
    _fields_: ClassVar[Any] = [("tcp", _TCP), ("unix", _Unix)]


class _SocketBuffer(C.Structure):
    _fields_: ClassVar[Any] = [
        (name, C.c_uint32)
        for name in (
            "count",
            "highwater",
            "mbcount",
            "mbmax",
            "lowwater",
        )
    ] + [("flags", C.c_int16), ("timeout", C.c_int16)]


class _Socket(C.Structure):
    _fields_: ClassVar[Any] = (
        [
            ("stat", _Stat),
            ("handle", C.c_uint64),
            ("control_block", C.c_uint64),
            ("type", C.c_int),
            ("protocol", C.c_int),
            ("family", C.c_int),
        ]
        + [
            (name, C.c_int16)
            for name in (
                "options",
                "linger",
                "state",
                "qlen",
                "incqlen",
                "qlimit",
                "timeout",
                "error",
            )
        ]
        + [
            ("oobmark", C.c_uint32),
            ("receive", _SocketBuffer),
            ("send", _SocketBuffer),
            ("kind", C.c_int),
            ("reserved", C.c_uint32),
            ("protocol_info", _Protocol),
        ]
    )


class _SocketFD(C.Structure):
    _fields_: ClassVar[Any] = [("file", _File), ("socket", _Socket)]


class DarwinProcessInspector:
    def __init__(self, *, library: Any = None) -> None:
        # Import/selection on Linux never loads a Mac library.
        self._library = library
        self._boot: str | None = None

    def _deadline(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise PreviewError("Process inspection exceeded its deadline.", 503)

    def _lib(self) -> Any:
        if self._library is None:
            self._library = C.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        return self._library

    def _boot_id(self, deadline: float) -> str:
        self._deadline(deadline)
        if self._boot is None:
            try:
                result = subprocess.run(
                    ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=max(0.001, deadline - time.monotonic()),
                )
                self._boot = str(UUID(result.stdout.strip()))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise PreviewError("Mac boot evidence is unavailable.", 410) from exc
        self._deadline(deadline)
        return self._boot

    def _read(self, pid: int, flavor: int, value: Any, deadline: float) -> None:
        self._deadline(deadline)
        length = self._lib().proc_pidinfo(pid, flavor, 0, C.byref(value), C.sizeof(value))
        self._deadline(deadline)
        if length != C.sizeof(value):
            raise PreviewError("Mac process evidence is missing or unreadable.", 410)

    def _identity(
        self, pid: int, generation: int, port: int, worktree: Path, deadline: float
    ) -> ProcessIdentity:
        info, paths = _BSDInfo(), _Paths()
        self._read(pid, 3, info, deadline)  # PROC_PIDTBSDINFO
        self._read(pid, 9, paths, deadline)  # PROC_PIDVNODEPATHINFO
        try:
            cwd = Path(os.fsdecode(paths.cwd.path)).resolve(strict=True)
            if (
                not paths.cwd.path
                or len(paths.cwd.path) >= 1024
                or info.uid != os.getuid()
                or info.pid != pid
            ):
                raise ValueError("missing process ownership")
            cwd.relative_to(worktree)
            if info.start_sec == 0 or info.start_usec >= 1_000_000:
                raise ValueError("invalid exact birth")
        except (OSError, ValueError) as exc:
            raise PreviewError(
                "Listener cwd or ownership is outside the Ticket preview worktree.", 410
            ) from exc
        return ProcessIdentity(
            self._boot_id(deadline),
            pid,
            info.start_sec * 1_000_000 + info.start_usec,
            generation,
            port,
            str(worktree),
        )

    def _pids(self, deadline: float) -> list[int]:
        self._deadline(deadline)
        # One extra slot makes a full buffer an explicit truncation refusal.
        values = (C.c_int * (config.DISCOVERY_PIDS + 1))()
        size = self._lib().proc_listpids(4, os.getuid(), values, C.sizeof(values))
        self._deadline(deadline)
        if size <= 0 or size % C.sizeof(C.c_int) or size >= C.sizeof(values):
            raise PreviewError("Mac process discovery is incomplete or exceeds its bound.", 503)
        return list({int(pid) for pid in values[: size // C.sizeof(C.c_int)] if pid > 0})

    def _sockets(self, pid: int, deadline: float, budget: list[int]) -> list[_Socket]:
        self._deadline(deadline)
        remaining = config.DISCOVERY_FDS - budget[0]
        if remaining <= 0:
            raise PreviewError("Process descriptor inspection bound exceeded.", 503)
        values = (_FD * (remaining + 1))()
        C.set_errno(0)
        size = self._lib().proc_pidinfo(pid, 1, 0, values, C.sizeof(values))
        self._deadline(deadline)
        if size == 0 and C.get_errno() == errno.ESRCH:
            return []
        if size < 0 or (size == 0 and C.get_errno()):
            raise PreviewError("Mac process descriptors are unreadable.", 410)
        if size % C.sizeof(_FD) or size >= C.sizeof(values):
            raise PreviewError("Process descriptor inspection bound exceeded.", 503)
        count = size // C.sizeof(_FD)
        budget[0] += count
        result = []
        for fd in values[:count]:
            self._deadline(deadline)
            if fd.kind != 2:  # PROX_FDTYPE_SOCKET
                continue
            value = _SocketFD()
            C.set_errno(0)
            length = self._lib().proc_pidfdinfo(
                pid,
                fd.fd,
                3,
                C.byref(value),
                C.sizeof(value),
            )  # PROC_PIDFDSOCKETINFO
            self._deadline(deadline)
            if length != C.sizeof(value):
                if C.get_errno() in (errno.EBADF, errno.ESRCH):
                    continue
                raise PreviewError("Mac socket ownership is unreadable.", 410)
            if value.socket.kind == 2 and value.socket.protocol == socket.IPPROTO_TCP:
                result.append(value.socket)
        return result

    @staticmethod
    def _port(value: _Socket) -> int:
        return socket.ntohs(value.protocol_info.tcp.internet.local_port & 0xFFFF)

    @staticmethod
    def _loopback(value: _Socket) -> bool:
        return value.family == socket.AF_INET and bytes(
            value.protocol_info.tcp.internet.local_address
        )[12:] == socket.inet_aton("127.0.0.1")

    def discover(self, port: int, worktree: Path) -> ProcessIdentity:
        deadline = time.monotonic() + config.INSPECTION_SECONDS
        owners: dict[tuple[int, int], _Socket] = {}
        budget = [0]
        for pid in self._pids(deadline):
            for value in self._sockets(pid, deadline, budget):
                if value.protocol_info.tcp.state == 1 and self._port(value) == port:
                    generation = int(value.protocol_info.tcp.internet.generation)
                    owners[pid, generation] = value
        if len(owners) != 1:
            raise PreviewError("Listener ownership is missing or shared.")
        (pid, generation), value = next(iter(owners.items()))
        if not self._loopback(value) or not 0 < generation < 2**63:
            raise PreviewError("Require exactly one listener bound to 127.0.0.1.")
        identity = self._identity(pid, generation, port, worktree, deadline)
        self.check(identity, deadline=deadline)
        return identity

    def check(self, identity: ProcessIdentity, *, deadline: float | None = None) -> None:
        deadline = (
            deadline if deadline is not None else time.monotonic() + config.INSPECTION_SECONDS
        )
        worktree = Path(identity.worktree)
        before = self._identity(
            identity.pid, identity.listener_inode, identity.port, worktree, deadline
        )
        listeners = {
            (int(value.protocol_info.tcp.internet.generation), self._loopback(value))
            for value in self._sockets(identity.pid, deadline, [0])
            if value.protocol_info.tcp.state == 1 and self._port(value) == identity.port
        }
        after = self._identity(
            identity.pid, identity.listener_inode, identity.port, worktree, deadline
        )
        if before != identity or after != identity:
            raise PreviewError("Preview process identity changed.", 410)
        if listeners != {(identity.listener_inode, True)}:
            raise PreviewError("Preview listener ended or changed.", 410)

    def connected(self, identity: ProcessIdentity, local_port: int) -> None:
        deadline = time.monotonic() + config.INSPECTION_SECONDS
        while True:
            self.check(identity, deadline=deadline)
            for value in self._sockets(identity.pid, deadline, [0]):
                internet = value.protocol_info.tcp.internet
                if (
                    self._loopback(value)
                    and value.protocol_info.tcp.state == 4
                    and self._port(value) == identity.port
                    and socket.ntohs(internet.remote_port & 0xFFFF) == local_port
                    and bytes(internet.remote_address)[12:] == socket.inet_aton("127.0.0.1")
                ):
                    self.check(identity, deadline=deadline)
                    return
            self._deadline(deadline)
            time.sleep(min(config.ACCEPT_RETRY_SECONDS, max(0, deadline - time.monotonic())))
