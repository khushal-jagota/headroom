"""Claude ACP turn semantics and opaque compaction lifecycle observation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Final, Literal

from acp.schema import (
    PromptRequest,
    SessionInfoUpdate,
    SessionNotification,
    TextContentBlock,
    UserMessageChunk,
)

from .backend_contracts import ConversationReplayItem
from .contracts import (
    ContextCompaction,
    ConversationCompactionBoundaryProvenance,
    ConversationSessionBinding,
    TurnDeliveryReceipt,
)
from .wire_contracts import ProtocolUpdateRejectedPayload

CLAUDE_COMPACTION_STARTED: Final = "Compacting..."
CLAUDE_COMPACTION_COMPLETED: Final = "\n\nCompacting completed."
CLAUDE_COMPACTION_FAILED_WITHOUT_REASON: Final = "\n\nCompacting failed."
CLAUDE_COMPACTION_FAILED_PREFIX: Final = "\n\nCompacting failed: "
CLAUDE_COMPACTION_METADATA_NAMESPACE: Final = "claude"
CLAUDE_COMPACTION_METADATA_KEY: Final = "compactionControl"
CLAUDE_PRIVATE_COMPACTION_SUMMARY_PREFIX: Final = (
    "This session is being continued from a previous conversation that ran out of "
    "context. The summary below covers the earlier portion of the conversation."
)
CLAUDE_PRIVATE_COMPACTION_SUMMARY_DETAIL_MARKER: Final = (
    "If you need specific details from before compaction (like exact code snippets, "
    "error messages, or content you generated), read the full transcript at:"
)
CLAUDE_PRIVATE_COMPACTION_SUMMARY_SUFFIX: Final = (
    "Pick up the last task as if the break never happened."
)

type _ClaudeCompactionPhase = Literal[
    "idle", "compacting", "completed", "failed"
]
type _ClaudeCompactionKey = tuple[str, str, str, int]


@dataclass(slots=True)
class _ClaudeCompactionState:
    next_ordinal: int = 0
    phase: _ClaudeCompactionPhase = "idle"
    boundary_identity: str | None = None
    exact_failure: str | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)


def is_claude_compaction_control(text: str) -> bool:
    """Return whether text is one exact control shape emitted by Claude ACP."""

    return (
        text in {
            CLAUDE_COMPACTION_STARTED,
            CLAUDE_COMPACTION_COMPLETED,
            CLAUDE_COMPACTION_FAILED_WITHOUT_REASON,
        }
        or (
            text.startswith(CLAUDE_COMPACTION_FAILED_PREFIX)
            and len(text) > len(CLAUDE_COMPACTION_FAILED_PREFIX)
        )
    )


def claude_compaction_control_text(
    notification: SessionNotification,
) -> str | None:
    """Read the exact provider-local metadata installed by the Claude decorator."""

    update = notification.update
    if not isinstance(update, SessionInfoUpdate):
        return None
    metadata = update.field_meta
    if not isinstance(metadata, dict) or set(metadata) != {
        CLAUDE_COMPACTION_METADATA_NAMESPACE
    }:
        return None
    claude = metadata.get(CLAUDE_COMPACTION_METADATA_NAMESPACE)
    if not isinstance(claude, dict) or set(claude) != {
        CLAUDE_COMPACTION_METADATA_KEY
    }:
        return None
    text = claude.get(CLAUDE_COMPACTION_METADATA_KEY)
    if not isinstance(text, str) or not is_claude_compaction_control(text):
        return None
    return text


class ClaudeAcpTurnStrategy:
    """Event-loop-confined Claude state keyed by one durable binding generation."""

    def __init__(self) -> None:
        self._compactions: dict[_ClaudeCompactionKey, _ClaudeCompactionState] = {}

    def classify_replay(
        self,
        session_binding: ConversationSessionBinding,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
    ) -> tuple[ConversationReplayItem, ...]:
        del compacted_boundaries
        classified: list[ConversationReplayItem] = []
        private_summary_ordinal = 0
        for item in replay:
            message_id = _claude_private_compaction_summary_message_id(item)
            if message_id is None:
                classified.append(item)
                continue
            private_summary_ordinal += 1
            identity = message_id or f"ordinal-{private_summary_ordinal}"
            classified.append(
                ContextCompaction(
                    boundary_id=(
                        f"{session_binding.employee_id}:"
                        f"{session_binding.acp_session_id}:"
                        f"{session_binding.binding_generation}:"
                        f"claude-replay:{identity}"
                    ),
                    state="compacted",
                    trigger="automatic",
                )
            )
        return tuple(classified)

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
            reason="Claude ACP does not support native steer",
        )

    def observe_compaction(
        self,
        session_binding: ConversationSessionBinding,
        notification: SessionNotification,
    ) -> ContextCompaction | None:
        if (
            session_binding.backend_key != "claude"
            or notification.session_id != session_binding.acp_session_id
        ):
            return None
        control = claude_compaction_control_text(notification)
        if control is None:
            return None
        state = self._state_for(session_binding)
        if control == CLAUDE_COMPACTION_STARTED:
            if state.phase == "idle":
                state.next_ordinal += 1
                state.phase = "compacting"
                state.boundary_identity = self._boundary_identity(
                    session_binding, state.next_ordinal
                )
                state.exact_failure = None
                state.changed.set()
            assert state.boundary_identity is not None
            return ContextCompaction(
                boundary_id=state.boundary_identity,
                state="compacting",
                trigger="automatic",
            )

        if state.phase != "compacting":
            return None
        if control == CLAUDE_COMPACTION_COMPLETED:
            state.phase = "completed"
        else:
            state.phase = "failed"
            state.exact_failure = control.removeprefix("\n\n")
        state.changed.set()
        return None

    async def capture_compaction(
        self, session_binding: ConversationSessionBinding
    ) -> ContextCompaction:
        return await self.capture_compaction_in_place(session_binding)

    async def capture_compaction_in_place(
        self,
        session_binding: ConversationSessionBinding,
    ) -> ContextCompaction:
        """Wait for Claude's terminal control; the generic lease owns the deadline."""

        state = self._state_for(session_binding)
        try:
            while state.phase not in {"completed", "failed"}:
                state.changed.clear()
                if state.phase in {"completed", "failed"}:
                    break
                await state.changed.wait()
        except asyncio.CancelledError:
            self._reset_after_capture(state)
            raise

        boundary_identity = state.boundary_identity
        if boundary_identity is None:
            raise RuntimeError("Claude compaction terminal control has no boundary identity")
        if state.phase == "completed":
            result = ContextCompaction(
                boundary_id=boundary_identity,
                state="compacted",
                trigger="automatic",
            )
        else:
            result = ContextCompaction(
                boundary_id=boundary_identity,
                state="failed",
                trigger="automatic",
                reason=state.exact_failure or "Compacting failed.",
            )
        self._reset_after_capture(state)
        return result

    def _state_for(
        self, session_binding: ConversationSessionBinding
    ) -> _ClaudeCompactionState:
        key = (
            session_binding.employee_id,
            session_binding.acp_session_id,
            session_binding.backend_key,
            session_binding.binding_generation,
        )
        return self._compactions.setdefault(key, _ClaudeCompactionState())

    @staticmethod
    def _boundary_identity(
        session_binding: ConversationSessionBinding, ordinal: int
    ) -> str:
        return (
            f"{session_binding.employee_id}:{session_binding.acp_session_id}:"
            f"{session_binding.binding_generation}:{ordinal}"
        )

    @staticmethod
    def _reset_after_capture(state: _ClaudeCompactionState) -> None:
        state.phase = "idle"
        state.boundary_identity = None
        state.exact_failure = None
        state.changed.clear()


def _claude_private_compaction_summary_message_id(
    item: SessionNotification | ProtocolUpdateRejectedPayload,
) -> str | None:
    if not isinstance(item, SessionNotification):
        return None
    update = item.update
    if not isinstance(update, UserMessageChunk) or not isinstance(
        update.content, TextContentBlock
    ):
        return None
    text = update.content.text
    if not (
        text.startswith(CLAUDE_PRIVATE_COMPACTION_SUMMARY_PREFIX)
        and f"\n\n{CLAUDE_PRIVATE_COMPACTION_SUMMARY_DETAIL_MARKER}" in text
        and text.endswith(CLAUDE_PRIVATE_COMPACTION_SUMMARY_SUFFIX)
    ):
        return None
    return update.message_id or ""
