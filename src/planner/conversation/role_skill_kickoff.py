"""One-shot employee role delivery for newly created ACP sessions."""

from __future__ import annotations

from typing import Final

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
    SetSessionConfigOptionResponse,
    TextContentBlock,
)

from .backend_contracts import (
    AcpConversationIngress,
    AcpEmployeeChild,
    AcpEmployeeChildFactory,
    ChildDeathCallback,
    LegacyAcpSessionModelSelection,
    PermissionRequestCallback,
)
from .contracts import ConversationEmployee

_TICKET_ROLE_DIRECTIVE: Final = "Use the installed `panels-worker` skill."
_CHIEF_ROLE_DIRECTIVE: Final = "Use the installed `panels-chief-of-staff` skill."


def _role_directive(employee: ConversationEmployee) -> str:
    if employee.entity_kind == "ticket":
        return _TICKET_ROLE_DIRECTIVE
    return _CHIEF_ROLE_DIRECTIVE


class _RoleSkillKickoffAcpEmployeeChild(AcpEmployeeChild):
    def __init__(
        self,
        delegate: AcpEmployeeChild,
        role_directive: str,
    ) -> None:
        self._delegate = delegate
        self._role_directive = role_directive
        self._armed_session_id: str | None = None
        self._prepared_prompt_ids: set[int] = set()

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

    async def set_config_option(
        self, session_id: str, config_id: str, value: str
    ) -> SetSessionConfigOptionResponse:
        return await self._delegate.set_config_option(session_id, config_id, value)

    async def set_legacy_session_model(self, session_id: str, model_id: str) -> None:
        if not isinstance(self._delegate, LegacyAcpSessionModelSelection):
            raise TypeError("ACP child does not support legacy session model selection")
        await self._delegate.set_legacy_session_model(session_id, model_id)

    async def close_session(self, session_id: str) -> None:
        await self._delegate.close_session(session_id)

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        return await self._delegate.load_session(request)

    async def capture_load_session(
        self,
        request: LoadSessionRequest,
        private_ingress: AcpConversationIngress,
    ) -> LoadSessionResponse:
        return await self._delegate.capture_load_session(request, private_ingress)

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse:
        return await self._delegate.fork_session(request)

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        return await self._delegate.prompt(self._prompt_for_display(request, consume=True))

    def prompt_for_display(self, request: PromptRequest) -> PromptRequest:
        return self._prompt_for_display(request, consume=False)

    def _prompt_for_display(self, request: PromptRequest, *, consume: bool) -> PromptRequest:
        prepared = id(request) in self._prepared_prompt_ids
        if prepared:
            self._prepared_prompt_ids.discard(id(request))
            if consume:
                self._armed_session_id = None
            return request
        if request.session_id != self._armed_session_id or self._is_command_shaped(request):
            return request
        if consume:
            self._armed_session_id = None
        prepared_request = request.model_copy(
            update={
                "prompt": [
                    TextContentBlock(type="text", text=self._role_directive),
                    *request.prompt,
                ]
            }
        )
        self._prepared_prompt_ids.add(id(prepared_request))
        return prepared_request

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
        child = await self._delegate.create(
            employee,
            generation,
            update_ingress,
            permission_callback,
            death_callback,
        )
        return _RoleSkillKickoffAcpEmployeeChild(child, role_directive)
