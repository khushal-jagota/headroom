"""VPS backend adapter: native children remain owned by the Mac process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from planner.conversation.backends import contracts as backend
from planner.conversation.contracts import (
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
)
from planner.conversation.message_content import MessageContent
from planner.conversation.message_files import ConversationMessageFiles
from planner.executor.codec import decode_prompt_result, encode_value
from planner.executor.contracts import (
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    JsonObject,
)

type SendCommand = Callable[[ChildCommand], Awaitable[CommandOutcome]]
type AssignChild = Callable[[ResolvedConversationStart, backend.BackendEventSink], ChildReference]


@dataclass(slots=True)
class RemoteBackendChildFactory:
    assign_child: AssignChild
    send_command: SendCommand

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: backend.BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> RemoteBackendChild:
        del message_files
        return RemoteBackendChild(self.assign_child(resolved_start, event_sink), self.send_command)


class RemoteBackendChild:
    """A disconnect leaves command futures pending; it never fabricates a refusal."""

    def __init__(self, reference: ChildReference, send_command: SendCommand) -> None:
        self.reference = reference
        self._send_command = send_command

    async def _call(
        self, operation: ChildOperation, turn: backend.TurnToken | None = None, **arguments: Any
    ) -> JsonObject:
        result = await self._send_command(
            ChildCommand(
                command_id=str(uuid4()),
                child=self.reference,
                operation=operation,
                turn=turn,
                arguments={key: encode_value(value) for key, value in arguments.items()},
            )
        )
        if result.disposition is CommandDisposition.uncertain:
            if operation is ChildOperation.steer:
                return {
                    "kind": "steer_uncertain",
                    "composed_content_delivered": result.result.get("composed_content_delivered"),
                }
            # An uncertainty is not proof of non-delivery. Never rebind/retry this command.
            raise RemoteCommandUncertain(result.command_id)
        if result.disposition is CommandDisposition.refused:
            error_name = str(result.result.get("error_type", "BackendSpawnFailed"))
            errors: dict[str, type[backend.BackendAdapterError]] = {
                name: getattr(backend, name)
                for name in (
                    "BackendSpawnFailed",
                    "SessionLoadFailed",
                    "PromptWriteFailed",
                    "PermissionAnswerWriteFailed",
                    "UserInputAnswerWriteFailed",
                    "NeedsRebind",
                )
            }
            raise errors.get(error_name, backend.BackendSpawnFailed)(
                str(result.result.get("error_message", "remote operation refused"))
            )
        return result.result

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        await self._call(
            ChildOperation.start,
            resolved_start=resolved_start,
            vendor_session_cursor=vendor_session_cursor,
        )

    async def write_prompt(
        self,
        turn_token: backend.TurnToken,
        content: MessageContent,
        *,
        sender_label: str,
        sender_content: MessageContent,
        sender_message_count: int = 1,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool = False,
    ) -> backend.BackendPromptAccepted:
        return decode_prompt_result(
            await self._call(
                ChildOperation.write_prompt,
                turn_token,
                content=content,
                sender_label=sender_label,
                sender_content=sender_content,
                sender_message_count=sender_message_count,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                automatic_compaction=automatic_compaction,
            )
        )

    async def steer(
        self,
        turn_token: backend.TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent | None = None,
        sender_label: str,
    ) -> backend.BackendSteerOutcome:
        result = await self._call(
            ChildOperation.steer,
            turn_token,
            content=content,
            sender_content=sender_content,
            sender_label=sender_label,
        )
        if result["kind"] == "steer_refused":
            return backend.BackendSteerRefused(
                PromptDeliveryRefusalReason(str(result["refusal_reason"]))
            )
        if result["kind"] == "steer_uncertain":
            delivered = result.get("composed_content_delivered")
            return backend.BackendSteerUncertain(delivered if isinstance(delivered, bool) else None)
        return backend.BackendSteerAccepted(bool(result.get("composed_content_delivered", True)))

    async def cancel_running_turn(self) -> None:
        await self._call(ChildOperation.cancel_running_turn)

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        await self._call(ChildOperation.answer_permission_ask, ask_id=ask_id, option_id=option_id)

    async def answer_user_input(self, request_id: str, answers: tuple[Any, ...]) -> None:
        await self._call(ChildOperation.answer_user_input, request_id=request_id, answers=answers)

    async def stop(self) -> None:
        await self._call(ChildOperation.stop)


class RemoteCommandUncertain(RuntimeError):
    """The original native operation has no proven outcome. Do not redispatch it."""
