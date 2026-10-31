"""VPS backend adapter: native children remain owned by the Mac process."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from planner.conversation.admission.contracts import AdmissionExpectation
from uuid import uuid4

from planner.conversation.admission.service import bind_command
from planner.conversation.backends import contracts as backend
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    DeliveryCancelled,
    RemoteCommandUncertain,
)
from planner.conversation.contracts import (
    AnswerFate,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import UserInputAnswer
from planner.conversation.message_content import MessageContent
from planner.core.db import connect
from planner.execution_identity import (
    AskKind,
    AskReference,
    ChildReference,
    ExecutorIdentity,
    ProtocolRefused,
)
from planner.executor.protocol import (
    AnswerPermission,
    AnswerUserInput,
    CancelTurn,
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildCommandArguments,
    ChildInventory,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    DurableAnswer,
    JsonObject,
    StartRun,
    Steer,
    StopRun,
    WireMessage,
    WireMessageKind,
    WritePrompt,
    decode_prompt_result,
    decode_provider_submission,
    encode_message,
    encode_value,
    type_adapter,
)
from planner.executor.skills import validate_skill_snapshot
from planner.executor.store import ExecutorStore

type SkillSnapshotSupplier = Callable[[ResolvedConversationStart], CanonicalSkillSnapshot]
type SendCommand = Callable[[ChildCommand], Awaitable[CommandOutcome]]
type CancelInitialDelivery = Callable[[ChildReference, tuple[str, ...]], Awaitable[bool]]


class MacRemoteChild:
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
        send_answer: Callable[[ChildCommand], None] | None = None,
    ) -> None:
        self.reference = reference
        self._send_answer = send_answer
        self.saved_answers: dict[str, ChildCommand] = {}
        self._callbacks: set[AskReference] | None = None
        self._retire_reference = retire_reference
        self._send_command = send_command
        self._skill_snapshot_supplier = skill_snapshot_supplier
        self._cancel_initial_delivery = cancel_initial_delivery
        self.initial_history_complete = initial_history_complete
        self._delivery_commands: list[ChildCommand] = []
        self.initial_delivery_cancelled = False
        self.prompt_accepted = False

    @property
    def stop_outside_lock(self) -> bool:
        return True

    async def detach(self) -> None:
        self.saved_answers.clear()
        self._callbacks = set()
        if self._retire_reference is not None:
            self._retire_reference(self.reference)

    async def withdraw_delivery(self) -> bool:
        """Fence initial work; true proves the prompt never called the provider.

        A successful child start does not prove prompt acceptance. Restored children
        lack complete local transmission history.
        """
        self.initial_delivery_cancelled = True
        may_have_sent = (
            await self._cancel_initial_delivery(
                self.reference, tuple(command.command_id for command in self._delivery_commands)
            )
            if self._cancel_initial_delivery is not None
            else any(
                command.operation is ChildOperation.write_prompt
                for command in self._delivery_commands
            )
        )
        return self.initial_history_complete and not may_have_sent and not self.prompt_accepted

    async def _call(
        self, arguments: ChildCommandArguments, turn: backend.TurnToken | None = None
    ) -> JsonObject:
        operation = ChildOperation(arguments.kind)
        command = ChildCommand(
            command_id=str(uuid4()),
            child=self.reference,
            operation=operation,
            turn=turn,
            arguments=arguments,
        )
        if operation in (ChildOperation.start, ChildOperation.write_prompt):
            if self.initial_delivery_cancelled:
                raise DeliveryCancelled()
            if operation is ChildOperation.write_prompt:
                self._delivery_commands = [
                    earlier
                    for earlier in self._delivery_commands
                    if earlier.operation is ChildOperation.start
                ]
                self.prompt_accepted = False
            self._delivery_commands.append(command)
        if operation is ChildOperation.start:
            try:
                encode_message(
                    WireMessage(
                        WireMessageKind.command,
                        command.command_id,
                        cast(JsonObject, encode_value(command)),
                    )
                )
            except ProtocolRefused as error:
                raise backend.BackendSpawnFailed(str(error)) from error
        result = await self._send_command(command)
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
            if error_name == "BackendPromptRejected":
                raise backend.BackendPromptRejected(
                    str(result.result.get("error_message", "provider rejected prompt")),
                    provider_submission=decode_provider_submission(
                        result.result.get("provider_submission")
                    ),
                )
            if error_name == "AdmissionRefused":
                raise backend.AdmissionRefused(
                    str(result.result.get("error_message", "model verification refused")),
                    reason=str(result.result.get("reason", "context_changed")),
                    corrective_action=str(
                        result.result.get("corrective_action", "verify_launch_context")
                    ),
                )
            errors: dict[str, type[backend.BackendAdapterError]] = {
                name: getattr(backend, name)
                for name in (
                    "AdmissionRefused",
                    "BackendSpawnFailed",
                    "SessionLoadFailed",
                    "PromptWriteFailed",
                    "PromptWriteRefused",
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
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
        emission_check: backend.EmissionCheck | None = None,
        admission_expectation: AdmissionExpectation | None = None,
    ) -> None:
        try:
            snapshot = self._skill_snapshot_supplier(resolved_start)
            validate_skill_snapshot(snapshot)
        except Exception as error:
            raise backend.BackendSpawnFailed(
                f"Canonical skill snapshot unavailable: {error}"
            ) from error
        relative_path = None
        if resolved_start.workspace_path_policy == WorkspacePathPolicy.legacy_home_mapping:
            try:
                relative = resolved_start.workspace_folder.resolve().relative_to(
                    Path.home().resolve()
                )
            except ValueError:
                pass
            else:
                relative_path = "" if relative == Path(".") else relative.as_posix()
        await self._call(
            StartRun(
                canonical_skill_snapshot=snapshot,
                resolved_start=resolved_start,
                vendor_session_cursor=vendor_session_cursor,
                workspace_home_relative=relative_path,
                admission_expectation=None
                if admission_expectation is None
                else bind_command(admission_expectation, child=self.reference, turn=None),
            )
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
        reasoning_effort_is_set: bool = False,
        emission_check: backend.EmissionCheck | None = None,
        admission_expectation: AdmissionExpectation | None = None,
    ) -> backend.BackendPromptAccepted:
        return decode_prompt_result(
            await self._call(
                WritePrompt(
                    content=content,
                    sender_label=sender_label,
                    sender_content=sender_content,
                    sender_message_count=sender_message_count,
                    mode=mode,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                    reasoning_effort_is_set=reasoning_effort_is_set,
                    automatic_compaction=automatic_compaction,
                    admission_expectation=None
                    if admission_expectation is None
                    else bind_command(admission_expectation, child=self.reference, turn=turn_token),
                ),
                turn_token,
            )
        )

    async def steer(
        self,
        turn_token: backend.TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent | None = None,
        sender_label: str,
        emission_check: backend.EmissionCheck | None = None,
        admission_expectation: AdmissionExpectation | None = None,
    ) -> backend.BackendSteerOutcome:
        result = await self._call(
            Steer(
                content=content,
                sender_content=sender_content,
                sender_label=sender_label,
                admission_expectation=None
                if admission_expectation is None
                else bind_command(admission_expectation, child=self.reference, turn=turn_token),
            ),
            turn_token,
        )
        if result["kind"] == "steer_refused":
            return backend.BackendSteerRefused(
                PromptDeliveryRefusalReason(str(result["refusal_reason"])),
                provider_submission=decode_provider_submission(result.get("provider_submission")),
            )
        if result["kind"] == "steer_uncertain":
            delivered = result.get("composed_content_delivered")
            return backend.BackendSteerUncertain(delivered if isinstance(delivered, bool) else None)
        return backend.BackendSteerAccepted(
            bool(result.get("composed_content_delivered", True)),
            provider_submission=decode_provider_submission(result.get("provider_submission")),
        )

    async def cancel_running_turn(self) -> None:
        await self._call(CancelTurn())

    def observe_callback(self, ask: AskReference) -> None:
        if self._callbacks is not None:
            self._callbacks.add(ask)

    def observe_callbacks(self, inventory: ChildInventory | None) -> None:
        self._callbacks = (
            set()
            if inventory is None
            else {
                *inventory.pending_asks,
                *(
                    AskReference(
                        self.reference, inventory.active_turn, request_id, AskKind.user_input
                    )
                    for request_id in inventory.pending_user_input_ids
                    if inventory.active_turn is not None
                ),
            }
        )
        self._callbacks = {
            ask
            for ask in self._callbacks
            if inventory is not None and ask.turn == inventory.active_turn
        }

    def can_emit_answer(self, command: ChildCommand) -> bool:
        args = command.arguments
        return (
            self.saved_answers.get(command.command_id) == command
            and isinstance(args, (AnswerPermission, AnswerUserInput))
            and args.answer is not None
            and (self._callbacks is None or args.answer.ask in self._callbacks)
        )

    def _save_answer(self, answer: DurableAnswer) -> AnswerFate:
        if self._send_answer is None:
            return AnswerFate.refused
        args = (
            AnswerPermission(
                ask_id=answer.ask.ask_id, option_id=answer.option_id or "", answer=answer
            )
            if answer.ask.kind is AskKind.permission
            else AnswerUserInput(
                request_id=answer.ask.ask_id, answers=answer.user_input_answers or (), answer=answer
            )
        )
        command = ChildCommand(
            answer.answer_id, self.reference, ChildOperation(args.kind), answer.ask.turn, args
        )
        self.saved_answers[answer.answer_id] = command
        self._send_answer(command)
        return AnswerFate.saved

    async def answer_permission_ask(
        self,
        ask_id: str,
        option_id: str,
        *,
        answer_id: str | None = None,
        turn_token: backend.TurnToken | None = None,
    ) -> AnswerFate:
        if answer_id is None or turn_token is None:
            return AnswerFate.refused
        return self._save_answer(
            DurableAnswer(
                answer_id,
                AskReference(self.reference, turn_token, ask_id, AskKind.permission),
                option_id,
                time.time_ns() // 1_000_000,
            )
        )

    async def answer_user_input(
        self,
        request_id: str,
        answers: tuple[UserInputAnswer, ...],
        *,
        answer_id: str | None = None,
        turn_token: backend.TurnToken | None = None,
    ) -> AnswerFate:
        if answer_id is None or turn_token is None:
            return AnswerFate.refused
        return self._save_answer(
            DurableAnswer(
                answer_id,
                AskReference(self.reference, turn_token, request_id, AskKind.user_input),
                None,
                time.time_ns() // 1_000_000,
                answers,
            )
        )

    async def stop(self) -> None:
        await self._call(StopRun())


def command_emission_decision(
    db_path: str, command: ChildCommand, identity: ExecutorIdentity | None, may_refuse: bool
) -> bool | CommandOutcome | AdmissionRefused:
    """Use original policy facts; never turn uncertain delivery into an unsent refusal."""
    if command.operation not in (ChildOperation.start, ChildOperation.write_prompt):
        return True
    from planner.conversation.admission.policy import check_expectation_policy

    conn = connect(db_path)
    try:
        args = command.arguments
        expected = (
            args.admission_expectation if isinstance(args, (StartRun, WritePrompt, Steer)) else None
        )
        if expected is None:
            original = conn.execute(
                "SELECT a.principal_kind,c.backend_key FROM executor_child_assignments a "
                "LEFT JOIN conversations c ON c.conversation_id=a.conversation_id "
                "WHERE a.executor_incarnation=? AND a.child_id=? AND a.conversation_id=?",
                (
                    command.child.executor_incarnation,
                    command.child.child_id,
                    command.child.conversation_id,
                ),
            ).fetchone()
            scoped_ticket = (
                original is not None and original[0] == "ticket" and original[1] != "hermes"
            )
            if not scoped_ticket:
                return True
        known = conn.execute(
            "SELECT outcome_json FROM executor_command_outcomes "
            "WHERE executor_incarnation=? AND command_id=?",
            (command.child.executor_incarnation, command.command_id),
        ).fetchone()
        if known is not None:
            outcome = type_adapter(CommandOutcome).validate_json(known[0])
            if outcome.child != command.child:
                raise ValueError("recorded outcome conflicts with command child")
            return outcome
        try:
            if expected is None:
                raise AdmissionRefused(
                    "model verification legacy_unscoped",
                    reason="legacy_unscoped",
                    corrective_action="classify_selection",
                )
            check_expectation_policy(conn, expected)
            if (
                expected.executor_identity is None
                or identity is None
                or expected.executor_identity.executor_incarnation != identity.executor_incarnation
            ):
                raise AdmissionRefused(
                    "The destination connection changed before submission",
                    reason="context_changed",
                    corrective_action="verify_launch_context",
                )
        except (AdmissionRefused, ValueError, TypeError) as failure:
            error = (
                failure
                if isinstance(failure, AdmissionRefused)
                else AdmissionRefused(
                    "recorded launch expectation is unproved",
                    reason="identity_unproved",
                    corrective_action="verify_launch_context",
                )
            )
            if not may_refuse:
                return error
            outcome = CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.refused,
                {
                    "error_type": "AdmissionRefused",
                    "error_message": str(error),
                    "reason": error.reason,
                    "corrective_action": error.corrective_action,
                },
            )
            # This is a parent pre-transport refusal, not a native FIFO receipt.
            # The existing canonical outcome transaction settles its exact lease.
            conn.execute("BEGIN IMMEDIATE")
            try:
                if identity is None:
                    raise ConnectionError("executor link unavailable")
                ExecutorStore._connected(conn, identity)
                ExecutorStore.record_command_outcome_in_transaction(conn, outcome)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return outcome
        return True
    finally:
        conn.close()
