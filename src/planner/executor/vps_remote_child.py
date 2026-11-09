"""VPS backend adapter: native children remain owned by the Mac process.

Every operation is a mailbox row. What the caller waits for differs by kind:
start, prompt, cancel and answers return once their row is committed; a steer waits up
to STEER_DELIVERY_LIMIT_SECONDS for its result, and only while the Mac is present; a
stop waits, unbounded but tracked, for proof that the agent stopped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from planner.conversation.backends import contracts as backend
from planner.conversation.backends.contracts import (
    DeliveryCancelled,
    RemoteCommandUncertain,
)
from planner.conversation.contracts import (
    AnswerFate,
    ConversationBackendKey,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import UserInputAnswer
from planner.conversation.message_content import MessageContent
from planner.execution_identity import (
    AskKind,
    AskReference,
    ChildReference,
    ProtocolRefused,
)
from planner.executor.clock import now_ms
from planner.executor.protocol import (
    AnswerPermission,
    AnswerUserInput,
    CancelTurn,
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildCommandArguments,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    DurableAnswer,
    JsonObject,
    StartRun,
    Steer,
    StopRun,
    WritePrompt,
)
from planner.executor.skills import validate_skill_snapshot

type SkillSnapshotSupplier = Callable[[ResolvedConversationStart], CanonicalSkillSnapshot]


class RemoteChildCommands(Protocol):
    """What a remote child needs from MacRuns: the mailbox, per kind."""

    async def put(self, command: ChildCommand) -> bool:
        """Commit the command's row. False: refused at put (its process was replaced, or
        the child's assignment is closed), and nothing was inserted."""
        ...

    def mac_is_present(self) -> bool: ...

    async def deliver_steer(self, command: ChildCommand) -> CommandOutcome | None:
        """The steer's outcome, or None when the Mac did not take it (away, withdrawn at
        the delivery limit, expired, or refused at put)."""
        ...

    async def stop(self, command: ChildCommand) -> CommandOutcome: ...

    async def save_answer(self, command: ChildCommand) -> bool:
        """Insert the answer under the conversation lock, only while its turn is live."""
        ...

    async def cancel_initial_delivery(
        self, child: ChildReference, command_ids: tuple[str, ...]
    ) -> bool:
        """Withdraw what is still waiting. True if a prompt may have reached the agent."""
        ...

    def retire_child(self, child: ChildReference) -> None: ...


