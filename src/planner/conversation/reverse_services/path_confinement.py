"""Descriptor-backed confinement for ACP filesystem paths and terminal cwd."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class AcpPathConfinementError(RuntimeError):
    pass


class AcpPathConfinement:
    def __init__(self, workspace_roots: tuple[Path, ...]) -> None:
        if not workspace_roots:
            raise ValueError("workspace_roots must not be empty")
        self._roots: list[tuple[Path, int]] = []
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        for declared in workspace_roots:
            try:
                resolved = declared.resolve(strict=True)
            except OSError as error:
                raise AcpPathConfinementError("workspace root could not be resolved") from error
            if not resolved.is_dir():
                raise AcpPathConfinementError("workspace root is not a directory")
            try:
                descriptor = os.open(resolved, flags)
            except OSError as error:
                self.close()
                raise AcpPathConfinementError("workspace root could not be opened") from error
            self._roots.append((resolved, descriptor))

    @property
    def resolved_roots(self) -> tuple[Path, ...]:
        return tuple(root for root, _descriptor in self._roots)

    def close(self) -> None:
        roots, self._roots = self._roots, []
        for _root, descriptor in roots:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def resolve_directory(self, requested: Path) -> Path:
        self._require_absolute_and_lexically_confined(requested)
        try:
            canonical = requested.resolve(strict=True)
        except OSError as error:
            raise AcpPathConfinementError("directory could not be resolved") from error
        root, relative = self._select_root(canonical)
        descriptor = self._open_parent_chain(root, relative.parts)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise AcpPathConfinementError("requested cwd is not a directory")
        finally:
            os.close(descriptor)
        return canonical

    def open_read_file(self, requested: Path) -> int:
        canonical = self._canonical_existing(requested)
        root, relative = self._select_root(canonical)
        return self._open_existing_regular(root, relative, writable=False)

    def open_write_file(self, requested: Path) -> int:
        self._require_absolute_and_lexically_confined(requested)
        try:
            requested.lstat()
        except FileNotFoundError:
            return self._open_new_regular(requested)
        except OSError as error:
            raise AcpPathConfinementError("write target could not be inspected") from error
        try:
            canonical = requested.resolve(strict=True)
        except OSError as error:
            raise AcpPathConfinementError("write target symlink is not valid") from error
        root, relative = self._select_root(canonical)
        descriptor = self._open_existing_regular(root, relative, writable=True)
        try:
            os.ftruncate(descriptor, 0)
        except OSError as error:
            os.close(descriptor)
            raise AcpPathConfinementError("write target could not be replaced") from error
        return descriptor

    def _canonical_existing(self, requested: Path) -> Path:
        self._require_absolute_and_lexically_confined(requested)
        try:
            requested.lstat()
            return requested.resolve(strict=True)
        except OSError as error:
            raise AcpPathConfinementError("read target could not be resolved") from error

    def _open_existing_regular(
        self,
        root: tuple[Path, int],
        relative: Path,
        *,
        writable: bool,
    ) -> int:
        parts = relative.parts
        if not parts:
            raise AcpPathConfinementError("requested path is a directory")
        parent = self._open_parent_chain(root, parts[:-1])
        flags = (os.O_WRONLY if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=parent)
        except OSError as error:
            raise AcpPathConfinementError("confined file could not be opened") from error
        finally:
            os.close(parent)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise AcpPathConfinementError("requested path is not a regular file")
        return descriptor

    def _open_new_regular(self, requested: Path) -> int:
        try:
            parent_canonical = requested.parent.resolve(strict=True)
        except OSError as error:
            raise AcpPathConfinementError("write target parent does not exist") from error
        root, relative_parent = self._select_root(parent_canonical)
        parent = self._open_parent_chain(root, relative_parent.parts)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(requested.name, flags, 0o644, dir_fd=parent)
        except OSError as error:
            raise AcpPathConfinementError("new confined file could not be created") from error
        finally:
            os.close(parent)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise AcpPathConfinementError("new path is not a regular file")
        return descriptor

    def _open_parent_chain(self, root: tuple[Path, int], parts: tuple[str, ...]) -> int:
        descriptor = os.dup(root[1])
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            for component in parts:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except OSError as error:
            os.close(descriptor)
            raise AcpPathConfinementError("confined path changed while it was opened") from error

    def _select_root(self, canonical: Path) -> tuple[tuple[Path, int], Path]:
        candidates = [root for root in self._roots if canonical.is_relative_to(root[0])]
        if not candidates:
            raise AcpPathConfinementError("path is outside every workspace root")
        selected = max(candidates, key=lambda root: len(root[0].parts))
        return selected, canonical.relative_to(selected[0])

    @staticmethod
    def _require_absolute_and_lexically_confined(requested: Path) -> None:
        if not requested.is_absolute():
            raise AcpPathConfinementError("path must be absolute")
        if ".." in requested.parts:
            raise AcpPathConfinementError("path must not contain parent traversal")
