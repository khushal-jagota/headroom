"""Codex ACP turn capabilities and opaque in-place compaction lifecycle."""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from acp.schema import (
    PromptRequest,
    SessionNotification,
    ToolCallProgress,
    ToolCallStart,
)

from .backend_contracts import ConversationReplayItem
from .contracts import (
    ContextCompaction,
    ConversationCompactionBoundaryProvenance,
    ConversationSessionBinding,
    TurnDeliveryReceipt,
)
from .wire_contracts import ProtocolUpdateRejectedPayload

type _CodexCompactionPhase = Literal["idle", "compacting", "compacted"]
type _CodexCompactionKey = tuple[str, str, str, int]


@dataclass(slots=True)
class _CodexCompactionState:
    phase: _CodexCompactionPhase = "idle"
    tool_call_id: str | None = None
    boundary_identity: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class CodexAcpTurnStrategy:
    """Event-loop-confined state for the pinned adapter's tagged tool updates."""

    def __init__(self) -> None:
        self._compactions: dict[_CodexCompactionKey, _CodexCompactionState] = {}

    def classify_replay(
        self,
        session_binding: ConversationSessionBinding,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
    ) -> tuple[ConversationReplayItem, ...]:
        del session_binding
        return (
            *replay,
            *(
                ContextCompaction(
                    boundary_id=boundary.boundary_id,
                    state="compacted",
                    trigger=boundary.trigger,
                )
                for boundary in compacted_boundaries
            ),
        )

    async def steer(
        self,
        session_binding: ConversationSessionBinding,
        prompt: PromptRequest,
        client_message_id: str,
    ) -> TurnDeliveryReceipt:
        del session_binding, prompt
        return TurnDeliveryReceipt(
            client_message_id=client_message_id,
            choice="steer",
            state="rejected",
            reason="Codex ACP does not support native steer",
        )

    def prompt_failure_reason(
        self,
        session_binding: ConversationSessionBinding,
        prompt: PromptRequest,
        error: BaseException,
    ) -> str | None:
        """Expose one concrete failure only for this binding's compaction prompt."""

        key = self._compaction_key(session_binding)
        state = self._compactions.get(key)
        active_compaction = state is not None and state.phase == "compacting"
        if (
            prompt.session_id != session_binding.acp_session_id
            or (
                not self._is_exact_compaction_prompt(prompt)
                and not active_compaction
            )
        ):
            return None
        if state is not None:
            self._reset_after_capture(state)
        message = str(error)
        reason = f"{type(error).__name__}: {message}"
        if (
            not message
            or message != message.strip()
            or any(unicodedata.category(character) == "Cc" for character in reason)
        ):
            return None
        return reason

    def observe_compaction(
        self,
        session_binding: ConversationSessionBinding,
        notification: SessionNotification,
    ) -> ContextCompaction | None:
        if (
            session_binding.backend_key != "codex"
            or notification.session_id != session_binding.acp_session_id
        ):
            return None
        update = notification.update
        if update.field_meta != {"contextCompaction": True}:
            return None
        state = self._state_for(session_binding)
        if isinstance(update, ToolCallStart):
            if (
                update.kind != "other"
                or update.title != "Context compacting"
                or update.status != "in_progress"
            ):
                return None
            if state.phase == "idle":
                state.phase = "compacting"
                state.tool_call_id = update.tool_call_id
                state.boundary_identity = self._boundary_identity(
                    session_binding, update.tool_call_id
                )
                state.changed.set()
            if state.phase != "compacting" or state.tool_call_id != update.tool_call_id:
                return None
            assert state.boundary_identity is not None
            return ContextCompaction(
                boundary_id=state.boundary_identity,
                state="compacting",
                trigger="automatic",
            )
        if not isinstance(update, ToolCallProgress):
            return None
        if state.phase != "compacting" or state.tool_call_id != update.tool_call_id:
            return None
        if update.status == "completed" and update.title == "Context compacted":
            state.phase = "compacted"
        else:
            return None
        state.changed.set()
        return None

    async def capture_compaction(
        self, session_binding: ConversationSessionBinding
    ) -> ContextCompaction:
        return await self.capture_compaction_in_place(session_binding)

    async def capture_compaction_in_place(
        self, session_binding: ConversationSessionBinding
    ) -> ContextCompaction:
        """Wait only for the tagged terminal update on the current Codex thread."""

        state = self._state_for(session_binding)
        try:
            while not self._is_compacted(state):
                state.changed.clear()
                if self._is_compacted(state):
                    break
                await state.changed.wait()
        except asyncio.CancelledError:
            self._reset_after_capture(state)
            raise
        boundary_identity = state.boundary_identity
        if boundary_identity is None:
            self._reset_after_capture(state)
            raise RuntimeError("Codex compaction terminal update has no boundary identity")
        result = ContextCompaction(
            boundary_id=boundary_identity,
            state="compacted",
            trigger="automatic",
        )
        self._reset_after_capture(state)
        return result

    def _state_for(
        self, session_binding: ConversationSessionBinding
    ) -> _CodexCompactionState:
        key = self._compaction_key(session_binding)
        return self._compactions.setdefault(key, _CodexCompactionState())

    @staticmethod
    def _compaction_key(
        session_binding: ConversationSessionBinding,
    ) -> _CodexCompactionKey:
        return (
            session_binding.employee_id,
            session_binding.acp_session_id,
            session_binding.backend_key,
            session_binding.binding_generation,
        )

    @staticmethod
    def _is_exact_compaction_prompt(prompt: PromptRequest) -> bool:
        return (
            len(prompt.prompt) == 1
            and getattr(prompt.prompt[0], "type", None) == "text"
            and getattr(prompt.prompt[0], "text", None) == "/compact"
        )

    @staticmethod
    def _boundary_identity(
        session_binding: ConversationSessionBinding, tool_call_id: str
    ) -> str:
        return (
            f"{session_binding.employee_id}:{session_binding.acp_session_id}:"
            f"{session_binding.binding_generation}:{tool_call_id}"
        )

    @staticmethod
    def _reset_after_capture(state: _CodexCompactionState) -> None:
        state.phase = "idle"
        state.tool_call_id = None
        state.boundary_identity = None
        state.changed.clear()

    @staticmethod
    def _is_compacted(state: _CodexCompactionState) -> bool:
        return state.phase == "compacted"
