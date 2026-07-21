"""Framework-free interfaces between the conversation domain and ACP backends."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from acp.schema import (
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
    SessionNotification,
)

from .contracts import (
    ContextCompaction,
    ConversationCompactionBoundaryProvenance,
    ConversationEmployee,
    ConversationSessionBinding,
    TurnDeliveryReceipt,
    _require_non_empty_text,
)
from .wire_contracts import ProtocolUpdateRejectedPayload

type ConversationReplayItem = (
    SessionNotification | ProtocolUpdateRejectedPayload | ContextCompaction
)


@dataclass(frozen=True, slots=True)
class BackendTurnCapabilities:
    supports_steer: bool
    observes_compaction: bool
    requires_fresh_child_after_requested_cancel: bool = False


@dataclass(frozen=True, slots=True)
class ReverseServiceCapabilities:
    filesystem: bool
    terminal: bool
    permission: bool


class BackendTurnStrategy(Protocol):
    def classify_replay(
        self,
        session_binding: ConversationSessionBinding,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
    ) -> tuple[ConversationReplayItem, ...]: ...

    async def steer(
        self,
        session_binding: ConversationSessionBinding,
        prompt: PromptRequest,
        client_message_id: str,
    ) -> TurnDeliveryReceipt: ...

    def observe_compaction(
        self,
        session_binding: ConversationSessionBinding,
        notification: SessionNotification,
    ) -> ContextCompaction | None: ...

    async def capture_compaction(
        self,
        session_binding: ConversationSessionBinding,
    ) -> ContextCompaction: ...


WorkingDirectoryResolver = Callable[[ConversationEmployee], Path]


@dataclass(frozen=True, slots=True)
class AgentBackendDefinition:
    backend_key: str
    argv: tuple[str, ...]
    inherited_environment_names: tuple[str, ...]
    environment_overrides: tuple[tuple[str, str], ...]
    expected_agent_name: str
    expected_agent_version: str
    turn_capabilities: BackendTurnCapabilities
    reverse_service_capabilities: ReverseServiceCapabilities
    working_directory_resolver: WorkingDirectoryResolver
    turn_strategy: BackendTurnStrategy

    def __post_init__(self) -> None:
        _require_non_empty_text(self.backend_key, field_name="backend_key")
        _require_non_empty_text(self.expected_agent_name, field_name="expected_agent_name")
        _require_non_empty_text(self.expected_agent_version, field_name="expected_agent_version")
        if not self.argv:
            raise ValueError("argv must not be empty")
        _require_non_empty_text(self.argv[0], field_name="argv executable")
        if len(set(self.inherited_environment_names)) != len(self.inherited_environment_names):
            raise ValueError("inherited_environment_names must not contain duplicates")
        for name in self.inherited_environment_names:
            _validate_environment_name(name)
        override_names = [name for name, _value in self.environment_overrides]
        if len(set(override_names)) != len(override_names):
            raise ValueError("environment_overrides must not contain duplicate names")
        for name, _value in self.environment_overrides:
            _validate_environment_name(name)

    def working_directory_for(self, employee: ConversationEmployee) -> Path:
        working_directory = self.working_directory_resolver(employee)
        if not working_directory.is_absolute():
            raise ValueError("working-directory resolver must return an absolute path")
        return working_directory


def _validate_environment_name(name: str) -> None:
    _require_non_empty_text(name, field_name="environment name")
    if "=" in name or "\x00" in name:
        raise ValueError("environment name is invalid")


class AcpConversationIngress(Protocol):
    async def __call__(
        self, notification: SessionNotification | ProtocolUpdateRejectedPayload
    ) -> None: ...


# Kept as a source-compatible public name for the ACP-00 contract. The corrected
# ACP-01 ingress also carries the already-frozen visible rejection payload.
AcpSessionUpdateIngress = AcpConversationIngress


PermissionRequestCallback = Callable[
    [RequestPermissionRequest], Awaitable[RequestPermissionResponse]
]
ChildDeathCallback = Callable[[BaseException | None], Awaitable[None]]


class AcpEmployeeChild(Protocol):
    @property
    def generation(self) -> int: ...

    @property
    def alive(self) -> bool: ...

    @property
    def supports_session_fork(self) -> bool: ...

    async def initialize(self, request: InitializeRequest) -> InitializeResponse: ...

    async def new_session(self, request: NewSessionRequest) -> NewSessionResponse: ...

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse: ...

    async def capture_load_session(
        self,
        request: LoadSessionRequest,
        private_ingress: AcpConversationIngress,
    ) -> LoadSessionResponse: ...

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse: ...

    async def prompt(self, request: PromptRequest) -> PromptResponse: ...

    async def cancel(self, notification: CancelNotification) -> None: ...

    async def close(self) -> None: ...


class AcpEmployeeChildFactory(Protocol):
    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: AcpConversationIngress,
        permission_callback: PermissionRequestCallback,
        death_callback: ChildDeathCallback,
    ) -> AcpEmployeeChild: ...
