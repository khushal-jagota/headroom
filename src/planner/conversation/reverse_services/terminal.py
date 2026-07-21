"""Employee/session-scoped ACP terminal ownership and bounded display state."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import inspect
import signal
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.schema import (
    CreateTerminalRequest,
    CreateTerminalResponse,
    KillTerminalRequest,
    KillTerminalResponse,
    ReleaseTerminalRequest,
    ReleaseTerminalResponse,
    TerminalExitStatus,
    TerminalOutputRequest,
    TerminalOutputResponse,
    WaitForTerminalExitRequest,
    WaitForTerminalExitResponse,
)

from ..configuration import (
    ACP_REVERSE_TERMINAL_CLEANUP_TIMEOUT_SECONDS,
    ACP_REVERSE_TERMINAL_OUTPUT_MAX_BYTES,
)
from ..contracts import (
    ConversationEmployee,
    ConversationSessionBinding,
    ConversationTerminalLifecycle,
    ConversationTerminalState,
)
from ..runtime_event_publisher import ConversationRuntimeEventPublisher
from .filesystem import BindingResolver
from .path_confinement import AcpPathConfinement, AcpPathConfinementError

DefaultWorkingDirectory = Callable[[ConversationEmployee], Path]
TerminalPublicationFailureCallback = Callable[
    [
        ConversationEmployee,
        ConversationSessionBinding,
        int,
        Exception,
    ],
    Awaitable[None],
]


class AcpReverseTerminalError(RuntimeError):
    pass


@dataclass(slots=True)
class _TerminalHandle:
    employee: ConversationEmployee
    binding: ConversationSessionBinding
    child_generation: int
    terminal_id: str
    process: asyncio.subprocess.Process
    retained_byte_limit: int
    output: str = ""
    truncated: bool = False
    exit_status: TerminalExitStatus | None = None
    lifecycle: ConversationTerminalLifecycle = "active"
    version: int = 0
    published_version: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    published: asyncio.Condition = field(default_factory=asyncio.Condition)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    releasing: bool = False
    released: asyncio.Event = field(default_factory=asyncio.Event)
    stop_publication: bool = False
    publication_error: Exception | None = None
    reader_task: asyncio.Task[None] | None = None
    watcher_task: asyncio.Task[None] | None = None
    publication_task: asyncio.Task[None] | None = None


class ScopedAcpTerminalService:
    def __init__(
        self,
        binding_resolver: BindingResolver,
        publisher: ConversationRuntimeEventPublisher,
        *,
        terminal_enabled: bool,
        terminal_id_factory: Callable[[], str],
        base_environment: Mapping[str, str],
        default_working_directory: DefaultWorkingDirectory,
        output_max_bytes: int = ACP_REVERSE_TERMINAL_OUTPUT_MAX_BYTES,
        cleanup_timeout_seconds: float = ACP_REVERSE_TERMINAL_CLEANUP_TIMEOUT_SECONDS,
    ) -> None:
        if output_max_bytes < 0:
            raise ValueError("output_max_bytes must be non-negative")
        if cleanup_timeout_seconds <= 0:
            raise ValueError("cleanup_timeout_seconds must be positive")
        self._binding_resolver = binding_resolver
        self._publisher = publisher
        self._terminal_enabled = terminal_enabled
        self._terminal_id_factory = terminal_id_factory
        self._base_environment = dict(base_environment)
        self._default_working_directory = default_working_directory
        self._output_max_bytes = output_max_bytes
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._lock = asyncio.Lock()
        self._handles: dict[tuple[str, str, str], _TerminalHandle] = {}
        self._display: OrderedDict[tuple[str, str, str], ConversationTerminalState] = OrderedDict()
        self._confinements: dict[tuple[str, tuple[str, ...]], AcpPathConfinement] = {}
        self._closing = False
        self._shutdown_task: asyncio.Task[tuple[str, ...]] | None = None
        self._publication_failure_callback: (
            TerminalPublicationFailureCallback | None
        ) = None

    def set_publication_failure_callback(
        self, callback: TerminalPublicationFailureCallback
    ) -> None:
        current = self._publication_failure_callback
        if current is not None and current != callback:
            raise RuntimeError("terminal publication failure callback is already set")
        self._publication_failure_callback = callback

    async def create_terminal(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: CreateTerminalRequest,
    ) -> CreateTerminalResponse:
        binding = await self._validate_scope(employee, child_generation, request.session_id)
        requested_cwd = (
            Path(request.cwd)
            if request.cwd is not None
            else self._default_working_directory(employee)
        )
        try:
            cwd = self._confinement_for(employee).resolve_directory(requested_cwd)
        except AcpPathConfinementError as error:
            raise AcpReverseTerminalError("terminal cwd is outside the workspace") from error

        environment = dict(self._base_environment)
        for variable in request.env or []:
            self._validate_environment(variable.name, variable.value)
            environment[variable.name] = variable.value
        requested_limit = request.output_byte_limit
        if requested_limit is not None and requested_limit < 0:
            raise AcpReverseTerminalError("terminal output limit must be non-negative")
        retained_limit = (
            self._output_max_bytes
            if requested_limit is None
            else min(self._output_max_bytes, requested_limit)
        )
        terminal_id = self._terminal_id_factory()
        if not terminal_id:
            raise AcpReverseTerminalError("generated terminal ID is empty")
        key = (employee.employee_id, binding.acp_session_id, terminal_id)
        async with self._lock:
            if self._closing:
                raise AcpReverseTerminalError("terminal service is closing")
            if key in self._handles or key in self._display:
                raise AcpReverseTerminalError("terminal ID is already in use")
        try:
            process = await asyncio.create_subprocess_exec(
                request.command,
                *(request.args or []),
                cwd=str(cwd),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, ValueError) as error:
            raise AcpReverseTerminalError("terminal process could not be created") from error
        handle = _TerminalHandle(
            employee=employee,
            binding=binding,
            child_generation=child_generation,
            terminal_id=terminal_id,
            process=process,
            retained_byte_limit=retained_limit,
        )
        async with self._lock:
            if self._closing or key in self._handles or key in self._display:
                process.kill()
                await process.wait()
                raise AcpReverseTerminalError("terminal ID became unavailable")
            self._handles[key] = handle

        initial = self._snapshot(handle)
        try:
            await self._publisher.publish_terminal_state(employee, binding, initial)
        except asyncio.CancelledError:
            async with self._lock:
                self._handles.pop(key, None)
            process.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    process.wait(), timeout=self._cleanup_timeout_seconds
                )
            raise
        except Exception as error:
            async with self._lock:
                self._handles.pop(key, None)
            process.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    process.wait(), timeout=self._cleanup_timeout_seconds
                )
            self._signal_publication_failure(handle, error)
            raise
        self._display[key] = initial
        handle.reader_task = asyncio.create_task(
            self._read_output(handle), name=f"panels.acp.terminal-reader.{terminal_id}"
        )
        handle.publication_task = asyncio.create_task(
            self._publish_changes(handle),
            name=f"panels.acp.terminal-publisher.{terminal_id}",
        )
        handle.watcher_task = asyncio.create_task(
            self._watch_exit(handle), name=f"panels.acp.terminal-watcher.{terminal_id}"
        )
        return CreateTerminalResponse(terminal_id=terminal_id)

    async def terminal_output(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: TerminalOutputRequest,
    ) -> TerminalOutputResponse:
        handle = await self._handle_for(
            employee, child_generation, request.session_id, request.terminal_id
        )
        async with handle.state_lock:
            version = handle.version
        await self._wait_published(
            handle,
            version,
            asyncio.get_running_loop().time() + self._cleanup_timeout_seconds,
        )
        state = self._display[self._key(handle)]
        return state.terminal_output

    async def wait_for_terminal_exit(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: WaitForTerminalExitRequest,
    ) -> WaitForTerminalExitResponse:
        handle = await self._handle_for(
            employee, child_generation, request.session_id, request.terminal_id
        )
        watcher = handle.watcher_task
        assert watcher is not None
        try:
            await asyncio.shield(watcher)
        except asyncio.CancelledError:
            if handle.publication_error is not None:
                raise AcpReverseTerminalError(
                    "terminal state publisher failed"
                ) from handle.publication_error
            raise
        assert handle.exit_status is not None
        return WaitForTerminalExitResponse(
            exit_code=handle.exit_status.exit_code,
            signal=handle.exit_status.signal,
        )

    async def kill_terminal(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: KillTerminalRequest,
    ) -> KillTerminalResponse:
        handle = await self._handle_for(
            employee, child_generation, request.session_id, request.terminal_id
        )
        deadline = asyncio.get_running_loop().time() + self._cleanup_timeout_seconds
        if handle.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                handle.process.kill()
        watcher = handle.watcher_task
        assert watcher is not None
        if not await self._wait_task_until(watcher, deadline):
            self._force_dispose_handle(handle)
            raise AcpReverseTerminalError("terminal kill did not establish exit")
        if not watcher.cancelled():
            try:
                watcher.result()
            except Exception as error:
                self._force_dispose_handle(handle)
                raise AcpReverseTerminalError(
                    "terminal kill did not establish exit"
                ) from error
        return KillTerminalResponse()

    async def release_terminal(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: ReleaseTerminalRequest,
    ) -> ReleaseTerminalResponse:
        binding = await self._validate_scope(employee, child_generation, request.session_id)
        key = (employee.employee_id, binding.acp_session_id, request.terminal_id)
        async with self._lock:
            handle = self._handles.get(key)
            if handle is None:
                released = self._display.get(key)
                if released is not None and released.lifecycle == "released":
                    return ReleaseTerminalResponse()
                raise AcpReverseTerminalError("terminal is not live in this session")
            if handle.child_generation != child_generation:
                raise AcpReverseTerminalError("terminal is not live in this session")
        await self._release(
            handle, asyncio.get_running_loop().time() + self._cleanup_timeout_seconds
        )
        return ReleaseTerminalResponse()

    def display_snapshots(
        self, binding: ConversationSessionBinding
    ) -> tuple[ConversationTerminalState, ...]:
        return tuple(
            state
            for (employee_id, session_id, _terminal_id), state in self._display.items()
            if employee_id == binding.employee_id and session_id == binding.acp_session_id
        )

    async def cleanup_child_generation(
        self,
        employee_id: str,
        session_id: str,
        child_generation: int,
        deadline: float,
    ) -> tuple[str, ...]:
        handles = [
            handle
            for key, handle in self._handles.items()
            if key[0] == employee_id
            and key[1] == session_id
            and handle.child_generation == child_generation
        ]
        return await self._release_many(handles, deadline)

    async def prepare_new_conversation(
        self,
        binding: ConversationSessionBinding,
        deadline: float,
    ) -> tuple[str, ...]:
        handles = [
            handle
            for key, handle in self._handles.items()
            if key[0] == binding.employee_id and key[1] == binding.acp_session_id
        ]
        unfinished = await self._release_many(handles, deadline)
        for key in tuple(self._display):
            if key[0] == binding.employee_id and key[1] == binding.acp_session_id:
                self._display.pop(key, None)
        return unfinished

    async def shutdown(self, deadline: float) -> tuple[str, ...]:
        async with self._lock:
            task = self._shutdown_task
            if task is None:
                self._closing = True
                handles = tuple(self._handles.values())
                task = asyncio.create_task(
                    self._shutdown_owned(handles, deadline),
                    name="panels.acp.terminals.shutdown",
                )
                self._shutdown_task = task
        return await asyncio.shield(task)

    async def _shutdown_owned(
        self, handles: tuple[_TerminalHandle, ...], deadline: float
    ) -> tuple[str, ...]:
        unfinished = await self._release_many(list(handles), deadline)
        residual = tuple(self._handles.values())
        if residual:
            unfinished = tuple(
                sorted(
                    set(unfinished)
                    | {handle.terminal_id for handle in residual}
                )
            )
            for handle in residual:
                self._force_dispose_handle(handle)
        self._display.clear()
        confinements, self._confinements = self._confinements, {}
        for confinement in confinements.values():
            confinement.close()
        return unfinished

    async def _read_output(self, handle: _TerminalHandle) -> None:
        stream = handle.process.stdout
        assert stream is not None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            decoded = decoder.decode(chunk)
            if decoded:
                await self._append_output(handle, decoded)
        final = decoder.decode(b"", final=True)
        if final:
            await self._append_output(handle, final)

    async def _append_output(self, handle: _TerminalHandle, text: str) -> None:
        async with handle.state_lock:
            combined = handle.output + text
            limit = handle.retained_byte_limit
            if limit == 0:
                if text:
                    handle.truncated = True
                combined = ""
            else:
                while len(combined.encode("utf-8")) > limit:
                    combined = combined[1:]
                    handle.truncated = True
            handle.output = combined
            handle.version += 1
            handle.changed.set()

    async def _watch_exit(self, handle: _TerminalHandle) -> None:
        return_code = await handle.process.wait()
        reader = handle.reader_task
        assert reader is not None
        await reader
        if return_code >= 0:
            exit_status = TerminalExitStatus(exit_code=return_code, signal=None)
        else:
            try:
                signal_name = signal.Signals(-return_code).name
            except ValueError:
                signal_name = f"SIG{-return_code}"
            exit_status = TerminalExitStatus(exit_code=None, signal=signal_name)
        async with handle.state_lock:
            handle.exit_status = exit_status
            handle.version += 1
            version = handle.version
            handle.changed.set()
        await self._wait_published(
            handle,
            version,
            asyncio.get_running_loop().time() + self._cleanup_timeout_seconds,
        )

    async def _publish_changes(self, handle: _TerminalHandle) -> None:
        try:
            while True:
                await handle.changed.wait()
                while True:
                    async with handle.state_lock:
                        version = handle.version
                        if version <= handle.published_version:
                            handle.changed.clear()
                            break
                        state = self._snapshot(handle)
                    await self._publisher.publish_terminal_state(
                        handle.employee, handle.binding, state
                    )
                    self._display[self._key(handle)] = state
                    async with handle.published:
                        handle.published_version = version
                        handle.published.notify_all()
                    async with handle.state_lock:
                        if handle.version == version:
                            handle.changed.clear()
                            break
                if handle.stop_publication and handle.published_version >= handle.version:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            handle.publication_error = error
            if handle.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    handle.process.kill()
            async with handle.published:
                handle.published.notify_all()
            self._signal_publication_failure(handle, error)
            self._force_dispose_handle(handle)

    def _signal_publication_failure(
        self, handle: _TerminalHandle, error: Exception
    ) -> None:
        callback = self._publication_failure_callback
        if callback is None:
            return

        async def notify() -> None:
            await callback(
                handle.employee,
                handle.binding,
                handle.child_generation,
                error,
            )

        task: asyncio.Task[None] = asyncio.create_task(
            notify(),
            name=(
                f"panels.acp.terminal-publication-failure."
                f"{handle.terminal_id}"
            ),
        )
        task.add_done_callback(self._consume_failure_callback)

    @staticmethod
    def _consume_failure_callback(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            return

    def _force_dispose_handle(self, handle: _TerminalHandle) -> None:
        if handle.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                handle.process.kill()
        tasks = [
            task
            for task in (
                handle.reader_task,
                handle.watcher_task,
                handle.publication_task,
            )
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        handle.reader_task = None
        handle.watcher_task = None
        handle.publication_task = None
        self._handles.pop(self._key(handle), None)
        handle.released.set()

    async def _release(self, handle: _TerminalHandle, deadline: float) -> None:
        async with handle.state_lock:
            if handle.lifecycle == "released":
                return
            if handle.releasing:
                already_releasing = True
            else:
                handle.releasing = True
                already_releasing = False
        if already_releasing:
            released_wait = asyncio.create_task(handle.released.wait())
            if not await self._wait_task_until(released_wait, deadline):
                released_wait.cancel()
                self._force_dispose_handle(handle)
                raise AcpReverseTerminalError(
                    "terminal release exceeded its deadline"
                )
            return

        process = handle.process
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            process_wait = asyncio.create_task(process.wait())
            grace_deadline = asyncio.get_running_loop().time() + max(
                0.0, (deadline - asyncio.get_running_loop().time()) / 2
            )
            if not await self._wait_task_until(process_wait, grace_deadline):
                process_wait.cancel()
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
        watcher = handle.watcher_task
        assert watcher is not None
        if not await self._wait_task_until(watcher, deadline):
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            self._force_dispose_handle(handle)
            raise AcpReverseTerminalError("terminal release exceeded its deadline") from None
        if watcher.cancelled():
            self._force_dispose_handle(handle)
            raise AcpReverseTerminalError("terminal release watcher was cancelled")
        try:
            watcher.result()
        except asyncio.CancelledError:
            self._force_dispose_handle(handle)
            raise
        except Exception as error:
            self._force_dispose_handle(handle)
            raise AcpReverseTerminalError("terminal publication failed during release") from error

        async with handle.state_lock:
            handle.lifecycle = "released"
            handle.version += 1
            version = handle.version
            handle.stop_publication = True
            handle.changed.set()
        try:
            await self._wait_published(handle, version, deadline)
        except asyncio.CancelledError:
            self._force_dispose_handle(handle)
            raise
        except Exception as error:
            self._force_dispose_handle(handle)
            raise AcpReverseTerminalError("terminal release state could not publish") from error
        publication = handle.publication_task
        assert publication is not None
        if not await self._wait_task_until(publication, deadline):
            self._force_dispose_handle(handle)
            raise AcpReverseTerminalError("terminal release exceeded its deadline")
        if not publication.cancelled():
            publication.result()
        async with self._lock:
            self._handles.pop(self._key(handle), None)
        handle.released.set()

    async def _release_many(
        self, handles: list[_TerminalHandle], deadline: float
    ) -> tuple[str, ...]:
        if not handles:
            return ()
        tasks = {asyncio.create_task(self._release(handle, deadline)): handle for handle in handles}
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, pending = await asyncio.wait(tasks, timeout=remaining)
        for task in done:
            if task.cancelled():
                continue
            try:
                task.result()
            except Exception:
                self._force_dispose_handle(tasks[task])
                continue
        unfinished = {tasks[task].terminal_id for task in pending}
        unfinished.update(
            tasks[task].terminal_id
            for task in done
            if not task.cancelled() and task.exception() is not None
        )
        for task in pending:
            handle = tasks[task]
            self._force_dispose_handle(handle)
            task.cancel()
        return tuple(sorted(unfinished))

    async def _handle_for(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        session_id: str,
        terminal_id: str,
    ) -> _TerminalHandle:
        binding = await self._validate_scope(employee, child_generation, session_id)
        key = (employee.employee_id, binding.acp_session_id, terminal_id)
        async with self._lock:
            handle = self._handles.get(key)
            if (
                handle is None
                or handle.child_generation != child_generation
                or handle.lifecycle != "active"
                or handle.releasing
            ):
                raise AcpReverseTerminalError("terminal is not live in this session")
            return handle

    async def _validate_scope(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        session_id: str,
    ) -> ConversationSessionBinding:
        if not self._terminal_enabled:
            raise AcpReverseTerminalError("terminal reverse service is disabled")
        result = self._binding_resolver(employee, child_generation)
        binding = await result if inspect.isawaitable(result) else result
        if (
            binding is None
            or binding.employee_id != employee.employee_id
            or binding.backend_key != employee.backend_key
            or binding.acp_session_id != session_id
        ):
            raise AcpReverseTerminalError("terminal request is outside the live session")
        return binding

    def _confinement_for(self, employee: ConversationEmployee) -> AcpPathConfinement:
        key = (employee.employee_id, tuple(str(root) for root in employee.workspace_roots))
        confinement = self._confinements.get(key)
        if confinement is None:
            confinement = AcpPathConfinement(employee.workspace_roots)
            self._confinements[key] = confinement
        return confinement

    @staticmethod
    def _validate_environment(name: str, value: str) -> None:
        if (
            not name
            or name != name.strip()
            or "=" in name
            or "\x00" in name
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
            or "\x00" in value
        ):
            raise AcpReverseTerminalError("terminal environment entry is invalid")

    @staticmethod
    def _key(handle: _TerminalHandle) -> tuple[str, str, str]:
        return (
            handle.employee.employee_id,
            handle.binding.acp_session_id,
            handle.terminal_id,
        )

    @staticmethod
    def _snapshot(handle: _TerminalHandle) -> ConversationTerminalState:
        return ConversationTerminalState(
            terminal_id=handle.terminal_id,
            lifecycle=handle.lifecycle,
            terminal_output=TerminalOutputResponse(
                output=handle.output,
                truncated=handle.truncated,
                exit_status=handle.exit_status,
            ),
        )

    @staticmethod
    async def _wait_published(handle: _TerminalHandle, version: int, deadline: float) -> None:
        async def wait() -> None:
            async with handle.published:
                await handle.published.wait_for(
                    lambda: (
                        handle.published_version >= version or handle.publication_error is not None
                    )
                )

        wait_task = asyncio.create_task(wait())
        if not await ScopedAcpTerminalService._wait_task_until(wait_task, deadline):
            wait_task.cancel()
            raise TimeoutError
        if handle.publication_error is not None:
            raise AcpReverseTerminalError(
                "terminal state publisher failed"
            ) from handle.publication_error

    @staticmethod
    async def _wait_task_until(task: asyncio.Task[Any], deadline: float) -> bool:
        if task.done():
            return True
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        if remaining == 0:
            return False
        done, _pending = await asyncio.wait({task}, timeout=remaining)
        return bool(done)
