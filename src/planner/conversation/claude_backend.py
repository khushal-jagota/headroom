"""Locked Claude Code ACP definition, decoration, and startup preflight."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

from acp.schema import (
    AgentMessageChunk,
    CancelNotification,
    ForkSessionRequest,
    ForkSessionResponse,
    InitializeRequest,
    InitializeResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionInfoUpdate,
    SessionNotification,
    SetSessionConfigOptionResponse,
    TextContentBlock,
)

from .backend_contracts import (
    AcpConversationIngress,
    AcpEmployeeChild,
    AcpEmployeeChildFactory,
    AgentBackendDefinition,
    BackendTurnCapabilities,
    BackendTurnStrategy,
    ChildDeathCallback,
    PermissionRequestCallback,
    ReverseServiceCapabilities,
)
from .claude_turn_strategy import (
    CLAUDE_COMPACTION_METADATA_KEY,
    CLAUDE_COMPACTION_METADATA_NAMESPACE,
    ClaudeAcpTurnStrategy,
    is_claude_compaction_control,
)
from .contracts import ConversationEmployee
from .employee_configuration import StableAcpEmployeeSessionConfigurationAdapter
from .sdk_child import SdkAcpEmployeeChildFactory, build_panels_initialize_request
from .wire_contracts import ProtocolUpdateRejectedPayload

if TYPE_CHECKING:
    from .backend_catalog import (
        EmployeeBackendBuildContext,
        EmployeeBackendRegistration,
        MaterializedEmployeeBackendRegistration,
    )

CLAUDE_BACKEND_KEY: Final = "claude"
CLAUDE_ACP_AGENT_NAME: Final = "@agentclientprotocol/claude-agent-acp"
CLAUDE_ACP_AGENT_VERSION: Final = "0.60.0"
CLAUDE_MINIMUM_NODE_MAJOR_VERSION: Final = 22
CLAUDE_INHERITED_ENVIRONMENT_NAMES: Final = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "USER",
)
CLAUDE_ADAPTER_RELATIVE_ENTRYPOINT: Final = Path(
    "agent_backends/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js"
)
CLAUDE_ADAPTER_RELATIVE_MANIFEST: Final = Path(
    "agent_backends/node_modules/@agentclientprotocol/claude-agent-acp/package.json"
)
CLAUDE_STARTUP_PREFLIGHT_TIMEOUT_SECONDS: Final = 30.0
_NODE_VERSION = re.compile(r"^v(?P<major>[0-9]+)(?:\.[0-9]+){2}$")


class ClaudeBackendStartupError(RuntimeError):
    pass


def _first_workspace_root(employee: ConversationEmployee) -> Path:
    return employee.workspace_roots[0]


def _require_absolute_path(path: Path, *, field_name: str) -> None:
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be absolute")


def _validate_node_executable(node_executable: Path) -> None:
    _require_absolute_path(node_executable, field_name="node_executable")
    if not node_executable.is_file() or not os.access(node_executable, os.X_OK):
        raise ValueError(f"Node executable is missing or not executable: {node_executable}")
    try:
        result = subprocess.run(
            (str(node_executable), "--version"),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Node version probe failed") from error
    version = result.stdout.strip()
    match = _NODE_VERSION.fullmatch(version)
    if result.returncode != 0 or match is None:
        raise ValueError(f"Node version probe returned an invalid result: {version!r}")
    if int(match.group("major")) < CLAUDE_MINIMUM_NODE_MAJOR_VERSION:
        raise ValueError(
            f"Claude ACP requires Node {CLAUDE_MINIMUM_NODE_MAJOR_VERSION} or newer"
        )


def _validate_locked_adapter(repository_root: Path) -> Path:
    _require_absolute_path(repository_root, field_name="repository_root")
    adapter_entrypoint = repository_root / CLAUDE_ADAPTER_RELATIVE_ENTRYPOINT
    adapter_manifest = repository_root / CLAUDE_ADAPTER_RELATIVE_MANIFEST
    if not adapter_entrypoint.is_file():
        raise ValueError(f"Claude ACP adapter entrypoint is missing: {adapter_entrypoint}")
    try:
        manifest = json.loads(adapter_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Claude ACP adapter manifest is missing or invalid") from error
    if not isinstance(manifest, dict):
        raise ValueError("Claude ACP adapter manifest must be an object")
    if manifest.get("name") != CLAUDE_ACP_AGENT_NAME:
        raise ValueError("Claude ACP adapter manifest has the wrong package name")
    if manifest.get("version") != CLAUDE_ACP_AGENT_VERSION:
        raise ValueError("Claude ACP adapter manifest has the wrong package version")
    return adapter_entrypoint


def build_claude_acp_backend_definition(
    *,
    repository_root: Path,
    node_executable: Path,
    turn_strategy: BackendTurnStrategy,
) -> AgentBackendDefinition:
    """Resolve one immutable definition from the project-local locked package."""

    _validate_node_executable(node_executable)
    adapter_entrypoint = _validate_locked_adapter(repository_root)
    return AgentBackendDefinition(
        backend_key=CLAUDE_BACKEND_KEY,
        argv=(str(node_executable), str(adapter_entrypoint)),
        inherited_environment_names=CLAUDE_INHERITED_ENVIRONMENT_NAMES,
        environment_overrides=(),
        expected_agent_name=CLAUDE_ACP_AGENT_NAME,
        expected_agent_version=CLAUDE_ACP_AGENT_VERSION,
        turn_capabilities=BackendTurnCapabilities(
            supports_steer=False,
            observes_compaction=True,
            requires_fresh_child_after_requested_cancel=True,
        ),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False,
            terminal=False,
            permission=True,
        ),
        working_directory_resolver=_first_workspace_root,
        turn_strategy=turn_strategy,
    )


def _resolve_node_executable() -> Path:
    candidate = shutil.which("node")
    if candidate is None:
        raise FileNotFoundError("Claude ACP requires Node on PATH")
    return Path(candidate).resolve(strict=True)


def build_claude_employee_backend_registration() -> EmployeeBackendRegistration:
    """Return the catalog entry without probing or spawning at import time."""

    from .backend_catalog import (
        EmployeeBackendRegistration,
        MaterializedEmployeeBackendRegistration,
    )

    def materialize(
        context: EmployeeBackendBuildContext,
    ) -> MaterializedEmployeeBackendRegistration:
        strategy = ClaudeAcpTurnStrategy()
        definition = build_claude_acp_backend_definition(
            repository_root=context.repository_root,
            node_executable=_resolve_node_executable(),
            turn_strategy=strategy,
        )
        child_factory = ClaudeAcpEmployeeChildFactory(
            definition,
        )
        employee_configuration_adapter = StableAcpEmployeeSessionConfigurationAdapter(
            definition=definition,
            child_factory=child_factory,
            workspace_root=context.repository_root,
            full_access_mode="bypassPermissions",
        )
        preflight = ClaudeBackendStartupPreflight(
            definition=definition,
            child_factory=child_factory,
            repository_root=context.repository_root,
        )
        return MaterializedEmployeeBackendRegistration(
            definition=definition,
            child_factory=child_factory,
            is_executable=preflight.is_executable,
            startup_preflight=preflight.run,
            employee_configuration_adapter=employee_configuration_adapter,
        )

    return EmployeeBackendRegistration(
        backend_key=CLAUDE_BACKEND_KEY,
        runtime_builder=materialize,
    )


def _normalize_claude_ingress(
    payload: SessionNotification | ProtocolUpdateRejectedPayload,
) -> SessionNotification | ProtocolUpdateRejectedPayload:
    if isinstance(payload, ProtocolUpdateRejectedPayload):
        return payload
    update = payload.update
    if (
        not isinstance(update, AgentMessageChunk)
        or not isinstance(update.content, TextContentBlock)
        or not is_claude_compaction_control(update.content.text)
    ):
        return payload
    control = SessionInfoUpdate(
        session_update="session_info_update",
        field_meta={
            CLAUDE_COMPACTION_METADATA_NAMESPACE: {
                CLAUDE_COMPACTION_METADATA_KEY: update.content.text
            }
        },
    )
    return payload.model_copy(update={"update": control})


class _ClaudeAcpEmployeeChild(AcpEmployeeChild):
    def __init__(self, delegate: AcpEmployeeChild) -> None:
        self._delegate = delegate

    @property
    def generation(self) -> int:
        return self._delegate.generation

    @property
    def alive(self) -> bool:
        return self._delegate.alive

    @property
    def supports_session_fork(self) -> bool:
        return self._delegate.supports_session_fork

    async def initialize(self, request: InitializeRequest) -> InitializeResponse:
        return await self._delegate.initialize(request)

    async def new_session(self, request: NewSessionRequest) -> NewSessionResponse:
        return await self._delegate.new_session(request)

    async def set_config_option(
        self, session_id: str, config_id: str, value: str
    ) -> SetSessionConfigOptionResponse:
        return await self._delegate.set_config_option(session_id, config_id, value)

    async def set_session_mode(self, session_id: str, mode_id: str) -> None:
        await self._delegate.set_session_mode(session_id, mode_id)

    async def close_session(self, session_id: str) -> None:
        await self._delegate.close_session(session_id)

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        return await self._delegate.load_session(request)

    async def capture_load_session(
        self,
        request: LoadSessionRequest,
        private_ingress: AcpConversationIngress,
    ) -> LoadSessionResponse:
        async def normalized_private_ingress(
            payload: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            await private_ingress(_normalize_claude_ingress(payload))

        return await self._delegate.capture_load_session(
            request,
            cast(AcpConversationIngress, normalized_private_ingress),
        )

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse:
        return await self._delegate.fork_session(request)

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        return await self._delegate.prompt(request)

    async def cancel(self, notification: CancelNotification) -> None:
        await self._delegate.cancel(notification)

    async def close(self) -> None:
        await self._delegate.close()

    async def force_close(self) -> None:
        force_close = getattr(self._delegate, "force_close", None)
        if force_close is None:
            await self._delegate.close()
            return
        await force_close()


class ClaudeAcpEmployeeChildFactory(AcpEmployeeChildFactory):
    """Decorate the generic SDK child without taking lifecycle ownership from it."""

    def __init__(
        self,
        definition: AgentBackendDefinition,
        *,
        delegate_factory: AcpEmployeeChildFactory | None = None,
    ) -> None:
        if definition.backend_key != CLAUDE_BACKEND_KEY:
            raise ValueError("Claude child factory requires the Claude backend definition")
        self.definition = definition
        self._delegate_factory = delegate_factory or SdkAcpEmployeeChildFactory(definition)

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: AcpConversationIngress,
        permission_callback: PermissionRequestCallback,
        death_callback: ChildDeathCallback,
    ) -> AcpEmployeeChild:
        async def normalized_ingress(
            payload: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            await update_ingress(_normalize_claude_ingress(payload))

        delegate = await self._delegate_factory.create(
            employee,
            generation,
            cast(AcpConversationIngress, normalized_ingress),
            permission_callback,
            death_callback,
        )
        return _ClaudeAcpEmployeeChild(delegate)


class ClaudeBackendStartupPreflight:
    """One-shot initialize-only validation owned by a materialized registration."""

    def __init__(
        self,
        *,
        definition: AgentBackendDefinition,
        child_factory: AcpEmployeeChildFactory,
        repository_root: Path,
        timeout_seconds: float = CLAUDE_STARTUP_PREFLIGHT_TIMEOUT_SECONDS,
    ) -> None:
        if definition.backend_key != CLAUDE_BACKEND_KEY:
            raise ValueError("Claude preflight requires the Claude backend definition")
        _require_absolute_path(repository_root, field_name="repository_root")
        if timeout_seconds <= 0:
            raise ValueError("Claude preflight timeout must be positive")
        self._definition = definition
        self._child_factory = child_factory
        self._repository_root = repository_root
        self._timeout_seconds = timeout_seconds
        self._state: str = "pending"

    def is_executable(self) -> bool:
        return self._state == "ready"

    async def run(self) -> None:
        if self._state != "pending":
            raise ClaudeBackendStartupError("Claude startup preflight is one-shot")
        self._state = "running"
        deadline = asyncio.get_running_loop().time() + self._timeout_seconds
        child: AcpEmployeeChild | None = None
        primary_error: BaseException | None = None
        try:
            child = cast(
                AcpEmployeeChild,
                await self._await_phase(
                    self._child_factory.create(
                        ConversationEmployee(
                            employee_id="claude-startup-preflight",
                            entity_kind="ticket",
                            entity_id="claude-startup-preflight",
                            workspace_roots=(self._repository_root,),
                            backend_key=CLAUDE_BACKEND_KEY,
                        ),
                        1,
                        cast(AcpConversationIngress, self._reject_update),
                        self._reject_permission,
                        self._observe_death,
                    ),
                    deadline,
                    "spawn",
                    abandoned_result_cleanup=self._force_close_abandoned_child,
                ),
            )
            response = cast(
                InitializeResponse,
                await self._await_phase(
                    child.initialize(build_panels_initialize_request(self._definition)),
                    deadline,
                    "initialize",
                ),
            )
            self._validate_response(response)
        except asyncio.CancelledError:
            self._state = "failed"
            raise
        except BaseException as error:
            primary_error = error
        finally:
            if child is not None:
                try:
                    await self._close_child(child, deadline)
                except BaseException as close_error:
                    if primary_error is None:
                        primary_error = close_error
        if primary_error is not None:
            self._state = "failed"
            if isinstance(primary_error, ClaudeBackendStartupError):
                raise primary_error
            raise ClaudeBackendStartupError(
                f"Claude startup preflight failed: {primary_error}"
            ) from primary_error
        self._state = "ready"

    async def _await_phase(
        self,
        operation: Awaitable[Any],
        deadline: float,
        phase: str,
        *,
        abandoned_result_cleanup: Callable[[Any], Awaitable[None]] | None = None,
    ) -> Any:
        task = asyncio.ensure_future(operation)
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            done, _pending = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError as cancellation:
            completed, result = await self._cancel_and_settle(task)
            if completed and abandoned_result_cleanup is not None:
                await abandoned_result_cleanup(result)
            raise cancellation
        if task not in done:
            completed, result = await self._cancel_and_settle(task)
            if completed and abandoned_result_cleanup is not None:
                await abandoned_result_cleanup(result)
            raise ClaudeBackendStartupError(
                "Claude startup preflight timed out during "
                f"{phase} after {self._timeout_seconds:g} seconds"
            )
        try:
            return task.result()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            raise ClaudeBackendStartupError(
                f"Claude startup preflight failed during {phase}: {error}"
            ) from error

    async def _close_child(self, child: AcpEmployeeChild, deadline: float) -> None:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        close_task = asyncio.create_task(child.close())
        try:
            done, _pending = await asyncio.wait({close_task}, timeout=remaining)
        except asyncio.CancelledError as cancellation:
            closed, _result = await self._cancel_and_settle(close_task)
            if not closed:
                try:
                    await self._force_close_child(child)
                except BaseException as close_error:
                    raise cancellation from close_error
            raise cancellation
        if close_task in done:
            try:
                close_task.result()
            except BaseException as error:
                try:
                    await self._force_close_child(child)
                except BaseException as force_close_error:
                    raise ClaudeBackendStartupError(
                        "Claude startup preflight failed during close and force-close: "
                        f"{force_close_error}"
                    ) from error
                raise ClaudeBackendStartupError(
                    f"Claude startup preflight failed during close: {error}"
                ) from error
            return
        await self._cancel_and_settle(close_task)
        # Force-close is idempotent for the production SDK child and proves the
        # transient process is settled before startup reports deadline failure.
        await self._force_close_child(child)
        raise ClaudeBackendStartupError(
            "Claude startup preflight timed out during close after "
            f"{self._timeout_seconds:g} seconds"
        )

    async def _force_close_abandoned_child(self, result: Any) -> None:
        await self._force_close_child(cast(AcpEmployeeChild, result))

    @staticmethod
    async def _force_close_child(child: AcpEmployeeChild) -> None:
        force_close = getattr(child, "force_close", None)
        if force_close is None:
            await child.close()
            return
        await force_close()

    @staticmethod
    async def _cancel_and_settle(
        task: asyncio.Future[Any],
    ) -> tuple[bool, Any]:
        if not task.done():
            task.cancel()
        try:
            return True, await task
        except BaseException:
            # Awaiting consumes cancellation or failure so the task cannot be
            # orphaned or produce an unobserved exception.
            return False, None

    def _validate_response(self, response: InitializeResponse) -> None:
        capabilities = response.agent_capabilities
        prompt_capabilities = (
            None if capabilities is None else capabilities.prompt_capabilities
        )
        if response.protocol_version != 1:
            raise ClaudeBackendStartupError(
                "Claude startup preflight requires ACP protocol version 1"
            )
        if (
            response.agent_info is None
            or response.agent_info.name != CLAUDE_ACP_AGENT_NAME
            or response.agent_info.version != CLAUDE_ACP_AGENT_VERSION
        ):
            raise ClaudeBackendStartupError(
                "Claude startup preflight received the wrong agent identity"
            )
        if capabilities is None or capabilities.load_session is not True:
            raise ClaudeBackendStartupError(
                "Claude startup preflight requires session/load"
            )
        if prompt_capabilities is None or prompt_capabilities.image is not True:
            raise ClaudeBackendStartupError(
                "Claude startup preflight requires image prompt capability"
            )
        if (
            prompt_capabilities is None
            or prompt_capabilities.embedded_context is not True
        ):
            raise ClaudeBackendStartupError(
                "Claude startup preflight requires embedded-context prompt capability"
            )

    @staticmethod
    async def _reject_update(
        _payload: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> None:
        raise ClaudeBackendStartupError(
            "Claude startup preflight received an unexpected session update"
        )

    @staticmethod
    async def _reject_permission(
        _request: RequestPermissionRequest,
    ) -> RequestPermissionResponse:
        raise ClaudeBackendStartupError(
            "Claude startup preflight received an unexpected permission request"
        )

    @staticmethod
    async def _observe_death(error: BaseException | None) -> None:
        if error is not None:
            raise ClaudeBackendStartupError(
                f"Claude startup preflight child exited: {error}"
            )
