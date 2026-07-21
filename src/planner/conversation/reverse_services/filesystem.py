"""Exact ACP text-file reverse service with descriptor-owned confinement."""

from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable

from acp.schema import (
    ReadTextFileRequest,
    ReadTextFileResponse,
    WriteTextFileRequest,
    WriteTextFileResponse,
)

from ..contracts import ConversationEmployee, ConversationSessionBinding
from .path_confinement import AcpPathConfinement, AcpPathConfinementError

BindingResolver = Callable[
    [ConversationEmployee, int],
    ConversationSessionBinding | None | Awaitable[ConversationSessionBinding | None],
]


class AcpReverseFilesystemError(RuntimeError):
    pass


class ConfinedAcpFilesystemService:
    def __init__(
        self,
        binding_resolver: BindingResolver,
        *,
        filesystem_enabled: bool,
    ) -> None:
        self._binding_resolver = binding_resolver
        self._filesystem_enabled = filesystem_enabled
        self._confinements: dict[tuple[str, tuple[str, ...]], AcpPathConfinement] = {}

    def confinement_for(self, employee: ConversationEmployee) -> AcpPathConfinement:
        key = (
            employee.employee_id,
            tuple(str(root) for root in employee.workspace_roots),
        )
        confinement = self._confinements.get(key)
        if confinement is None:
            try:
                confinement = AcpPathConfinement(employee.workspace_roots)
            except AcpPathConfinementError as error:
                raise AcpReverseFilesystemError(str(error)) from error
            self._confinements[key] = confinement
        return confinement

    async def read_text_file(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: ReadTextFileRequest,
    ) -> ReadTextFileResponse:
        await self._validate_scope(employee, child_generation, request.session_id)
        if request.line is not None and request.line < 1:
            raise AcpReverseFilesystemError("line must be one-based")
        if request.limit is not None and request.limit < 0:
            raise AcpReverseFilesystemError("limit must be non-negative")
        try:
            descriptor = self.confinement_for(employee).open_read_file(self._path(request.path))
            try:
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
            text = b"".join(chunks).decode("utf-8", errors="strict")
        except (OSError, UnicodeDecodeError, AcpPathConfinementError) as error:
            raise AcpReverseFilesystemError("text file could not be read safely") from error
        lines = text.splitlines(keepends=True)
        start = (request.line or 1) - 1
        selected = lines[start:]
        if request.limit is not None:
            selected = selected[: request.limit]
        return ReadTextFileResponse(content="".join(selected))

    async def write_text_file(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: WriteTextFileRequest,
    ) -> WriteTextFileResponse:
        await self._validate_scope(employee, child_generation, request.session_id)
        encoded = request.content.encode("utf-8", errors="strict")
        try:
            descriptor = self.confinement_for(employee).open_write_file(self._path(request.path))
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(descriptor, view)
                    view = view[written:]
            finally:
                os.close(descriptor)
        except (OSError, UnicodeEncodeError, AcpPathConfinementError) as error:
            raise AcpReverseFilesystemError("text file could not be written safely") from error
        return WriteTextFileResponse()

    async def _validate_scope(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        session_id: str,
    ) -> None:
        if not self._filesystem_enabled:
            raise AcpReverseFilesystemError("filesystem reverse service is disabled")
        binding_or_awaitable = self._binding_resolver(employee, child_generation)
        if inspect.isawaitable(binding_or_awaitable):
            binding = await binding_or_awaitable
        else:
            binding = binding_or_awaitable
        if (
            binding is None
            or binding.employee_id != employee.employee_id
            or binding.acp_session_id != session_id
            or binding.backend_key != employee.backend_key
        ):
            raise AcpReverseFilesystemError("filesystem request is outside the live session")

    def close(self) -> None:
        confinements, self._confinements = self._confinements, {}
        for confinement in confinements.values():
            confinement.close()

    @staticmethod
    def _path(value: str):  # type: ignore[no-untyped-def]
        from pathlib import Path

        return Path(value)
