"""Official-SDK ACP child process and lifecycle ownership."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from acp import PROTOCOL_VERSION
from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError
from acp.interfaces import Client
from acp.schema import (
    CancelNotification,
    ClientCapabilities,
    CreateTerminalRequest,
    CreateTerminalResponse,
    EnvVariable,
    FileSystemCapabilities,
    ForkSessionRequest,
    ForkSessionResponse,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    KillTerminalRequest,
    KillTerminalResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    ReadTextFileRequest,
    ReadTextFileResponse,
    ReleaseTerminalRequest,
    ReleaseTerminalResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    SetSessionConfigOptionResponse,
    TerminalOutputRequest,
    TerminalOutputResponse,
    WaitForTerminalExitRequest,
    WaitForTerminalExitResponse,
    WriteTextFileRequest,
    WriteTextFileResponse,
)
from acp.stdio import spawn_agent_process
from acp.transports import default_environment

from planner import __version__

from .backend_contracts import (
    AcpConversationIngress,
    AcpEmployeeChild,
    AgentBackendDefinition,
    ChildDeathCallback,
    PermissionRequestCallback,
)
from .configuration import ACP_CHILD_STDERR_TAIL_MAX_BYTES
from .contracts import ConversationEmployee
from .ordered_ingress import (
    AcpSessionUpdateIngressClosed,
    AcpSessionUpdateIngressOverflow,
    OrderedAcpConversationIngress,
)
from .runtime_ports import AcpFilesystemRuntimePort, AcpTerminalRuntimePort


class AcpChildError(RuntimeError):
    pass


class AcpChildInitializeMismatch(AcpChildError):
    pass


class AcpChildProcessExited(AcpChildError):
    def __init__(self, return_code: int | None, stderr_tail: str) -> None:
        detail = f"ACP agent exited unexpectedly with status {return_code}"
        if stderr_tail:
            detail = f"{detail}; stderr tail: {stderr_tail}"
        super().__init__(detail)
        self.return_code = return_code
        self.stderr_tail = stderr_tail


class AcpChildUnsupportedReverseService(AcpChildError):
    pass


class AcpChildEnvironmentPolicyError(AcpChildError):
    pass


def build_panels_initialize_request(
    definition: AgentBackendDefinition,
) -> InitializeRequest:
    reverse = definition.reverse_service_capabilities
    return InitializeRequest(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(
            fs=FileSystemCapabilities(
                read_text_file=reverse.filesystem,
                write_text_file=reverse.filesystem,
            ),
            terminal=reverse.terminal,
        ),
        client_info=Implementation(name="panels", title="Panels", version=__version__),
    )


def build_confined_child_environment(
    definition: AgentBackendDefinition,
    employee: ConversationEmployee,
    *,
    ambient_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    ambient = os.environ if ambient_environment is None else ambient_environment
    sdk_defaults = default_environment()
    undeclared_defaults = sorted(
        name for name in sdk_defaults if name not in definition.inherited_environment_names
    )
    if undeclared_defaults:
        raise AcpChildEnvironmentPolicyError(
            "backend does not declare SDK default environment names: "
            + ", ".join(undeclared_defaults)
        )

    environment = {
        name: ambient[name] for name in definition.inherited_environment_names if name in ambient
    }
    environment.update(definition.environment_overrides)
    for name in tuple(environment):
        if name.startswith("PLAN_") or name == "HERMES_TUI_SKILLS":
            del environment[name]

    environment["PLAN_ACTOR"] = "worker" if employee.entity_kind == "ticket" else "chief"
    if employee.entity_kind == "ticket":
        environment["PLAN_TICKET_ID"] = employee.entity_id
    else:
        environment.pop("PLAN_TICKET_ID", None)
    return environment


class _BoundedStderrTail:
    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._tail = bytearray()
        self._changed = asyncio.Event()

    def append(self, chunk: bytes) -> None:
        self._tail.extend(chunk)
        overflow = len(self._tail) - self._max_bytes
        if overflow > 0:
            del self._tail[:overflow]
        self._changed.set()

    def text(self) -> str:
        return bytes(self._tail).decode("utf-8", errors="replace")

    async def wait_for_data(self) -> None:
        if self._tail:
            return
        await self._changed.wait()


class _SdkClientBridge:
    def __init__(
        self,
        ordered_ingress: OrderedAcpConversationIngress,
        permission_callback: PermissionRequestCallback,
        definition: AgentBackendDefinition,
        employee: ConversationEmployee,
        child_generation: int,
        filesystem_service: AcpFilesystemRuntimePort | None,
        terminal_service: AcpTerminalRuntimePort | None,
    ) -> None:
        self._ordered_ingress = ordered_ingress
        self._permission_callback = permission_callback
        self._definition = definition
        self._employee = employee
        self._child_generation = child_generation
        self._filesystem_service = filesystem_service
        self._terminal_service = terminal_service

    def on_connect(self, agent: object) -> None:
        del agent

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        notification = SessionNotification(
            session_id=session_id,
            update=update,
            field_meta=kwargs or None,
        )
        self._ordered_ingress.fulfill_typed(notification)

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        if not self._definition.reverse_service_capabilities.permission:
            raise RequestError.method_not_found("session/request_permission")
        return await asyncio.shield(
            self._permission_callback(
                RequestPermissionRequest(
                    session_id=session_id,
                    tool_call=tool_call,
                    options=options,
                    field_meta=kwargs or None,
                )
            )
        )

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        if (
            not self._definition.reverse_service_capabilities.filesystem
            or self._filesystem_service is None
        ):
            raise RequestError.method_not_found("fs/read_text_file")
        return await self._filesystem_service.read_text_file(
            self._employee,
            self._child_generation,
            ReadTextFileRequest(
                session_id=session_id,
                path=path,
                line=line,
                limit=limit,
                field_meta=kwargs or None,
            ),
        )

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse:
        if (
            not self._definition.reverse_service_capabilities.filesystem
            or self._filesystem_service is None
        ):
            raise RequestError.method_not_found("fs/write_text_file")
        return await self._filesystem_service.write_text_file(
            self._employee,
            self._child_generation,
            WriteTextFileRequest(
                session_id=session_id,
                path=path,
                content=content,
                field_meta=kwargs or None,
            ),
        )

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        service = self._require_terminal_service("terminal/create")
        return await service.create_terminal(
            self._employee,
            self._child_generation,
            CreateTerminalRequest(
                session_id=session_id,
                command=command,
                args=args,
                env=env,
                cwd=cwd,
                output_byte_limit=output_byte_limit,
                field_meta=kwargs or None,
            ),
        )

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> TerminalOutputResponse:
        service = self._require_terminal_service("terminal/output")
        return await service.terminal_output(
            self._employee,
            self._child_generation,
            TerminalOutputRequest(
                session_id=session_id,
                terminal_id=terminal_id,
                field_meta=kwargs or None,
            ),
        )

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        service = self._require_terminal_service("terminal/wait_for_exit")
        return await service.wait_for_terminal_exit(
            self._employee,
            self._child_generation,
            WaitForTerminalExitRequest(
                session_id=session_id,
                terminal_id=terminal_id,
                field_meta=kwargs or None,
            ),
        )

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> KillTerminalResponse:
        service = self._require_terminal_service("terminal/kill")
        return await service.kill_terminal(
            self._employee,
            self._child_generation,
            KillTerminalRequest(
                session_id=session_id,
                terminal_id=terminal_id,
                field_meta=kwargs or None,
            ),
        )

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse:
        service = self._require_terminal_service("terminal/release")
        return await service.release_terminal(
            self._employee,
            self._child_generation,
            ReleaseTerminalRequest(
                session_id=session_id,
                terminal_id=terminal_id,
                field_meta=kwargs or None,
            ),
        )

    def _require_terminal_service(self, method: str) -> AcpTerminalRuntimePort:
        if (
            not self._definition.reverse_service_capabilities.terminal
            or self._terminal_service is None
        ):
            raise RequestError.method_not_found(method)
        return self._terminal_service


@dataclass(slots=True)
class _SpawnedValues:
    connection: ClientSideConnection
    process: asyncio.subprocess.Process


class SdkAcpEmployeeChild(AcpEmployeeChild):
    def __init__(
        self,
        *,
        generation: int,
        definition: AgentBackendDefinition,
        ordered_ingress: OrderedAcpConversationIngress,
        close_event: asyncio.Event,
        lifetime_task: asyncio.Task[None],
        spawned: _SpawnedValues,
        stderr_tail: _BoundedStderrTail,
    ) -> None:
        self._generation = generation
        self._definition = definition
        self._ordered_ingress = ordered_ingress
        self._close_event = close_event
        self._lifetime_task = lifetime_task
        self._connection = spawned.connection
        self._process = spawned.process
        self._stderr_tail = stderr_tail
        self._intentional_close = False
        self._close_lock = asyncio.Lock()
        self._load_lock = asyncio.Lock()
        self._supports_session_fork = False

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def alive(self) -> bool:
        return (
            not self._intentional_close
            and not self._lifetime_task.done()
            and self._process.returncode is None
            and self._ordered_ingress.fatal_error is None
        )

    @property
    def supports_session_fork(self) -> bool:
        return self._supports_session_fork

    @property
    def stderr_tail(self) -> str:
        return self._stderr_tail.text()

    async def wait_for_stderr_data(self) -> None:
        await self._stderr_tail.wait_for_data()

    def _require_alive(self) -> None:
        if not self.alive:
            raise AcpChildError("ACP child generation is not alive")

    async def initialize(self, request: InitializeRequest) -> InitializeResponse:
        self._require_alive()
        response = await self._connection.initialize(
            protocol_version=request.protocol_version,
            client_capabilities=request.client_capabilities,
            client_info=request.client_info,
            **(request.field_meta or {}),
        )
        mismatch: str | None = None
        if response.protocol_version != PROTOCOL_VERSION:
            mismatch = f"protocol {response.protocol_version}, expected {PROTOCOL_VERSION}"
        elif response.agent_info is None:
            mismatch = "missing agent identity"
        elif response.agent_info.name != self._definition.expected_agent_name:
            mismatch = (
                f"agent {response.agent_info.name!r}, expected "
                f"{self._definition.expected_agent_name!r}"
            )
        elif response.agent_info.version != self._definition.expected_agent_version:
            mismatch = (
                f"agent version {response.agent_info.version!r}, expected "
                f"{self._definition.expected_agent_version!r}"
            )
        elif (
            response.agent_capabilities is None
            or response.agent_capabilities.load_session is not True
        ):
            mismatch = "agent does not support session/load"
        if mismatch is not None:
            await self.close()
            raise AcpChildInitializeMismatch(mismatch)
        assert response.agent_capabilities is not None
        session_capabilities = response.agent_capabilities.session_capabilities
        self._supports_session_fork = (
            session_capabilities is not None and session_capabilities.fork is not None
        )
        return response

    async def new_session(self, request: NewSessionRequest) -> NewSessionResponse:
        self._require_alive()
        return await self._connection.new_session(
            cwd=request.cwd,
            additional_directories=request.additional_directories,
            mcp_servers=request.mcp_servers,
            **(request.field_meta or {}),
        )

    async def set_config_option(
        self, session_id: str, config_id: str, value: str
    ) -> SetSessionConfigOptionResponse:
        self._require_alive()
        return await self._connection.set_config_option(
            config_id=config_id,
            session_id=session_id,
            value=value,
        )

    async def set_legacy_session_model(self, session_id: str, model_id: str) -> None:
        """Send the removed ACP model request for a backend that still implements it."""

        self._require_alive()
        raw_connection = cast(Any, self._connection)._conn
        await raw_connection.send_request(
            "session/set_model",
            {"sessionId": session_id, "modelId": model_id},
        )

    async def close_session(self, session_id: str) -> None:
        self._require_alive()
        await self._connection.close_session(session_id=session_id)

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        async with self._load_lock:
            self._require_alive()
            self._ordered_ingress.begin_load_epoch()
            try:
                response = await self._connection.load_session(
                    cwd=request.cwd,
                    session_id=request.session_id,
                    mcp_servers=request.mcp_servers,
                    additional_directories=request.additional_directories,
                    **(request.field_meta or {}),
                )
            except BaseException:
                self._ordered_ingress.abort_load_epoch()
                raise
            await self._ordered_ingress.finish_load_epoch()
            return response

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse:
        self._require_alive()
        if not self._supports_session_fork:
            raise AcpChildError("ACP child does not advertise session/fork")
        response = await self._connection.fork_session(
            session_id=request.session_id,
            cwd=request.cwd,
            additional_directories=request.additional_directories,
            mcp_servers=request.mcp_servers,
            **(request.field_meta or {}),
        )
        if not response.session_id.strip():
            raise AcpChildError("session/fork returned an empty session ID")
        return response

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self._require_alive()
        epoch = self._ordered_ingress.begin_response_consumption_epoch("session/prompt")
        try:
            response = await self._connection.prompt(
                session_id=request.session_id,
                prompt=request.prompt,
                **(request.field_meta or {}),
            )
        except BaseException:
            self._ordered_ingress.abort_response_consumption_epoch(epoch)
            raise
        await self._ordered_ingress.finish_response_consumption_epoch(epoch)
        return response

    async def capture_load_session(
        self,
        request: LoadSessionRequest,
        private_ingress: AcpConversationIngress,
    ) -> LoadSessionResponse:
        """Load one fresh unpublished generation into a private typed sink."""

        async with self._load_lock:
            self._require_alive()
            epoch = self._ordered_ingress.begin_response_consumption_epoch(
                "session/load",
                private_ingress=private_ingress,
                private_session_id=request.session_id,
            )
            try:
                response = await self._connection.load_session(
                    cwd=request.cwd,
                    session_id=request.session_id,
                    mcp_servers=request.mcp_servers,
                    additional_directories=request.additional_directories,
                    **(request.field_meta or {}),
                )
            except BaseException:
                self._ordered_ingress.abort_response_consumption_epoch(epoch)
                raise
            await self._ordered_ingress.finish_response_consumption_epoch(epoch)
            return response

    async def cancel(self, notification: CancelNotification) -> None:
        self._require_alive()
        await self._connection.cancel(
            session_id=notification.session_id,
            **(notification.field_meta or {}),
        )

    async def close(self) -> None:
        async with self._close_lock:
            if self._intentional_close:
                task = self._lifetime_task
            else:
                self._intentional_close = True
                self._close_event.set()
                task = self._lifetime_task
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def force_close(self) -> None:
        self._intentional_close = True
        self._close_event.set()
        self._ordered_ingress.retire_without_wait(
            drain=False,
            cause=AcpSessionUpdateIngressClosed("ACP child was force-closed"),
        )
        if self._process.returncode is None:
            self._process.kill()
        self._lifetime_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, ProcessLookupError):
            await self._lifetime_task


class SdkAcpEmployeeChildFactory:
    def __init__(
        self,
        definition: AgentBackendDefinition,
        *,
        stderr_tail_max_bytes: int = ACP_CHILD_STDERR_TAIL_MAX_BYTES,
        ingress_max_items: int | None = None,
        filesystem_service: AcpFilesystemRuntimePort | None = None,
        terminal_service: AcpTerminalRuntimePort | None = None,
    ) -> None:
        if stderr_tail_max_bytes <= 0:
            raise ValueError("stderr_tail_max_bytes must be positive")
        self.definition = definition
        self._stderr_tail_max_bytes = stderr_tail_max_bytes
        self._ingress_max_items = ingress_max_items
        self._filesystem_service = filesystem_service
        self._terminal_service = terminal_service

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: AcpConversationIngress,
        permission_callback: PermissionRequestCallback,
        death_callback: ChildDeathCallback,
    ) -> SdkAcpEmployeeChild:
        if generation <= 0:
            raise ValueError("generation must be positive")
        reverse = self.definition.reverse_service_capabilities
        if reverse.filesystem and self._filesystem_service is None:
            raise AcpChildUnsupportedReverseService(
                "filesystem capability requires the complete reverse service"
            )
        if reverse.terminal and self._terminal_service is None:
            raise AcpChildUnsupportedReverseService(
                "terminal capability requires the complete reverse service"
            )
        working_directory = self.definition.working_directory_for(employee)
        environment = build_confined_child_environment(self.definition, employee)
        close_event = asyncio.Event()
        fatal_event = asyncio.Event()
        fatal_holder: list[BaseException] = []
        stderr_tail = _BoundedStderrTail(self._stderr_tail_max_bytes)
        death_settled = False
        death_lock = asyncio.Lock()

        async def settle_death(error: BaseException | None) -> None:
            nonlocal death_settled
            async with death_lock:
                if death_settled:
                    return
                death_settled = True
            await death_callback(error)

        def on_ingress_fatal(error: BaseException) -> None:
            if not fatal_holder:
                fatal_holder.append(error)
                fatal_event.set()

        ingress_kwargs: dict[str, Any] = {"fatal_callback": on_ingress_fatal}
        if self._ingress_max_items is not None:
            ingress_kwargs["max_items"] = self._ingress_max_items
        ordered_ingress = OrderedAcpConversationIngress(update_ingress, **ingress_kwargs)
        ordered_ingress.start()
        client = _SdkClientBridge(
            ordered_ingress,
            permission_callback,
            self.definition,
            employee,
            generation,
            self._filesystem_service,
            self._terminal_service,
        )
        ready: asyncio.Future[_SpawnedValues] = asyncio.get_running_loop().create_future()
        intentional_close = False

        async def drain_stderr(process: asyncio.subprocess.Process) -> None:
            if process.stderr is None:
                return
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    return
                stderr_tail.append(chunk)

        async def lifetime() -> None:
            nonlocal intentional_close
            error: BaseException | None = None
            task_cancelled = False
            try:
                async with spawn_agent_process(
                    cast(Client, client),
                    self.definition.argv[0],
                    *self.definition.argv[1:],
                    env=environment,
                    cwd=working_directory,
                    observers=[ordered_ingress.observe_stream],
                ) as (connection, process):
                    stderr_task = asyncio.create_task(
                        drain_stderr(process), name=f"panels.acp.stderr.{employee.employee_id}"
                    )
                    if not ready.done():
                        ready.set_result(_SpawnedValues(connection, process))
                    close_wait = asyncio.create_task(close_event.wait())
                    fatal_wait = asyncio.create_task(fatal_event.wait())
                    process_wait = asyncio.create_task(process.wait())
                    done, pending = await asyncio.wait(
                        (close_wait, fatal_wait, process_wait),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    intentional_close = close_wait in done and close_event.is_set()
                    if fatal_wait in done and fatal_event.is_set():
                        error = fatal_holder[0]
                        if isinstance(error, AcpSessionUpdateIngressOverflow):
                            # Raw-accepted reservations remain owned even after
                            # overflow rejects further frames. Keep the SDK
                            # connection alive until their typed callbacks arrive
                            # and the serial sink has consumed the complete prefix.
                            await ordered_ingress.wait_until_reserved_fulfilled()
                            await ordered_ingress.wait_until_accepted_drained()
                    elif process_wait in done and not intentional_close:
                        await stderr_task
                        error = AcpChildProcessExited(process.returncode, stderr_tail.text())
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    if not stderr_task.done():
                        # The SDK context closes stdin/process immediately after this
                        # block, so do not await EOF while still inside the context.
                        stderr_task.cancel()
                        await asyncio.gather(stderr_task, return_exceptions=True)
            except asyncio.CancelledError:
                task_cancelled = True
                if not ready.done():
                    ready.cancel()
                raise
            except BaseException as exc:
                error = exc
                if not ready.done():
                    ready.set_exception(exc)
            finally:
                await ordered_ingress.close(
                    drain=intentional_close and error is None and not task_cancelled,
                    cause=error
                    or AcpSessionUpdateIngressClosed("ACP child connection lifetime ended"),
                )
                await settle_death(None if intentional_close else error)

        lifetime_task = asyncio.create_task(
            lifetime(), name=f"panels.acp.child.{employee.employee_id}.{generation}"
        )
        try:
            spawned = await ready
        except BaseException:
            lifetime_task.cancel()
            await asyncio.gather(lifetime_task, return_exceptions=True)
            raise
        return SdkAcpEmployeeChild(
            generation=generation,
            definition=self.definition,
            ordered_ingress=ordered_ingress,
            close_event=close_event,
            lifetime_task=lifetime_task,
            spawned=spawned,
            stderr_tail=stderr_tail,
        )