def _raise_for(outcome: CommandOutcome) -> None:
    """A command that did not succeed, as the backend error the core expects."""
    if outcome.disposition is CommandDisposition.uncertain:
        # An uncertainty is not proof of non-delivery. Never rebind/retry this command.
        raise RemoteCommandUncertain(outcome.command_id)
    if outcome.disposition is CommandDisposition.refused:
        error_name = str(outcome.result.get("error_type", "BackendSpawnFailed"))
        if error_name == "BackendPromptRejected":
            raise backend.BackendPromptRejected(
                str(outcome.result.get("error_message", "provider rejected prompt")),
            )
        errors: dict[str, type[backend.BackendAdapterError]] = {
            name: getattr(backend, name)
            for name in (
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
            str(outcome.result.get("error_message", "remote operation refused"))
        )


_REPLACED_MESSAGE = "The Mac executor process that owned this child was replaced"


class MacRemoteChild:
    """A Mac child's commands, as mailbox rows."""

    def __init__(
        self,
        reference: ChildReference,
        commands: RemoteChildCommands,
        skill_snapshot_supplier: SkillSnapshotSupplier,
        *,
        event_sink: backend.BackendEventSink | None = None,
        initial_history_complete: bool = True,
    ) -> None:
        self.reference = reference
        self.event_sink = event_sink
        self._commands = commands
        self._skill_snapshot_supplier = skill_snapshot_supplier
        self.initial_history_complete = initial_history_complete
        self._delivery_commands: list[ChildCommand] = []
        # Initial puts still committing: a withdrawal waits for them, so a row that lands
        # after it looked is still withdrawn.
        self._initial_puts: set[asyncio.Task[bool]] = set()
        self.initial_delivery_cancelled = False
        # A Claude child takes its model and effort when it starts. Its prompt is
        # accepted at commit, so the Mac's refusal of a change would come back only as a
        # failed turn: the change is refused here instead, and the core rebinds.
        self._claude_started_on: tuple[str, str | None] | None = None

    @property
    def stop_outside_lock(self) -> bool:
        return True

    async def detach(self) -> None:
        self._commands.retire_child(self.reference)

    async def withdraw_delivery(self) -> bool:
        """Fence initial work; true proves the prompt never called the provider.

        Waiting rows are withdrawn on the VPS, which is proof they never ran. A row the
        Mac fetched may have reached the provider. Restored children lack complete local
        transmission history.
        """
        self.initial_delivery_cancelled = True
        await asyncio.gather(*self._initial_puts, return_exceptions=True)
        may_have_sent = await self._commands.cancel_initial_delivery(
            self.reference, tuple(command.command_id for command in self._delivery_commands)
        )
        return self.initial_history_complete and not may_have_sent

    def _command(
        self, arguments: ChildCommandArguments, turn: backend.TurnToken | None = None
    ) -> ChildCommand:
        return ChildCommand(
            command_id=str(uuid4()),
            child=self.reference,
            operation=ChildOperation(arguments.kind),
            turn=turn,
            arguments=arguments,
        )

    async def _put_initial(self, command: ChildCommand) -> bool:
        if self.initial_delivery_cancelled:
            raise DeliveryCancelled()
        if command.operation is ChildOperation.write_prompt:
            self._delivery_commands = [
                earlier
                for earlier in self._delivery_commands
                if earlier.operation is ChildOperation.start
            ]
        self._delivery_commands.append(command)
        put = asyncio.ensure_future(self._commands.put(command))
        self._initial_puts.add(put)
        put.add_done_callback(self._initial_puts.discard)
        # A cancelled caller does not cancel the commit, which a withdrawal then finds.
        return await asyncio.shield(put)

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        try:
            snapshot = self._skill_snapshot_supplier(resolved_start)
            validate_skill_snapshot(snapshot)
        except Exception as error:
            raise backend.BackendSpawnFailed(
                f"Canonical skill snapshot unavailable: {error}"
            ) from error
        relative_path = None
        if resolved_start.workspace_path_policy == WorkspacePathPolicy.home_relative:
            try:
                relative = resolved_start.workspace_folder.resolve().relative_to(
                    Path.home().resolve()
                )
            except ValueError:
                pass
            else:
                relative_path = "" if relative == Path(".") else relative.as_posix()
        elif resolved_start.workspace_path_policy == WorkspacePathPolicy.executor_default:
            # The placeholder names a folder inside the Mac workspace, written with that
            # workspace as "/". The workspace itself sends no subfolder.
            subfolder = resolved_start.workspace_folder.relative_to("/")
            relative_path = None if subfolder == Path(".") else subfolder.as_posix()
        command = self._command(
            StartRun(
                canonical_skill_snapshot=snapshot,
                resolved_start=resolved_start,
                vendor_session_cursor=vendor_session_cursor,
                workspace_home_relative=relative_path,
            )
        )
        try:
            placed = await self._put_initial(command)
        except ProtocolRefused as error:
            raise backend.BackendSpawnFailed(str(error)) from error
        if not placed:
            raise backend.BackendSpawnFailed(_REPLACED_MESSAGE)
        if resolved_start.backend_key is ConversationBackendKey.claude:
            self._claude_started_on = (resolved_start.model, resolved_start.reasoning_effort)

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
    ) -> backend.BackendPromptAccepted:
        """Accepted at commit. A prompt the Mac then cannot deliver ends its turn failed
        through the ordered outbox."""
        if self._claude_started_on is not None:
            model, effort = self._claude_started_on
            if (model_change is not None and model_change != model) or (
                (reasoning_effort_change is not None or reasoning_effort_is_set)
                and reasoning_effort_change != effort
            ):
                raise backend.NeedsRebind(
                    "claude takes its model and reasoning effort when the child starts"
                )
        command = self._command(
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
            ),
            turn_token,
        )
        try:
            placed = await self._put_initial(command)
        except ProtocolRefused as error:
            raise backend.PromptWriteRefused(str(error)) from error
        if not placed:
            raise backend.PromptWriteRefused(_REPLACED_MESSAGE)
        return backend.BackendPromptAccepted(composed_content_delivered=True)

    async def steer(
        self,
        turn_token: backend.TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent | None = None,
        sender_label: str,
    ) -> backend.BackendSteerOutcome:
        not_delivered = backend.BackendSteerRefused(
            PromptDeliveryRefusalReason.remote_steer_not_delivered
        )
        if not self._commands.mac_is_present():
            # No row: the core queues the text as the next message.
            return not_delivered
        try:
            outcome = await self._commands.deliver_steer(
                self._command(
                    Steer(
                        content=content,
                        sender_content=sender_content,
                        sender_label=sender_label,
                    ),
                    turn_token,
                )
            )
        except ProtocolRefused:
            return not_delivered  # Too large for one command: nothing was written.
        if outcome is None:
            return not_delivered
        if outcome.disposition is CommandDisposition.uncertain:
            delivered = outcome.result.get("composed_content_delivered")
            return backend.BackendSteerUncertain(delivered if isinstance(delivered, bool) else None)
        _raise_for(outcome)
        result: JsonObject = outcome.result
        if result["kind"] == "steer_refused":
            return backend.BackendSteerRefused(
                PromptDeliveryRefusalReason(str(result["refusal_reason"])),
            )
        if result["kind"] == "steer_uncertain":
            delivered = result.get("composed_content_delivered")
            return backend.BackendSteerUncertain(delivered if isinstance(delivered, bool) else None)
        return backend.BackendSteerAccepted(
            bool(result.get("composed_content_delivered", True)),
        )

    async def cancel_running_turn(self) -> None:
        """Returns at commit. The VPS ends the turn itself; a cancel the Mac could not
        carry out retires the child (MacRuns). Refused at put, the replaced process's
        drain ends the turn."""
        await self._commands.put(self._command(CancelTurn()))

    async def _save_answer(self, answer: DurableAnswer) -> AnswerFate:
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
        try:
            saved = await self._commands.save_answer(command)
        except ProtocolRefused:
            return AnswerFate.refused  # Too large for one command: nothing was written.
        return AnswerFate.saved if saved else AnswerFate.refused

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
        return await self._save_answer(
            DurableAnswer(
                answer_id,
                AskReference(self.reference, turn_token, ask_id, AskKind.permission),
                option_id,
                now_ms(),
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
        return await self._save_answer(
            DurableAnswer(
                answer_id,
                AskReference(self.reference, turn_token, request_id, AskKind.user_input),
                None,
                now_ms(),
                answers,
            )
        )

    async def stop(self) -> None:
        _raise_for(await self._commands.stop(self._command(StopRun())))
