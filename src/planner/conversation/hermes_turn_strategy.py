"""Pinned Hermes-only native steer and compaction normalization."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from acp.schema import (
    AgentMessageChunk,
    PromptRequest,
    PromptResponse,
    SessionInfoUpdate,
    SessionNotification,
    TextContentBlock,
    UserMessageChunk,
)

from .contracts import (
    ContextCompaction,
    ConversationCompactionBoundaryProvenance,
    ConversationSessionBinding,
    TurnDeliveryReceipt,
)
from .runtime_ports import ConversationRuntimeLease
from .wire_contracts import ProtocolUpdateRejectedPayload

HERMES_SUMMARY_PREFIX: Final = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. "
    "Respond ONLY to the latest user message that appears AFTER this "
    "summary — that message is the single source of truth for what to do "
    "right now. "
    "Topic overlap with the summary does NOT mean you should resume its "
    "task: even on similar topics, the latest user message WINS. Treat ONLY "
    "the latest message as the active task and discard stale items from "
    "'## Historical Task Snapshot' / '## Historical In-Progress State' / "
    "'## Historical Pending User Asks' / "
    "'## Historical Remaining Work' entirely — do not 'wrap up' or "
    "'finish' work described there unless the latest message explicitly "
    "asks for it. "
    "Reverse signals in the latest message (e.g. 'stop', 'undo', 'roll "
    "back', 'just verify', 'don't do that anymore', 'never mind', a new "
    "topic) must immediately end any in-flight work described in the "
    "summary; do not re-surface it in later turns. "
    "IMPORTANT: Your persistent memory (MEMORY.md, USER.md) in the system "
    "prompt is ALWAYS authoritative and active — never ignore or deprioritize "
    "memory content due to this compaction note. "
    "The current session state (files, config, etc.) may reflect work "
    "described here — avoid repeating it:"
)
HERMES_SUMMARY_END_MARKER: Final = (
    "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
)
HERMES_MERGED_PRIOR_CONTEXT_HEADER: Final = (
    "[PRIOR CONTEXT — for reference only; not a new message]"
)
HERMES_MERGED_SUMMARY_DELIMITER: Final = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"

ConcurrentPrompt = Callable[
    [ConversationSessionBinding, str, PromptRequest], Awaitable[PromptResponse]
]
CaptureUpdates = Callable[
    [ConversationSessionBinding],
    Awaitable[Sequence[SessionNotification | ProtocolUpdateRejectedPayload]],
]


@dataclass(frozen=True, slots=True)
class _HermesPrivateContextMarker:
    replay_index: int


type _HermesPrivateContextTextDisposition = Literal[
    "ordinary", "candidate", "malformed"
]


class HermesAcpTurnStrategy:
    def __init__(
        self,
        *,
        concurrent_prompt: ConcurrentPrompt | None,
        capture_updates: CaptureUpdates | None,
    ) -> None:
        self._concurrent_prompt = concurrent_prompt
        self._capture_updates = capture_updates

    def classify_replay(
        self,
        session_binding: ConversationSessionBinding,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
    ) -> tuple[SessionNotification | ProtocolUpdateRejectedPayload | ContextCompaction, ...]:
        if compacted_boundaries and any(
            isinstance(item, ProtocolUpdateRejectedPayload) for item in replay
        ):
            raise ValueError("Hermes compacted replay contains a rejected update")
        try:
            marker = self._find_private_context_marker(session_binding, replay)
        except ValueError as error:
            raise ValueError("Hermes compacted replay is structurally invalid") from error
        if marker is None:
            if compacted_boundaries:
                return (
                    *replay,
                    *self._completion_boundaries(compacted_boundaries),
                )
            return replay
        if not compacted_boundaries:
            raise ValueError("Hermes compacted replay has no durable provenance")
        replacements = self._completion_boundaries(compacted_boundaries)
        return (
            *replay[: marker.replay_index],
            *replacements,
            *replay[marker.replay_index + 1 :],
        )

    async def steer(
        self,
        session_binding: ConversationSessionBinding,
        prompt: PromptRequest,
        client_message_id: str,
    ) -> TurnDeliveryReceipt:
        if self._concurrent_prompt is None:
            return self._rejected_steer(client_message_id, "Native steer is unavailable")
        concurrent_request = self._steer_request(
            session_binding, prompt, client_message_id
        )
        if isinstance(concurrent_request, TurnDeliveryReceipt):
            return concurrent_request
        try:
            await self._concurrent_prompt(session_binding, client_message_id, concurrent_request)
        except BaseException:
            return self._rejected_steer(client_message_id, "Native steer failed")
        return TurnDeliveryReceipt(
            client_message_id=client_message_id,
            choice="steer",
            state="accepted",
        )

    async def steer_with_runtime_lease(
        self,
        lease: ConversationRuntimeLease,
        session_binding: ConversationSessionBinding,
        prompt: PromptRequest,
        client_message_id: str,
    ) -> TurnDeliveryReceipt:
        concurrent_request = self._steer_request(
            session_binding, prompt, client_message_id
        )
        if isinstance(concurrent_request, TurnDeliveryReceipt):
            return concurrent_request
        try:
            await lease.prompt(concurrent_request)
        except BaseException:
            return self._rejected_steer(client_message_id, "Native steer failed")
        return TurnDeliveryReceipt(
            client_message_id=client_message_id,
            choice="steer",
            state="accepted",
        )

    def observe_compaction(
        self,
        session_binding: ConversationSessionBinding,
        notification: SessionNotification,
    ) -> ContextCompaction | None:
        if notification.session_id != session_binding.acp_session_id:
            return None
        update = notification.update
        if not isinstance(update, SessionInfoUpdate):
            return None
        metadata = update.field_meta
        if not isinstance(metadata, dict):
            return None
        hermes = metadata.get("hermes")
        if not isinstance(hermes, dict):
            return None
        provenance = hermes.get("sessionProvenance")
        if not isinstance(provenance, dict):
            return None
        if provenance.get("reason") != "compression":
            return None
        if provenance.get("acpSessionId") != session_binding.acp_session_id:
            return None
        previous = provenance.get("previousHermesSessionId")
        current = provenance.get("currentHermesSessionId")
        depth = provenance.get("compressionDepth")
        if (
            not isinstance(previous, str)
            or not previous
            or not isinstance(current, str)
            or not current
            or previous == current
            or not isinstance(depth, int)
            or isinstance(depth, bool)
        ):
            return None
        return ContextCompaction(
            boundary_id=f"{previous}:{current}:{depth}",
            state="compacting",
            trigger="automatic",
        )

    async def capture_compaction(
        self, session_binding: ConversationSessionBinding
    ) -> ContextCompaction:
        if self._capture_updates is None:
            return self._failed_capture("Compaction capture is unavailable")
        try:
            replay = await self._capture_updates(session_binding)
        except BaseException:
            return self._failed_capture("Compaction capture failed")
        return self.capture_compaction_from_updates(session_binding, replay)

    def capture_compaction_from_updates(
        self,
        session_binding: ConversationSessionBinding,
        replay: Sequence[SessionNotification | ProtocolUpdateRejectedPayload],
    ) -> ContextCompaction:
        if any(isinstance(item, ProtocolUpdateRejectedPayload) for item in replay):
            return self._failed_capture("Compaction capture did not match the protocol")
        try:
            self._find_private_context_marker(session_binding, replay)
        except ValueError:
            return self._failed_capture("Compacted private context marker was invalid")
        return ContextCompaction(
            boundary_id="hermes-capture",
            state="compacted",
            trigger="automatic",
        )

    def _find_private_context_marker(
        self,
        session_binding: ConversationSessionBinding,
        replay: Sequence[SessionNotification | ProtocolUpdateRejectedPayload],
    ) -> _HermesPrivateContextMarker | None:
        candidate: _HermesPrivateContextMarker | None = None
        for replay_index, notification in enumerate(replay):
            if isinstance(notification, ProtocolUpdateRejectedPayload):
                continue
            update = notification.update
            if not isinstance(update, (UserMessageChunk, AgentMessageChunk)):
                continue
            content = update.content
            if not isinstance(content, TextContentBlock):
                continue
            disposition = self._classify_private_context_text(content.text)
            if disposition == "ordinary":
                continue
            if disposition == "malformed":
                raise ValueError("Hermes private context marker is malformed")
            if notification.session_id != session_binding.acp_session_id:
                raise ValueError("Hermes private context marker names another session")
            if candidate is not None:
                raise ValueError("Hermes private context marker is not unique")
            candidate = _HermesPrivateContextMarker(
                replay_index=replay_index,
            )
        return candidate

    @staticmethod
    def _completion_boundaries(
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
    ) -> tuple[ContextCompaction, ...]:
        return tuple(
            ContextCompaction(
                boundary_id=boundary.boundary_id,
                state="compacted",
                trigger=boundary.trigger,
            )
            for boundary in compacted_boundaries
        )

    def _steer_request(
        self,
        session_binding: ConversationSessionBinding,
        prompt: PromptRequest,
        client_message_id: str,
    ) -> PromptRequest | TurnDeliveryReceipt:
        if prompt.session_id != session_binding.acp_session_id:
            return self._rejected_steer(
                client_message_id, "Steer session does not match"
            )
        if len(prompt.prompt) != 1 or not isinstance(
            prompt.prompt[0], TextContentBlock
        ):
            return self._rejected_steer(
                client_message_id, "Steer accepts one text block"
            )
        text = prompt.prompt[0].text.strip()
        if not text:
            return self._rejected_steer(client_message_id, "Steer text is empty")
        return PromptRequest(
            session_id=session_binding.acp_session_id,
            prompt=[TextContentBlock(type="text", text="/steer " + text)],
        )

    @staticmethod
    def _classify_private_context_text(
        text: str,
    ) -> _HermesPrivateContextTextDisposition:
        stripped = text.strip()
        if stripped.startswith(HERMES_SUMMARY_PREFIX):
            if (
                stripped.count(HERMES_SUMMARY_END_MARKER) != 1
                or not stripped.endswith(HERMES_SUMMARY_END_MARKER)
            ):
                return "malformed"
            marker_body = stripped[: -len(HERMES_SUMMARY_END_MARKER)].rstrip()
            if marker_body == HERMES_SUMMARY_PREFIX:
                return "malformed"
            return "candidate"

        if stripped.startswith(HERMES_MERGED_PRIOR_CONTEXT_HEADER):
            if stripped.count(HERMES_MERGED_SUMMARY_DELIMITER) != 1:
                return "malformed"
            _prior, private_marker_tail = stripped.split(
                HERMES_MERGED_SUMMARY_DELIMITER, 1
            )
            private_marker = private_marker_tail.strip()
            if (
                not private_marker.startswith(HERMES_SUMMARY_PREFIX)
                or private_marker.count(HERMES_SUMMARY_END_MARKER) != 1
                or not private_marker.endswith(HERMES_SUMMARY_END_MARKER)
            ):
                return "malformed"
            marker_body = private_marker[: -len(HERMES_SUMMARY_END_MARKER)].rstrip()
            if marker_body == HERMES_SUMMARY_PREFIX:
                return "malformed"
            return "candidate"

        if any(
            marker in stripped
            for marker in (
                HERMES_SUMMARY_PREFIX,
                HERMES_SUMMARY_END_MARKER,
                HERMES_MERGED_SUMMARY_DELIMITER,
            )
        ):
            return "malformed"
        return "ordinary"

    @staticmethod
    def _rejected_steer(client_message_id: str, reason: str) -> TurnDeliveryReceipt:
        return TurnDeliveryReceipt(
            client_message_id=client_message_id,
            choice="steer",
            state="rejected",
            reason=reason,
        )

    @staticmethod
    def _failed_capture(reason: str) -> ContextCompaction:
        return ContextCompaction(
            boundary_id="hermes-capture",
            state="failed",
            trigger="automatic",
            reason=reason,
        )
