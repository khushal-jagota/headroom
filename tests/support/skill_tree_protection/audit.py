"""Refuse interpreter-audited writes to the frozen launch skill inventory.

This is an interpreter diagnostic guard, not process containment. Existing file
descriptors, native code, child processes and path races remain outside its reach.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import NoReturn

from tests.support.skill_tree_protection.contracts import (
    GuardRefusal,
    ProtectionInventory,
    RefusalReceipt,
)

_ACKNOWLEDGEMENT_EVENT = "panels.tests.skill_guard.acknowledgement"
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


class AuditGuard:
    def __init__(self, inventory: ProtectionInventory) -> None:
        self.inventory = inventory
        roots: list[Path] = []
        for path in sorted(map(Path, inventory.protected_paths), key=lambda item: len(item.parts)):
            if not any(path.is_relative_to(root) for root in roots):
                roots.append(path)
        self._protected_roots = tuple(roots)
        self._receipts: list[RefusalReceipt] = []
        self._context = "collection/session"
        self._acknowledged = False
        self._installed = False

    @property
    def receipts(self) -> tuple[RefusalReceipt, ...]:
        return tuple(self._receipts)

    def set_context(self, node: str) -> None:
        self._context = node

    def install(self) -> None:
        if self._installed:
            return
        sys.addaudithook(self._audit)
        sys.audit(_ACKNOWLEDGEMENT_EVENT, self)
        if not self._acknowledged:
            raise RuntimeError("skill guard audit-hook installation was suppressed")
        self._installed = True

    def _refuse(self, event: str, path: object, reason: str) -> NoReturn:
        receipt = RefusalReceipt(self._context, event, str(path), reason)
        self._receipts.append(receipt)
        raise GuardRefusal(
            f"skill guard refused path={path!s} operation={event} test={self._context}: {reason}"
        )

    def _path(self, operand: object, descriptor: object, event: str) -> Path:
        if isinstance(operand, int):
            return self._descriptor_path(operand, event)
        if not isinstance(operand, (str, bytes, os.PathLike)):
            self._refuse(event, operand, "filesystem operand cannot be resolved safely")
        raw = os.fsdecode(operand)
        if os.path.isabs(raw):
            return Path(raw)
        if descriptor is None or descriptor == -1 or descriptor == getattr(os, "AT_FDCWD", -100):
            base = Path.cwd()
        elif isinstance(descriptor, int):
            base = self._descriptor_path(descriptor, event)
            if not base.is_dir():
                self._refuse(event, operand, "directory descriptor does not name a directory")
        else:
            self._refuse(event, operand, "directory descriptor cannot be resolved safely")
        return base / raw

    def _descriptor_path(self, descriptor: int, event: str) -> Path:
        try:
            name = os.readlink(f"/proc/self/fd/{descriptor}")
        except OSError:
            self._refuse(event, f"fd:{descriptor}", "descriptor base is unavailable")
        if not name.startswith("/"):
            self._refuse(event, f"fd:{descriptor}", "descriptor has no stable filesystem path")
        if name.endswith(" (deleted)"):
            try:
                descriptor_info = os.fstat(descriptor)
                path_info = (
                    os.stat(name)
                    if stat.S_ISDIR(descriptor_info.st_mode) and descriptor_info.st_nlink > 0
                    else None
                )
            except (OSError, OverflowError):
                self._refuse(event, f"fd:{descriptor}", "descriptor has no stable filesystem path")
            # A live directory can literally end in the procfs deletion marker.
            # Attribute its unchanged name only when it still names this object.
            if path_info is None or (path_info.st_dev, path_info.st_ino) != (
                descriptor_info.st_dev, descriptor_info.st_ino,
            ):
                self._refuse(event, f"fd:{descriptor}", "descriptor has no stable filesystem path")
        return Path(name)

    def _is_absent_leaf_in_deleted_directory(
        self, operand: object, descriptor: object,
    ) -> bool:
        if not isinstance(operand, (str, bytes, os.PathLike)):
            return False
        raw = os.fsdecode(operand)
        if (
            not raw or raw in (".", "..") or os.sep in raw
            or (os.altsep is not None and os.altsep in raw)
        ):
            return False
        if not isinstance(descriptor, int) or descriptor in (
            -1, getattr(os, "AT_FDCWD", -100),
        ):
            return False
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_nlink != 0:
                return False
            name = os.readlink(f"/proc/self/fd/{descriptor}")
        except (OSError, OverflowError):
            return False
        # An unlinked directory is empty and cannot acquire child entries. The
        # native removal must fail without mutation. A '..' operand can still
        # reach its live parent, and a live name can literally end in this marker.
        return name.startswith("/") and name.endswith(" (deleted)")

    def _check(self, event: str, path: Path, *, entry: bool, destructive: bool = False) -> None:
        try:
            resolved = (
                path.parent.resolve(strict=False) / path.name if entry
                else path.resolve(strict=False)
            )
        except (OSError, RuntimeError):
            self._refuse(event, path, "destination cannot be resolved safely")
            return
        candidates = (resolved,)
        for root in self._protected_roots:
            # Inventory retains aliases and frozen external targets, including
            # missing roots. No later environment value changes this decision.
            for candidate in candidates:
                if candidate.is_relative_to(root) or (
                    destructive and root.is_relative_to(candidate)
                ):
                    self._refuse(event, path, f"protected launch skill path {root}")

    def _audit(self, event: str, args: tuple[object, ...]) -> None:
        if event == _ACKNOWLEDGEMENT_EVENT:
            if args and args[0] is self:
                self._acknowledged = True
            return
        if event == "open":
            operand, mode, flags = args
            # fdopen does not open a path or create a new descriptor. Existing
            # descriptors are explicitly outside this path-based hook's scope;
            # this includes ordinary subprocess pipes and sockets.
            if isinstance(operand, int):
                return
            writes = (isinstance(flags, int) and bool(flags & _WRITE_FLAGS)) or (
                isinstance(mode, str) and any(character in mode for character in "wax+")
            )
            if not writes:
                return
            if mode is None and isinstance(operand, (str, bytes)) and not os.path.isabs(operand):
                # CPython's os.open audit payload omits dir_fd. CWD cannot
                # distinguish a normal relative call from a descriptor call.
                self._refuse(
                    event, os.fsdecode(operand),
                    "relative low-level write open has unknown directory base (dir_fd omitted)",
                )
            self._check(event, self._path(operand, None, event), entry=False)
        elif event in ("os.remove", "os.rmdir", "os.mkdir"):
            if event != "os.mkdir" and self._is_absent_leaf_in_deleted_directory(
                args[0], args[-1],
            ):
                return
            self._check(event, self._path(args[0], args[-1], event), entry=True,
                        destructive=event != "os.mkdir")
        elif event in ("os.rename", "os.link"):
            self._check(event, self._path(args[0], args[2], event), entry=True,
                        destructive=event == "os.rename")
            self._check(event, self._path(args[1], args[3], event), entry=True,
                        destructive=event == "os.rename")
            if event == "os.link":
                # os.link's audit payload omits follow_symlinks. Protect both
                # interpretations of a symlink source rather than guessing.
                self._check(event, self._path(args[0], args[2], event), entry=False)
        elif event == "os.symlink":
            destination = self._path(args[1], args[2], event)
            self._check(event, destination, entry=True)
            raw = os.fsdecode(args[0])  # type: ignore[arg-type]
            target = destination.parent / raw
            self._check(event, target, entry=False)
        elif event == "os.truncate":
            self._check(event, self._path(args[0], None, event), entry=False)
