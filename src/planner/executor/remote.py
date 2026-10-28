"""VPS backend adapter: native children remain owned by the Mac process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from planner.conversation.backends import contracts as backend
from planner.conversation.contracts import (
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.message_content import MessageContent
from planner.conversation.message_files import ConversationMessageFiles
from planner.executor.codec import decode_prompt_result, encode_value
from planner.executor.contracts import (
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    JsonObject,
    RemoteInputTransferFailed,
    WireMessage,
    WireMessageKind,
)
from planner.executor.skills import validate_skill_snapshot
from planner.executor.wire import ProtocolRefused, encode_frame

type SkillSnapshotSupplier = Callable[[ResolvedConversationStart], CanonicalSkillSnapshot]
type SendCommand = Callable[[ChildCommand], Awaitable[CommandOutcome]]
type AssignChild = Callable[[ResolvedConversationStart, backend.BackendEventSink], ChildReference]
type CancelInitialDelivery = Callable[[ChildReference, tuple[str, ...]], bool]


@dataclass(slots=True)
class RemoteBackendChildFactory:
    assign_child: AssignChild
    send_command: SendCommand
    skill_snapshot_supplier: SkillSnapshotSupplier
    cancel_initial_delivery: CancelInitialDelivery | None = None
    retire_reference: Callable[[ChildReference], None] | None = None

    def recover(self, reference: ChildReference) -> RemoteBackendChild:
        return RemoteBackendChild(
            reference,
            self.send_command,
            self.skill_snapshot_supplier,
            self.cancel_initial_delivery,
            initial_history_complete=False,
            retire_reference=self.retire_reference,
        )

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: backend.BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> RemoteBackendChild:
        del message_files
        return RemoteBackendChild(
            self.assign_child(resolved_start, event_sink),
            self.send_command,
            self.skill_snapshot_supplier,
            self.cancel_initial_delivery,
            retire_reference=self.retire_reference,
        )


class RemoteBackendChild:
    """A disconnect leaves command futures pending; it never fabricates a refusal."""

    def __init__(
        self,
        reference: ChildReference,
        send_command: SendCommand,
        skill_snapshot_supplier: SkillSnapshotSupplier,
        cancel_initial_delivery: CancelInitialDelivery | None = None,
        *,
        initial_history_complete: bool = True,
        retire_reference: Callable[[ChildReference], None] | None = None,
    ) -> None:
        self.reference = reference
        self._retire_reference = retire_reference
        self._send_command = send_command
        self._skill_snapshot_supplier = skill_snapshot_supplier
        self._cancel_initial_delivery = cancel_initial_delivery
        self.initial_history_complete = initial_history_complete
        self.initial_commands: list[ChildCommand] = []
        self.record_command: Callable[[ChildCommand], Awaitable[None]] | None = None
        self.initial_delivery_cancelled = False
        self.prompt_accepted = False

    def retire_local_reference(self) -> None:
        if self._retire_reference is not None:
            self._retire_reference(self.reference)

    def cancel_initial_delivery(self) -> bool:
        """Fence initial work; true proves the prompt never crossed the wire.

        A successful child start does not prove prompt acceptance. Restored children
        lack complete local transmission history.
        """
        self.initial_delivery_cancelled = True
        may_have_sent = (
            self._cancel_initial_delivery(
                self.reference, tuple(command.command_id for command in self.initial_commands)
            )
            if self._cancel_initial_delivery is not None
            else any(
                command.operation is ChildOperation.write_prompt
                for command in self.initial_commands
            )
        )
        return self.initial_history_complete and not may_have_sent and not self.prompt_accepted

    async def _call(
        self, operation: ChildOperation, turn: backend.TurnToken | None = None, **arguments: Any
    ) -> JsonObject:
        command = ChildCommand(
            command_id=str(uuid4()),
            child=self.reference,
            operation=operation,
            turn=turn,
            arguments={key: encode_value(value) for key, value in arguments.items()},
        )
        if operation in (ChildOperation.start, ChildOperation.write_prompt):
            if self.initial_delivery_cancelled:
                raise RemoteInitialDeliveryCancelled()
            self.initial_commands.append(command)
            if self.record_command is not None:
                await self.record_command(command)
            if self.initial_delivery_cancelled:
                raise RemoteInitialDeliveryCancelled()
        elif operation is ChildOperation.stop and self.record_command is not None:
            await self.record_command(command)
        if operation is ChildOperation.start:
            try:
                encode_frame(
                    WireMessage(
                        WireMessageKind.command,
                        command.command_id,
                        cast(JsonObject, encode_value(command)),
                    )
                )
            except ProtocolRefused as error:
                raise backend.BackendSpawnFailed(str(error)) from error
        try:
            result = await self._send_command(command)
        except RemoteInputTransferFailed as error:
            error_type = (
                backend.BackendSpawnFailed
                if operation is ChildOperation.start
                else backend.PromptWriteFailed
            )
            raise error_type(str(error)) from error
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
        if operation is ChildOperation.write_prompt:
            self.prompt_accepted = True
        return result.result

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        try:
            snapshot = self._skill_snapshot_supplier(resolved_start)
            validate_skill_snapshot(snapshot)
        except Exception as error:
            raise backend.BackendSpawnFailed(
                f"Canonical skill snapshot unavailable: {error}"
            ) from error
        arguments: dict[str, Any] = {
            "canonical_skill_snapshot": snapshot,
            "resolved_start": resolved_start,
            "vendor_session_cursor": vendor_session_cursor,
        }
        if resolved_start.workspace_path_policy == WorkspacePathPolicy.legacy_home_mapping:
            try:
                relative = resolved_start.workspace_folder.resolve().relative_to(
                    Path.home().resolve()
                )
            except ValueError:
                pass
            else:
                arguments["workspace_home_relative"] = (
                    "" if relative == Path(".") else relative.as_posix()
                )
        await self._call(ChildOperation.start, **arguments)

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


class RemoteInitialDeliveryCancelled(RuntimeError):
    """Local shutdown owns disposition; this is not a native command outcome."""
