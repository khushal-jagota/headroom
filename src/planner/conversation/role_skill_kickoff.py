"""One-shot employee role delivery for newly created ACP sessions."""

from __future__ import annotations

from typing import Final, cast

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
    SessionNotification,
    TextContentBlock,
    UserMessageChunk,
)

from .backend_contracts import (
    AcpConversationIngress,
    AcpEmployeeChild,
    AcpEmployeeChildFactory,
    ChildDeathCallback,
    PermissionRequestCallback,
)
from .contracts import ConversationEmployee
from .wire_contracts import ProtocolUpdateRejectedPayload

_TICKET_ROLE_DIRECTIVE: Final = "Use the installed `panels-worker` skill."
_CHIEF_ROLE_DIRECTIVE: Final = "Use the installed `panels-chief-of-staff` skill."


def _role_directive(employee: ConversationEmployee) -> str:
    if employee.entity_kind == "ticket":
        return _TICKET_ROLE_DIRECTIVE
    return _CHIEF_ROLE_DIRECTIVE


class _RoleDirectiveEchoNormalizer:
    def __init__(self, session_id: str, role_directive: str) -> None:
        self._session_id = session_id
        self._role_directive = role_directive
        self._consumed = False

    def normalize(
        self,
        payload: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> SessionNotification | ProtocolUpdateRejectedPayload | None:
        if (
            self._consumed
            or not isinstance(payload, SessionNotification)
            or payload.session_id != self._session_id
            or not isinstance(payload.update, UserMessageChunk)
            or not isinstance(payload.update.content, TextContentBlock)
        ):
            return payload
        text = payload.update.content.text
        if text == self._role_directive:
            self._consumed = True
            return None
        flattened_prefix = f"{self._role_directive}\n"
        if not text.startswith(flattened_prefix):
            return payload
        self._consumed = True
        visible_content = payload.update.content.model_copy(
            update={"text": text[len(flattened_prefix) :]}
        )
        visible_update = payload.update.model_copy(
            update={"content": visible_content}
        )
        return payload.model_copy(update={"update": visible_update})


class _ScopedRoleDirectiveEchoIngress:
    def __init__(self, downstream: AcpConversationIngress) -> None:
        self._downstream = downstream
        self._normalizer: _RoleDirectiveEchoNormalizer | None = None

    def arm(
        self, session_id: str, role_directive: str
    ) -> _RoleDirectiveEchoNormalizer:
        if self._normalizer is not None:
            raise RuntimeError("role directive echo ingress is already armed")
        normalizer = _RoleDirectiveEchoNormalizer(session_id, role_directive)
        self._normalizer = normalizer
        return normalizer

    def disarm(self, normalizer: _RoleDirectiveEchoNormalizer) -> None:
        if self._normalizer is not normalizer:
            raise RuntimeError("role directive echo ingress arm changed unexpectedly")
        self._normalizer = None

    async def __call__(
        self,
        payload: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> None:
        normalizer = self._normalizer
        visible = payload if normalizer is None else normalizer.normalize(payload)
        if visible is not None:
            await self._downstream(visible)


class _RoleSkillKickoffAcpEmployeeChild(AcpEmployeeChild):
    def __init__(
        self,
        delegate: AcpEmployeeChild,
        role_directive: str,
        normal_ingress: _ScopedRoleDirectiveEchoIngress,
    ) -> None:
        self._delegate = delegate
        self._role_directive = role_directive
        self._normal_ingress = normal_ingress
        self._armed_session_id: str | None = None

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
        response = await self._delegate.new_session(request)
        self._armed_session_id = response.session_id
        return response

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        normalizer = self._normal_ingress.arm(
            request.session_id, self._role_directive
        )
        try:
            return await self._delegate.load_session(request)
        finally:
            self._normal_ingress.disarm(normalizer)

    async def capture_load_session(
        self,
        request: LoadSessionRequest,
        private_ingress: AcpConversationIngress,
    ) -> LoadSessionResponse:
        normalizer = _RoleDirectiveEchoNormalizer(
            request.session_id, self._role_directive
        )

        async def visible_private_ingress(
            payload: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            visible = normalizer.normalize(payload)
            if visible is not None:
                await private_ingress(visible)

        return await self._delegate.capture_load_session(
            request, cast(AcpConversationIngress, visible_private_ingress)
        )

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse:
        return await self._delegate.fork_session(request)

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        if (
            request.session_id != self._armed_session_id
            or self._is_command_shaped(request)
        ):
            return await self._delegate.prompt(request)
        self._armed_session_id = None
        delivered = request.model_copy(
            update={
                "prompt": [
                    TextContentBlock(type="text", text=self._role_directive),
                    *request.prompt,
                ]
            }
        )
        normalizer = self._normal_ingress.arm(
            request.session_id, self._role_directive
        )
        try:
            return await self._delegate.prompt(delivered)
        finally:
            self._normal_ingress.disarm(normalizer)

    @staticmethod
    def _is_command_shaped(request: PromptRequest) -> bool:
        return (
            bool(request.prompt)
            and all(isinstance(block, TextContentBlock) for block in request.prompt)
            and isinstance(request.prompt[0], TextContentBlock)
            and request.prompt[0].text.startswith("/")
        )

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


class RoleSkillKickoffAcpEmployeeChildFactory(AcpEmployeeChildFactory):
    """Decorate only the first prompt sent to each successfully created session."""

    def __init__(self, delegate: AcpEmployeeChildFactory) -> None:
        self._delegate = delegate

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: AcpConversationIngress,
        permission_callback: PermissionRequestCallback,
        death_callback: ChildDeathCallback,
    ) -> AcpEmployeeChild:
        role_directive = _role_directive(employee)
        normal_ingress = _ScopedRoleDirectiveEchoIngress(update_ingress)

        child = await self._delegate.create(
            employee,
            generation,
            cast(AcpConversationIngress, normal_ingress),
            permission_callback,
            death_callback,
        )
        return _RoleSkillKickoffAcpEmployeeChild(
            child, role_directive, normal_ingress
        )
