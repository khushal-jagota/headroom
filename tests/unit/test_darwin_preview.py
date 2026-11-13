"""Synthetic libproc protects native identity and socket ownership gaps.

The real Linux integration host already proves registry/proxy wiring. These cases
cover the distinct native ABI read boundary without another host or browser.
"""

from __future__ import annotations

import ctypes as C
import errno
import os
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID
from planner.platform_runtime.darwin import preview as native
from planner.platform_runtime.darwin.worker_processes import _BSDInfo
from planner.previews import config
from planner.previews.contracts import PreviewError, TicketEligibility
from planner.previews.logic.eligibility import require_eligible


def tcp(*, state: int = 1, generation: int = 567) -> native._Socket:
    value = native._Socket()
    value.kind, value.protocol, value.family = 2, socket.IPPROTO_TCP, socket.AF_INET
    value.protocol_info.tcp.state = state
    internet = value.protocol_info.tcp.internet
    internet.generation = generation
    internet.local_port, internet.remote_port = socket.htons(3001), socket.htons(45678)
    for address in (internet.local_address, internet.remote_address):
        address[12:] = socket.inet_aton("127.0.0.1")
    return value


class Libproc:
    def __init__(self, cwd: Path) -> None:
        self.cwd, self.birth = cwd, 123
        self.descriptors = {12: [tcp()]}
        self.error, self.truncate = 0, False

    def proc_listpids(self, kind: int, uid: int, target: Any, size: int) -> int:
        for i, pid in enumerate(self.descriptors):
            target[i] = pid
        return size if self.truncate else len(self.descriptors) * C.sizeof(C.c_int)

    def proc_pidinfo(self, pid: int, flavor: int, arg: int, target: Any, size: int) -> int:
        if flavor == 1:
            for i, _ in enumerate(self.descriptors[pid]):
                target[i].fd, target[i].kind = i + 3, 2
            return len(self.descriptors[pid]) * C.sizeof(native._FD)
        if flavor == 3:
            info = _BSDInfo()
            info.pid, info.uid = pid, os.getuid()
            info.start_sec, info.start_usec = 1, self.birth
            C.memmove(target, C.byref(info), C.sizeof(info))
        elif flavor == 9:
            paths = native._Paths()
            paths.cwd.path = os.fsencode(self.cwd)
            C.memmove(target, C.byref(paths), C.sizeof(paths))
        return size

    def proc_pidfdinfo(self, pid: int, fd: int, flavor: int, target: Any, size: int) -> int:
        if self.error:
            C.set_errno(self.error)
            return 0
        value = native._SocketFD()
        value.socket = self.descriptors[pid][fd - 3]
        C.memmove(target, C.byref(value), C.sizeof(value))
        return size


def inspector(path: Path) -> tuple[native.DarwinProcessInspector, Libproc]:
    library = Libproc(path)
    subject = native.DarwinProcessInspector(library=library)
    subject._boot = "boot"
    return subject, library


def test_exact_birth_and_socket_generation_reuse(tmp_path: Path) -> None:
    subject, library = inspector(tmp_path)
    identity = subject.discover(3001, tmp_path)
    assert identity.start_ticks == 1_000_123 and identity.listener_inode == 567
    library.birth += 1
    with pytest.raises(PreviewError, match="identity changed"):
        subject.check(identity)
    library.birth -= 1
    library.descriptors[12][0].protocol_info.tcp.internet.generation += 1
    with pytest.raises(PreviewError, match="listener ended or changed"):
        subject.check(identity)


@pytest.mark.parametrize("failure", ["shared", "wildcard", "cwd"])
def test_one_loopback_owner_in_reserved_tree(tmp_path: Path, failure: str) -> None:
    subject, library = inspector(tmp_path)
    if failure == "shared":
        library.descriptors[13] = [tcp()]
    elif failure == "wildcard":
        library.descriptors[12][0].protocol_info.tcp.internet.local_address[12:] = b"\0" * 4
    else:
        library.cwd = tmp_path.parent
    with pytest.raises(PreviewError):
        subject.discover(3001, tmp_path)


def test_accepted_reverse_tuple_belongs_to_pinned_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, library = inspector(tmp_path)
    identity = subject.discover(3001, tmp_path)
    library.descriptors[13] = [tcp(state=4)]
    monkeypatch.setattr(config, "INSPECTION_SECONDS", 0.03)
    with pytest.raises(PreviewError, match="deadline"):
        subject.connected(identity, 45678)
    library.descriptors[12].append(tcp(state=4, generation=568))
    subject.connected(identity, 45678)


def test_incomplete_unreadable_and_expired_evidence(tmp_path: Path) -> None:
    subject, library = inspector(tmp_path)
    library.truncate = True
    with pytest.raises(PreviewError, match="incomplete"):
        subject.discover(3001, tmp_path)
    library.truncate, library.error = False, errno.EPERM
    with pytest.raises(PreviewError, match="unreadable"):
        subject.discover(3001, tmp_path)
    with pytest.raises(PreviewError, match="deadline"):
        subject._pids(time.monotonic() - 1)


def test_named_home_eligibility_and_all_remote_machines(tmp_path: Path) -> None:
    require_eligible(TicketEligibility("t_home", tmp_path, False, HOME_MACHINE_ID))
    for destination in (ORIGINAL_MAC_MACHINE_ID, "another-mac"):
        with pytest.raises(PreviewError, match="remote Mac executor"):
            require_eligible(TicketEligibility("t_remote", tmp_path, False, destination))
