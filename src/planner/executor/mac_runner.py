"""Native children and callbacks live here, independently of every connection."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventValue,
    BackendPromptRejected,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerRefused,
    NeedsRebind,
    PermissionAskRaised,
    PromptWriteRefused,
    SessionLoadFailed,
    TurnEnded,
    TurnToken,
    UserInputRequested,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ResolvedConversationStart,
)
from planner.conversation.logic.addressed_reply import with_runtime_prompt_context
from planner.conversation.message_content import MessageFile
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.production_backends import production_backend_child_factories
from planner.execution_identity import (
    AskKind,
    AskReference,
    ChildReference,
)
from planner.executor.clock import now_ms
from planner.executor.configuration import (
    EXECUTOR_CAPABILITY_ENV,
    EXECUTOR_CHILD_ID_ENV,
    EXECUTOR_ROOT_ENV,
    NATIVE_SKILLS_DIR_ENV,
    NATIVE_WORKER_CONTEXT,
    ExecutorPaths,
)
from planner.executor.files import (
    contained_path,
    install_verified,
    safe_component,
)
from planner.executor.link_files import get_file, settled_io
from planner.executor.mac_connection import MacConnection
from planner.executor.mac_event_sink import NativeEventSink
from planner.executor.mac_outbox import OutboxEntry
from planner.executor.protocol import (
    AnswerApplication,
    AnswerPermission,
    AnswerUserInput,
    BackendEvent,
    BackendEventMethod,
    CancelTurn,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    CommandState,
    DurableAnswer,
    EnvelopeKind,
    FileDownloadRequest,
    FileScope,
    FileTransfer,
    JsonObject,
    QueuedEnvelope,
    Quiescence,
    StartRun,
    Steer,
    WireMessageKind,
    WithdrawCommands,
    WritePrompt,
    as_object,
    encode_backend_event,
    encode_value,
    type_adapter,
)
from planner.executor.skills import install_native_skill_snapshot, native_skill_home
from planner.files.logic.paths import conversation_files_root
from planner.workspace_localization import localize_workspace


class MacOutboxWriter(Protocol):
    """The runner produces durable output and counts items awaiting a receipt."""

    def add(self, entry: OutboxEntry, *, ticket_id: str | None = None) -> QueuedEnvelope: ...

    def pending_count(self) -> int: ...


class _CommandAdmissionRefused(Exception):
    """An admission guard proved that the native command was not dispatched."""


_REFUSALS = (
    _CommandAdmissionRefused,
    BackendPromptRejected,
    BackendSpawnFailed,
    SessionLoadFailed,
    NeedsRebind,
    PromptWriteRefused,
)


def _outcome(
    command: ChildCommand, disposition: CommandDisposition, result: JsonObject
) -> CommandOutcome:
    return CommandOutcome(command.command_id, command.child, disposition, result)


def _error_outcome(command: ChildCommand, error: Exception) -> CommandOutcome:
    """Refused proves the provider was not reached. Anything else stays uncertain."""
    refusal = next((kind for kind in _REFUSALS if isinstance(error, kind)), None)
    return _outcome(
        command,
        CommandDisposition.refused if refusal is not None else CommandDisposition.uncertain,
        {
            "error_type": refusal.__name__ if refusal is not None else type(error).__name__,
            "error_message": str(error),
        },
    )


@dataclass
class RunningChild:
    reference: ChildReference
    capability: str
    native: BackendChild
    ticket_id: str
    launch_input: StartRun
    localized_start: ResolvedConversationStart
    backend_key: ConversationBackendKey
    active_turn: TurnToken | None = None
    asks: dict[str, AskReference] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass
class _CommandRecord:
    command: ChildCommand
    task: asyncio.Task[CommandOutcome] | None = None
    preparation: asyncio.Task[None] | None = None
    outcome: CommandOutcome | None = None
    invoking: bool = False
    required_output: AnswerApplication | CommandOutcome | None = None
    output_published: bool = False

    @property
    def phase(self) -> Literal["preparing", "invoking", "finished"]:
        if self.outcome is not None:
            return "finished"
        return "invoking" if self.invoking else "preparing"


class MacRunner:
    def __init__(
        self,
        *,
        incarnation: str,
        outbox: MacOutboxWriter,
        factory: Callable[[ResolvedConversationStart], BackendChildFactory] | None = None,
        panels_server_url: str = "",
        connection: MacConnection,
        message_files: ConversationMessageFiles,
        paths: ExecutorPaths,
        workspace_home: Path | None = None,
    ) -> None:
        self.workspace_home = workspace_home or Path.home()
        self.paths = paths
        self.incarnation = incarnation
        self.outbox = outbox
        self.connection = connection
        self.factory = factory or self._factory(panels_server_url)
        self.message_files = message_files
        self._children: dict[str, RunningChild] = {}
        self._withdrawn_commands: set[str] = set()
        self._commands: dict[str, _CommandRecord] = {}
        self.admission_stopped = False
        self._retired_children: set[str] = set()
        self._retiring_children: set[str] = set()
        self._source_tasks: dict[ChildReference, set[asyncio.Task[object]]] = {}
        self._failed_source_work: set[ChildReference] = set()

    def authenticate(self, child_id: str | None, capability: str | None) -> RunningChild:
        child = self._children.get(child_id or "")
        if (
            child is None
            or child_id in self._retiring_children
            or capability is None
            or not secrets.compare_digest(child.capability, capability)
        ):
            raise PermissionError("unknown executor child capability")
        return child

    def source_work[T](
        self,
        child: ChildReference,
        work: Callable[[], Awaitable[T]],
        *,
        final_event: bool = False,
    ) -> asyncio.Task[T]:
        """Own work that must reach the outbox before this run's stop can be proved."""
        if child.child_id in self._retired_children or (
            child.child_id in self._retiring_children and not final_event
        ):
            raise PermissionError("source child is retired or retiring")
        task = asyncio.ensure_future(work())
        self._source_tasks.setdefault(child, set()).add(cast(asyncio.Task[object], task))
        task.add_done_callback(partial(self._source_work_done, child))
        return task

    def _source_work_done(self, child: ChildReference, task: asyncio.Task[object]) -> None:
        tasks = self._source_tasks[child]
        tasks.discard(task)
        if not tasks:
            del self._source_tasks[child]
        if task.cancelled() or task.exception() is not None:
            self._failed_source_work.add(child)

    def _factory(
        self, server_url: str
    ) -> Callable[[ResolvedConversationStart], BackendChildFactory]:
        def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
            return production_backend_child_factories(panels_server_url=server_url)[
                resolved.backend_key
            ]

        return factory

    async def _send_live_event(self, event: BackendEvent) -> None:
        link = self.connection.ready_link()
        if link is not None:
            with contextlib.suppress(ConnectionError, OSError):
                await link.tell(WireMessageKind.live_backend_event, as_object(event))

    async def _prepare_inputs(self, command: ChildCommand) -> None:
        args = command.arguments
        if not isinstance(args, (WritePrompt, Steer)):
            return
        seen: set[str] = set()
        for content in (args.content, args.sender_content or ()):
            for piece in content:
                if isinstance(piece, MessageFile) and piece.stored_file_id not in seen:
                    seen.add(piece.stored_file_id)
                    await self._download_input(command, piece.stored_file_id)

    async def _download_input(self, command: ChildCommand, stored_file_id: str) -> None:
        link = self.connection.link
        if link is None:
            raise ConnectionError("executor link unavailable")
        temporary = self.paths.run / (".input-" + uuid4().hex)
        try:
            async with link.transfer_capacity():
                response = await link.request(
                    WireMessageKind.get_file,
                    as_object(FileDownloadRequest(command.command_id, stored_file_id)),
                )
                slot = type_adapter(FileTransfer).validate_python(response.payload)
                manifest = slot.manifest
                if (
                    response.kind is not WireMessageKind.file_transfer
                    or slot.direction != "download"
                    or manifest.scope is not FileScope.conversation
                    or manifest.owner_id != command.child.conversation_id
                    or Path(manifest.relative_path).name != stored_file_id
                ):
                    raise ValueError("invalid input transfer")
                root = conversation_files_root(self.paths.message_files_anchor)
                target = contained_path(
                    root, f"{safe_component(manifest.owner_id)}/{manifest.relative_path}"
                )
                self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
                await get_file(link, slot, temporary)
                if link is not self.connection.link:
                    raise ConnectionError("input generation changed")

                await settled_io(install_verified, temporary, target, manifest)

        finally:
            temporary.unlink(missing_ok=True)

    async def run(self, command: ChildCommand) -> CommandOutcome:
        record = self._commands.get(command.command_id)
        if record is not None and record.command != command:
            raise ValueError("command id reused with different content")
        if record is None:
            record = _CommandRecord(command)
            self._commands[command.command_id] = record
        if record.outcome is not None:
            await self._publish_required_output(record)
            return record.outcome
        if record.task is None:
            record.task = asyncio.create_task(self._execute(command))
        # Socket cancellation must not cancel a native operation whose fate is unknown.
        return await asyncio.shield(record.task)

    async def flush_command_output(self, command_ids: tuple[str, ...]) -> None:
        """Retry required output after a failed disk write, never the native operation."""
        for command_id in command_ids:
            record = self._commands.get(command_id)
            if record is not None and record.outcome is not None:
                await self._publish_required_output(record)

    def ask(self, command_ids: tuple[str, ...]) -> tuple[CommandState, ...]:
        states: list[CommandState] = []
        for command_id in command_ids:
            record = self._commands.get(command_id)
            states.append(
                CommandState(
                    command_id,
                    "unknown" if record is None else record.phase,
                    record.outcome if record is not None else None,
                )
            )
        return tuple(states)

    def children(self) -> tuple[ChildInventory, ...]:
        return tuple(
            ChildInventory(
                child.reference,
                child.active_turn,
                tuple(child.asks.values()),
            )
            for child in self._children.values()
        )

    def _localize_start(
        self,
        resolved: ResolvedConversationStart,
        reference: ChildReference,
        capability: str,
        workspace_home_relative: str | None,
        identity: dict[str, str],
        skill_generation: Path,
    ) -> ResolvedConversationStart:
        try:
            relative = workspace_home_relative
            workspace = localize_workspace(
                resolved.workspace_folder,
                workspace_home=self.workspace_home,
                workspace_home_relative=relative if isinstance(relative, str) else None,
            )
        except (OSError, ValueError) as error:
            raise BackendSpawnFailed(f"Mac workspace unavailable: {error}") from error
        original = resolved.role_materials
        # Native adapters merge role identity after launch settings. Remove the VPS
        # account paths and rebind transport facts here, at that final precedence.
        for key in (
            "HOME",
            "USERPROFILE",
            "CODEX_HOME",
            "HERMES_HOME",
            "CLAUDE_CONFIG_DIR",
            EXECUTOR_ROOT_ENV,
            EXECUTOR_CHILD_ID_ENV,
            EXECUTOR_CAPABILITY_ENV,
            "PLAN_TICKET_FILES_DIR",
            NATIVE_SKILLS_DIR_ENV,
        ):
            identity.pop(key, None)
        artifact_root = self.paths.artifacts / safe_component(identity["PLAN_TICKET_ID"])
        artifact_root.mkdir(parents=True, exist_ok=True)
        identity[EXECUTOR_CHILD_ID_ENV] = reference.child_id
        identity[EXECUTOR_CAPABILITY_ENV] = capability
        identity[EXECUTOR_ROOT_ENV] = str(self.paths.root)
        identity["PLAN_TICKET_FILES_DIR"] = str(artifact_root)
        identity[NATIVE_SKILLS_DIR_ENV] = str(skill_generation / "skills")
        role_text = original.role_text if original else ""
        return replace(
            resolved,
            workspace_folder=workspace,
            role_materials=ConversationRoleMaterials(role_text, tuple(identity.items())),
        )

    async def _stop_exact_child(self, command: ChildCommand) -> CommandOutcome:
        reference = command.child
        child = self._children.get(reference.child_id)
        current = asyncio.current_task()
        stop_owner = next(
            (
                record.task
                for record in self._commands.values()
                if record.task is not None
                and not record.task.done()
                and record.command.child == reference
                and record.command.operation is ChildOperation.stop
            ),
            current,
        )
        if stop_owner is not current:
            # Never join another stop: two owners otherwise wait on one another.
            return _outcome(
                command, CommandDisposition.refused, {"reason": "child_stop_in_progress"}
            )

        # Admission closes before the first await and stays closed across stop retries.
        self._retiring_children.add(reference.child_id)
        for key, other in self._commands.items():
            if (
                other.command.child == reference
                and other.command.operation is not ChildOperation.stop
            ):
                self.withdraw(key)
        commands = [
            record.task
            for record in self._commands.values()
            if record.task is not None
            and record.task is not current
            and record.command.child == reference
            and record.command.operation is not ChildOperation.stop
            and not record.task.done()
        ]
        try:
            if commands:
                await asyncio.gather(*(asyncio.shield(task) for task in commands))
            await self._finish_source_work(reference)
            if child is not None:
                async with child.lock:
                    self._mark_invoking(command)
                    await child.native.stop()
                await self._finish_source_work(reference)
            if reference in self._failed_source_work or any(
                record.command.child == reference
                and record.required_output is not None
                and not record.output_published
                for record in self._commands.values()
            ):
                raise RuntimeError("required source publication failed")
        except Exception as error:
            return _outcome(
                command,
                CommandDisposition.uncertain,
                {"error_type": type(error).__name__, "error_message": str(error)},
            )
        self._retired_children.add(reference.child_id)
        if child is not None and self._children.get(reference.child_id) is child:
            self._children.pop(reference.child_id)
        return _outcome(command, CommandDisposition.succeeded, {})

    async def _finish_source_work(self, reference: ChildReference) -> None:
        while tasks := self._source_tasks.get(reference):
            await asyncio.wait(tuple(tasks))

    async def _start_run(self, command: ChildCommand, args: StartRun) -> JsonObject:
        if command.child.child_id in self._children:
            raise BackendSpawnFailed("child identity already has a native start")
        if self.admission_stopped:
            raise _CommandAdmissionRefused("executor is quiescing")
        resolved = args.resolved_start
        if resolved.conversation_id != command.child.conversation_id:
            raise ValueError("child conversation mismatch")
        identity = dict(
            resolved.role_materials.identity_environment_variables
            if resolved.role_materials
            else ()
        )
        ticket_id = identity.get("PLAN_TICKET_ID")
        if not ticket_id:
            raise BackendSpawnFailed("Mac runs require a Ticket identity")
        if any(existing.ticket_id == ticket_id for existing in self._children.values()):
            raise BackendSpawnFailed("Ticket already has a retained source child")
        capability = secrets.token_urlsafe(32)
        try:
            generation = install_native_skill_snapshot(
                args.canonical_skill_snapshot,
                paths=self.paths,
                native_home=native_skill_home(resolved.backend_key),
            )
        except Exception as error:
            raise BackendSpawnFailed(f"Canonical skill bootstrap failed: {error}") from error
        resolved = self._localize_start(
            resolved, command.child, capability, args.workspace_home_relative, identity, generation
        )
        native = self.factory(resolved)(
            resolved_start=resolved,
            event_sink=NativeEventSink(self, command.child),
            message_files=self.message_files,
        )
        child = RunningChild(
            command.child,
            capability,
            native,
            ticket_id=ticket_id,
            launch_input=args,
            localized_start=resolved,
            model=resolved.model,
            reasoning_effort=resolved.reasoning_effort,
            backend_key=resolved.backend_key,
        )
        self._children[command.child.child_id] = child
        async with child.lock:
            self._check_child_admission(child)
            self._mark_invoking(command)
            await native.start(
                resolved,
                vendor_session_cursor=args.vendor_session_cursor,
            )
        return {}

    def _check_child_admission(self, child: RunningChild) -> None:
        if child.reference.child_id in self._retiring_children:
            raise _CommandAdmissionRefused("source child identity is retiring")

    async def _write_prompt(
        self, child: RunningChild, command: ChildCommand, args: WritePrompt
    ) -> JsonObject:
        if self.admission_stopped:
            raise _CommandAdmissionRefused("executor is quiescing")
        assert command.turn is not None
        model = child.model if args.model_change is None else args.model_change
        effort = (
            child.reasoning_effort
            if args.reasoning_effort_change is None and not args.reasoning_effort_is_set
            else args.reasoning_effort_change
        )
        previous_turn = child.active_turn
        child.active_turn = command.turn
        self._mark_invoking(command)
        try:
            result = await child.native.write_prompt(
                command.turn,
                with_runtime_prompt_context(args.content, NATIVE_WORKER_CONTEXT),
                sender_label=args.sender_label,
                sender_content=args.sender_content,
                sender_message_count=args.sender_message_count,
                mode=args.mode,
                model_change=args.model_change,
                reasoning_effort_change=args.reasoning_effort_change,
                reasoning_effort_is_set=args.reasoning_effort_is_set,
                automatic_compaction=args.automatic_compaction,
            )
        except BackendPromptRejected:
            child.active_turn = previous_turn
            raise
        child.model, child.reasoning_effort = model, effort
        return {
            "kind": "prompt_accepted",
            "composed_content_delivered": result.composed_content_delivered,
        }

    async def _steer(self, child: RunningChild, command: ChildCommand, args: Steer) -> JsonObject:
        if self.admission_stopped:
            raise _CommandAdmissionRefused("executor is quiescing")
        assert command.turn is not None
        self._mark_invoking(command)
        outcome = await child.native.steer(
            command.turn,
            with_runtime_prompt_context(args.content, NATIVE_WORKER_CONTEXT),
            sender_content=args.sender_content,
            sender_label=args.sender_label,
        )
        if isinstance(outcome, BackendSteerRefused):
            return {
                "kind": "steer_refused",
                "refusal_reason": outcome.refusal_reason.value,
            }
        return {
            "kind": "steer_accepted"
            if isinstance(outcome, BackendSteerAccepted)
            else "steer_uncertain",
            "composed_content_delivered": outcome.composed_content_delivered,
        }

    async def _cancel_turn(self, child: RunningChild, command: ChildCommand) -> JsonObject:
        self._mark_invoking(command)
        await child.native.cancel_running_turn()
        return {}

    def _check_answer(
        self, child: RunningChild, command: ChildCommand, answer: DurableAnswer | None
    ) -> None:
        if answer is None:
            return
        if child.active_turn is not None and child.active_turn != answer.ask.turn:
            raise _CommandAdmissionRefused("answer turn is no longer active")
        if child.asks.get(answer.ask.ask_id) != answer.ask:
            message = (
                "permission callback is gone"
                if answer.ask.kind is AskKind.permission
                else "user input callback is gone"
            )
            raise _CommandAdmissionRefused(message)

    def _answer_application(self, command: ChildCommand, ask: AskReference | None) -> JsonObject:
        if ask is None:
            return {}
        application = AnswerApplication(command.command_id, ask, now_ms())
        self._commands[command.command_id].required_output = application
        return {"answer_application": encode_value(application)}

    async def _answer_permission(
        self, child: RunningChild, command: ChildCommand, args: AnswerPermission
    ) -> JsonObject:
        self._check_answer(child, command, args.answer)
        self._mark_invoking(command)
        await child.native.answer_permission_ask(args.ask_id, args.option_id)
        return self._answer_application(command, child.asks.pop(args.ask_id, None))

    async def _answer_user_input(
        self, child: RunningChild, command: ChildCommand, args: AnswerUserInput
    ) -> JsonObject:
        self._check_answer(child, command, args.answer)
        self._mark_invoking(command)
        await child.native.answer_user_input(args.request_id, args.answers)
        return self._answer_application(command, child.asks.pop(args.request_id, None))

    def withdraw_commands(self, request: WithdrawCommands) -> tuple[str, ...]:
        if request.child.executor_incarnation != self.incarnation:
            raise ValueError("withdraw belongs to another executor")
        for command_id in request.command_ids:
            previous = self._commands.get(command_id)
            if previous is not None and previous.command.child != request.child:
                raise ValueError("withdraw command belongs to another child")
        return tuple(key for key in request.command_ids if self.withdraw(key))

    def withdraw(self, command_id: str) -> bool:
        """True proves this command has not called, and now cannot call, its provider."""
        if command_id in self._withdrawn_commands:
            return True
        record = self._commands.get(command_id)
        if record is not None and (record.invoking or record.outcome is not None):
            return False
        self._withdrawn_commands.add(command_id)
        task = record.preparation if record is not None else None
        if task is not None:
            task.cancel()
        return True

    def _mark_invoking(self, command: ChildCommand) -> None:
        if command.command_id in self._withdrawn_commands:
            raise _CommandAdmissionRefused("command withdrawn before provider invocation")
        self._commands[command.command_id].invoking = True

    async def _execute(self, command: ChildCommand) -> CommandOutcome:
        record = self._commands[command.command_id]
        try:
            await self._prepare(record)
            outcome = await self._dispatch(command)
        except Exception as error:
            outcome = _error_outcome(command, error)
        record.outcome = outcome
        if (
            command.operation is ChildOperation.stop
            and outcome.disposition is CommandDisposition.succeeded
        ):
            record.required_output = outcome
        await self._publish_required_output(record)
        return outcome

    async def _prepare(self, record: _CommandRecord) -> None:
        command = record.command
        if command.operation is not ChildOperation.stop:
            task = asyncio.create_task(self._prepare_inputs(command))
            record.preparation = task
            if command.command_id in self._withdrawn_commands:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if command.command_id not in self._withdrawn_commands:
                    raise
            except Exception as error:
                refusal = (
                    BackendSpawnFailed
                    if command.operation is ChildOperation.start
                    else PromptWriteRefused
                )
                raise refusal(str(error)) from error
            finally:
                record.preparation = None
        if command.command_id in self._withdrawn_commands:
            raise _CommandAdmissionRefused("command withdrawn before provider invocation")

    async def _dispatch(self, command: ChildCommand) -> CommandOutcome:
        if command.child.executor_incarnation != self.incarnation:
            return _outcome(command, CommandDisposition.refused, {"reason": "incarnation_gone"})
        if command.operation is ChildOperation.stop:
            return await self._stop_exact_child(command)
        if command.child.child_id in self._retiring_children:
            raise BackendSpawnFailed("source child identity was retired")
        args = command.arguments
        if isinstance(args, StartRun):
            result = await self._start_run(command, args)
        else:
            child = self._children.get(command.child.child_id)
            if child is None or child.reference != command.child:
                return _outcome(command, CommandDisposition.uncertain, {"reason": "child_gone"})
            async with child.lock:
                self._check_child_admission(child)
                match args:
                    case WritePrompt():
                        result = await self._write_prompt(child, command, args)
                    case Steer():
                        result = await self._steer(child, command, args)
                    case CancelTurn():
                        result = await self._cancel_turn(child, command)
                    case AnswerPermission():
                        result = await self._answer_permission(child, command, args)
                    case AnswerUserInput():
                        result = await self._answer_user_input(child, command, args)
                    case _:
                        raise ValueError("unsupported child command")
        return _outcome(
            command,
            CommandDisposition.uncertain
            if result.get("kind") == "steer_uncertain"
            else CommandDisposition.succeeded,
            result,
        )

    async def _publish_required_output(self, record: _CommandRecord) -> None:
        output = record.required_output
        if output is None or record.output_published:
            return
        if isinstance(output, AnswerApplication):
            kind = EnvelopeKind.answer_application
            child = output.ask.child
            submission_id = "application_" + hashlib.sha256(output.answer_id.encode()).hexdigest()
        else:
            # Last in this child's FIFO: accepting this item proves prior output arrived.
            kind = EnvelopeKind.child_stopped
            child = output.child
            submission_id = "stopped_" + hashlib.sha256(output.command_id.encode()).hexdigest()
        await self._emit(child.conversation_id, kind, output, submission_id=submission_id)
        record.output_published = True

    async def _emit(
        self,
        conversation_id: str,
        kind: EnvelopeKind,
        value: object,
        submission_id: str | None = None,
    ) -> None:
        payload = cast(JsonObject, encode_value(value))
        await asyncio.to_thread(
            self.outbox.add,
            OutboxEntry(conversation_id, kind, payload, submission_id),
        )

    async def report_event(
        self,
        reference: ChildReference,
        turn: TurnToken | None,
        event: BackendEventValue,
    ) -> None:
        await asyncio.shield(
            self.source_work(
                reference,
                lambda: self._report_event(reference, turn, event),
                final_event=True,
            )
        )

    async def _report_event(
        self,
        reference: ChildReference,
        turn: TurnToken | None,
        event: BackendEventValue,
    ) -> None:
        child = self._children.get(reference.child_id)
        if child is None or child.reference != reference:
            raise ValueError("event from unknown child")
        if isinstance(event, PermissionAskRaised):
            ask = event.ask
            if turn is None:
                raise ValueError("permission ask lacks turn")
            child.asks[ask.ask_id] = AskReference(reference, turn, ask.ask_id, AskKind.permission)
        elif isinstance(event, UserInputRequested):
            request = event.request
            if turn is None:
                raise ValueError("user input request lacks turn")
            child.asks[request.request_id] = AskReference(
                reference, turn, request.request_id, AskKind.user_input
            )
        elif isinstance(event, TurnEnded):
            if child.active_turn == turn:
                child.active_turn = None
                child.asks.clear()
        method, arguments = encode_backend_event(event)
        wire_event = BackendEvent(reference, method, turn, arguments)
        await asyncio.to_thread(
            self.outbox.add,
            OutboxEntry(
                reference.conversation_id,
                EnvelopeKind.backend_event,
                cast(JsonObject, encode_value(wire_event)),
            ),
            ticket_id=child.ticket_id
            if method
            in (
                BackendEventMethod.agent_message_completed,
                BackendEventMethod.tool_call_finished,
                BackendEventMethod.turn_ended,
            )
            else None,
        )

    def _quiescence_counts(self) -> tuple[int, int, int, int]:
        active = sum(child.active_turn is not None for child in self._children.values())
        callbacks = sum(len(child.asks) for child in self._children.values())
        stopped_references = {
            record.command.child
            for record in self._commands.values()
            if record.command.operation == ChildOperation.stop
            and (outcome := record.outcome) is not None
            and outcome.disposition == CommandDisposition.succeeded
            and outcome.child == record.command.child
        }
        live_references = {child.reference for child in self._children.values()}
        # Historical uncertainty does not block maintenance after its exact child stopped.
        # Native calls and required publications remain counted until they settle.
        unresolved = sum(
            record.task is not None and not record.task.done() for record in self._commands.values()
        ) + sum(
            outcome.disposition == CommandDisposition.uncertain
            and not (outcome.child in stopped_references and outcome.child not in live_references)
            for record in self._commands.values()
            if (outcome := record.outcome) is not None
        )
        unresolved += sum(
            record.required_output is not None and not record.output_published
            for record in self._commands.values()
        )
        unresolved += sum(len(tasks) for tasks in self._source_tasks.values())
        return active, callbacks, unresolved, self.outbox.pending_count()

    async def quiesce(self) -> Quiescence:
        # Admission closes synchronously before any awaited native operation.
        self.admission_stopped = True
        counts = self._quiescence_counts()
        stopped = False
        if not any(counts[:3]):
            for child in tuple(self._children.values()):
                async with child.lock:
                    if any(self._quiescence_counts()[:3]):
                        break
                    await child.native.stop()
                    self._children.pop(child.reference.child_id, None)
            # stop can emit events or expose a pending operation. Check the journal and
            # all live blockers again after the awaited boundary before granting exit.
            counts = self._quiescence_counts()
            stopped = not self._children and not any(counts)
        return Quiescence(True, *counts, stopped)
