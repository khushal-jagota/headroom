"""Native children and callbacks live here, independently of every connection."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import cast

from pydantic import TypeAdapter

from planner.conversation.backends.codex_app_server.login import codex_home
from planner.conversation.backends.contracts import (
    BackendAdapterError,
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationRoleMaterials,
    MoveRetirementDisposition,
    MoveRetirementResult,
    ResolvedConversationStart,
)
from planner.conversation.logic.user_input import ordered_user_input_answers
from planner.conversation.message_files import ConversationMessageFiles
from planner.executor.codec import encode_backend_event, encode_value, invoke_child
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    AllocatingDurableSpool,
    AnswerApplication,
    AskKind,
    AskReference,
    AuthorizedAction,
    BackendEvent,
    BackendEventMethod,
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    DurableAnswer,
    EnvelopeKind,
    ExecutorIdentity,
    FileSnapshot,
    JsonObject,
    Quiescence,
    ReconciliationInventory,
    SourceRetirementReceipt,
    SourceRetirementRequest,
)
from planner.executor.files import safe_component, snapshot_ticket
from planner.executor.skills import install_native_skill_snapshot, validate_skill_snapshot


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class _CommandAdmissionRefused(Exception):
    """An admission guard proved that the native command was not dispatched."""


@dataclass
class _Child:
    reference: ChildReference
    capability: str
    native: BackendChild
    ticket_id: str | None = None
    active_turn: TurnToken | None = None
    asks: dict[str, AskReference] = field(default_factory=dict)
    user_inputs: dict[str, tuple[AskReference, BackendUserInputRequest]] = field(
        default_factory=dict
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class JournalEventSink:
    def __init__(self, registry: NativeChildRegistry, reference: ChildReference) -> None:
        self.registry = registry
        self.reference = reference

    def __getattr__(self, name: str) -> Callable[..., Awaitable[None]]:
        method = BackendEventMethod(name)
        signature = inspect.signature(getattr(BackendEventSink, name))

        async def report(*args: object, **kwargs: object) -> None:
            bound = signature.bind(None, *args, **kwargs)
            arguments = dict(bound.arguments)
            arguments.pop("self", None)
            turn = cast(TurnToken | None, arguments.pop("turn_token", None))
            await self.registry.report_event(self.reference, method, turn, arguments)

        return report


class NativeChildRegistry:
    def __init__(
        self,
        *,
        incarnation: str,
        spool: AllocatingDurableSpool,
        factory: Callable[[ResolvedConversationStart, str, ChildReference], BackendChildFactory],
        message_files: ConversationMessageFiles,
        paths: ExecutorPaths | None = None,
        workspace_home: Path | None = None,
    ) -> None:
        self.workspace_home = workspace_home or Path.home()
        self.paths = paths
        self.incarnation = incarnation
        self.spool = spool
        self.factory = factory
        self.message_files = message_files
        self.children: dict[str, _Child] = {}
        self.outcomes: dict[str, CommandOutcome] = {}
        self._commands: dict[str, ChildCommand] = {}
        self._tasks: dict[str, asyncio.Task[CommandOutcome]] = {}
        self._applications: dict[str, AnswerApplication] = {}
        self._answers: dict[str, DurableAnswer] = {}
        self._outcomes_published: set[str] = set()
        self._applications_published: set[str] = set()
        self._confirmed_command_ids: set[str] = set()
        self.admission_stopped = False
        self._retired_children: set[str] = set()
        self._retiring_children: set[str] = set()
        self._retiring_tickets: set[str] = set()
        self._source_work: dict[str, int] = {}
        self._retirements: dict[
            str, tuple[SourceRetirementRequest, asyncio.Task[SourceRetirementReceipt]]
        ] = {}

    def authenticate(self, child_id: str | None, capability: str | None) -> _Child:
        child = self.children.get(child_id or "")
        if (
            child is None
            or child_id in self._retired_children
            or child_id in self._retiring_children
            or child.ticket_id in self._retiring_tickets
            or capability is None
            or not secrets.compare_digest(child.capability, capability)
        ):
            raise PermissionError("unknown executor child capability")
        return child

    def begin_source_work(self, child: ChildReference) -> None:
        native = self.children.get(child.child_id)
        if (
            child.child_id in self._retired_children
            or child.child_id in self._retiring_children
            or (native is not None and native.ticket_id in self._retiring_tickets)
        ):
            raise PermissionError("source child is retired or retiring")
        key = child.conversation_id
        self._source_work[key] = self._source_work.get(key, 0) + 1

    async def source_work[T](
        self,
        child: ChildReference,
        work: Callable[[], Awaitable[T]],
        *,
        new_work: bool = False,
    ) -> T:
        # Existing native work can still publish its final events after admission closes.
        if new_work and self.admission_stopped:
            raise PermissionError("executor admission stopped")
        self.begin_source_work(child)
        task = asyncio.ensure_future(work())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A cancelled catalog refresh or IPC must not release its source count
            # while its snapshot/publication thread still owns durable work.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise
        finally:
            self.end_source_work(child)

    def end_source_work(self, child: ChildReference) -> None:
        key = child.conversation_id
        self._source_work[key] -= 1
        if not self._source_work[key]:
            del self._source_work[key]

    async def retire_source(
        self,
        request_id: str,
        request: SourceRetirementRequest,
        identity: ExecutorIdentity,
    ) -> SourceRetirementReceipt:
        previous = self._retirements.get(request_id)
        if previous is not None:
            if previous[0] != request:
                raise ValueError("source retirement id reused with different content")
            return await asyncio.shield(previous[1])
        task = asyncio.create_task(self._retire_source(request, identity))
        self._retirements[request_id] = (request, task)
        return await asyncio.shield(task)

    async def _retire_source(
        self,
        request: SourceRetirementRequest,
        identity: ExecutorIdentity,
    ) -> SourceRetirementReceipt:
        def receipt(
            disposition: MoveRetirementDisposition, reason: str | None = None
        ) -> SourceRetirementReceipt:
            return SourceRetirementReceipt(
                identity,
                request.ticket_id,
                request.conversation_id,
                request.assigned_children,
                MoveRetirementResult(disposition, reason),
            )

        refs = set(request.assigned_children)
        ids = {ref.child_id for ref in refs}
        if (
            request.expected_identity != identity
            or identity.executor_incarnation != self.incarnation
            or len(ids) != len(request.assigned_children)
            or any(ref.executor_incarnation != self.incarnation for ref in refs)
            or request.ticket_id in self._retiring_tickets
        ):
            return receipt(
                MoveRetirementDisposition.refused,
                "source identity is not current or retirement is in progress",
            )
        conversations = {ref.conversation_id for ref in refs}
        if request.conversation_id is not None:
            conversations.add(request.conversation_id)
        children = [
            child
            for child in self.children.values()
            if child.ticket_id == request.ticket_id
            or child.reference.conversation_id in conversations
        ]
        if any(
            child.ticket_id != request.ticket_id or child.reference not in refs
            for child in children
        ):
            return receipt(
                MoveRetirementDisposition.refused,
                "source child assignment is unknown or conflicting",
            )
        self._retiring_tickets.add(request.ticket_id)
        self._retiring_children.update(ids)
        try:
            busy = (
                any(
                    child.active_turn is not None
                    or child.asks
                    or child.user_inputs
                    or child.lock.locked()
                    for child in children
                )
                or any(self._source_work.get(conv, 0) for conv in conversations)
                or any(
                    not task.done()
                    for key, task in self._tasks.items()
                    if self._commands[key].child.child_id in ids
                )
                or any(
                    outcome.disposition is CommandDisposition.uncertain
                    for outcome in self.outcomes.values()
                    if outcome.child.child_id in ids
                )
                or any(
                    command_id not in self._outcomes_published
                    for command_id, outcome in self.outcomes.items()
                    if outcome.child.child_id in ids
                )
                or any(
                    answer_id not in self._applications_published
                    for answer_id, application in self._applications.items()
                    if application.ask.child.child_id in ids
                )
                or any(self.spool.has_pending(conv) for conv in conversations)
            )
            if busy:
                return receipt(
                    MoveRetirementDisposition.refused, "source has activity or waiting messages"
                )
            # Tombstone identities before native effects, fencing delayed starts.
            self._retired_children.update(ids)
            for child in children:
                try:
                    async with child.lock:
                        await child.native.stop()
                except Exception:
                    return receipt(
                        MoveRetirementDisposition.uncertain, "source stop could not be confirmed"
                    )
            for child in children:
                self.children.pop(child.reference.child_id, None)
            if any(self._source_work.get(conv, 0) for conv in conversations) or any(
                self.spool.has_pending(conv) for conv in conversations
            ):
                return receipt(
                    MoveRetirementDisposition.uncertain, "source retirement produced unsettled work"
                )
            return receipt(MoveRetirementDisposition.retired)
        finally:
            self._retiring_tickets.discard(request.ticket_id)
            self._retiring_children.difference_update(ids)

    async def execute_command(self, command: ChildCommand) -> CommandOutcome:
        previous = self._commands.get(command.command_id)
        if previous is not None and previous != command:
            raise ValueError("command id reused with different content")
        if command.command_id in self.outcomes:
            outcome = self.outcomes[command.command_id]
            if command.command_id not in self._outcomes_published:
                await self._publish_outcome(outcome)
            return outcome
        task = self._tasks.get(command.command_id)
        if (
            task is not None
            and task.done()
            and not task.cancelled()
            and task.exception() is not None
            and self._can_resume_answer_wrapper(command)
        ):
            # Only reconstruct the durable-answer wrapper around a retained native
            # result/application. An unknown native operation is never redispatched.
            task = None
        if task is None:
            self._commands[command.command_id] = command
            task = asyncio.create_task(self._execute(command))
            self._tasks[command.command_id] = task
        # Socket cancellation must not cancel a native operation whose fate is unknown.
        return await asyncio.shield(task)

    def _can_resume_answer_wrapper(self, command: ChildCommand) -> bool:
        if (
            command.operation
            not in (ChildOperation.answer_permission_ask, ChildOperation.answer_user_input)
            or "answer" not in command.arguments
        ):
            return False
        answer = TypeAdapter(DurableAnswer).validate_python(command.arguments["answer"])
        expected = self._native_answer_command(answer)
        if (
            answer.ask.child != command.child
            or answer.ask.turn != command.turn
            or command.operation != expected.operation
        ):
            return False
        application = self._applications.get(answer.answer_id)
        if application is not None:
            return application.ask == answer.ask
        native = self._commands.get(answer.answer_id)
        return (
            native is not None
            and answer.answer_id in self.outcomes
            and native.child == answer.ask.child
            and native.turn == answer.ask.turn
            and native == expected
        )

    def _localize_start(
        self,
        resolved: ResolvedConversationStart,
        reference: ChildReference,
        capability: str,
        arguments: JsonObject,
    ) -> ResolvedConversationStart:
        try:
            if "workspace_home_relative" in arguments:
                relative = arguments["workspace_home_relative"]
                if (
                    not isinstance(relative, str)
                    or "\\" in relative
                    or (relative and any(part in ("", ".", "..") for part in relative.split("/")))
                    or (isinstance(relative, str) and PurePosixPath(relative).is_absolute())
                ):
                    raise ValueError("unsafe home-relative workspace")
                home = self.workspace_home.resolve(strict=True)
                workspace = (home / relative).resolve(strict=True)
                workspace.relative_to(home)
            else:
                # An explicit absolute native path is usable only if that exact
                # directory exists; never substitute cwd or another checkout.
                if not resolved.workspace_folder.is_absolute():
                    raise ValueError("workspace needs absolute native path or home mapping")
                workspace = resolved.workspace_folder.resolve(strict=True)
            if not workspace.is_dir():
                raise ValueError("mapped workspace is not a directory")
        except (OSError, ValueError) as error:
            raise BackendSpawnFailed(f"Mac workspace unavailable: {error}") from error
        original = resolved.role_materials
        identity = dict(original.identity_environment_variables if original else ())
        # Native adapters merge role identity after launch settings. Remove the VPS
        # account paths and rebind transport facts here, at that final precedence.
        for key in (
            "HOME",
            "USERPROFILE",
            "CODEX_HOME",
            "HERMES_HOME",
            "CLAUDE_CONFIG_DIR",
            "PLAN_EXECUTOR_ROOT",
            "PLAN_EXECUTOR_CHILD_ID",
            "PLAN_EXECUTOR_CAPABILITY",
            "PLAN_TICKET_FILES_DIR",
        ):
            identity.pop(key, None)
        identity["PLAN_EXECUTOR_CHILD_ID"] = reference.child_id
        identity["PLAN_EXECUTOR_CAPABILITY"] = capability
        if self.paths is not None:
            ticket_id = identity.get("PLAN_TICKET_ID", resolved.conversation_id)
            artifact_root = self.paths.artifacts / safe_component(ticket_id)
            artifact_root.mkdir(parents=True, exist_ok=True)
            identity["PLAN_EXECUTOR_ROOT"] = str(self.paths.root)
            identity["PLAN_TICKET_FILES_DIR"] = str(artifact_root)
        role_text = original.role_text if original else ""
        if self.paths is not None:
            role_text += (
                "\n\nFor native execution, PLAN_TICKET_FILES_DIR supersedes all VPS-local "
                "Ticket artifact write paths in the skills. Write Ticket artifacts beneath "
                "PLAN_TICKET_FILES_DIR. Keep served /files/tickets/<ticket-id>/<relative-path> "
                "links in Ticket fields."
            )
        return replace(
            resolved,
            workspace_folder=workspace,
            role_materials=ConversationRoleMaterials(role_text, tuple(identity.items())),
        )

    async def _execute(self, command: ChildCommand) -> CommandOutcome:
        if (
            command.child.child_id in self._retired_children
            or command.child.child_id in self._retiring_children
        ):
            outcome = CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.refused,
                {
                    "error_type": "BackendSpawnFailed",
                    "error_message": "source child identity was retired",
                },
            )
        elif command.child.executor_incarnation != self.incarnation:
            outcome = CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.refused,
                {"reason": "incarnation_gone"},
            )
        elif (
            command.operation
            in (ChildOperation.answer_permission_ask, ChildOperation.answer_user_input)
            and "answer" in command.arguments
        ):
            answer = TypeAdapter(DurableAnswer).validate_python(command.arguments["answer"])
            if (
                answer.ask.child != command.child
                or answer.ask.turn != command.turn
                or command.operation != self._native_answer_command(answer).operation
            ):
                raise ValueError("durable answer target conflicts with command")
            application = await self.apply_answer(answer)
            outcome = CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.succeeded
                if application is not None
                else CommandDisposition.uncertain,
                {"applied": application is not None},
            )
        else:
            try:
                native_command = command
                child = self.children.get(command.child.child_id)
                if child is not None and command.operation == ChildOperation.start:
                    raise BackendSpawnFailed("child identity already has a native start")
                if child is None and command.operation == ChildOperation.start:
                    if self.admission_stopped:
                        raise _CommandAdmissionRefused("executor is quiescing")
                    resolved = TypeAdapter(ResolvedConversationStart).validate_python(
                        command.arguments["resolved_start"]
                    )
                    if resolved.conversation_id != command.child.conversation_id:
                        raise ValueError("child conversation mismatch")
                    environment = dict(
                        resolved.role_materials.identity_environment_variables
                        if resolved.role_materials
                        else ()
                    )
                    ticket_id = environment.get("PLAN_TICKET_ID")
                    if ticket_id is not None and any(
                        existing.ticket_id == ticket_id for existing in self.children.values()
                    ):
                        raise BackendSpawnFailed("Ticket already has a retained source child")
                    try:
                        snapshot = TypeAdapter(CanonicalSkillSnapshot).validate_python(
                            command.arguments["canonical_skill_snapshot"]
                        )
                        validate_skill_snapshot(snapshot)
                        if self.paths is None:
                            raise ValueError("executor paths are required for canonical skills")
                    except Exception as error:
                        raise BackendSpawnFailed(
                            f"Canonical skill bootstrap failed: {error}"
                        ) from error
                    capability = secrets.token_urlsafe(32)
                    resolved = self._localize_start(
                        resolved, command.child, capability, command.arguments
                    )
                    try:
                        install_native_skill_snapshot(
                            snapshot, paths=self.paths, native_home=codex_home(os.environ)
                        )
                    except Exception as error:
                        raise BackendSpawnFailed(
                            f"Canonical skill bootstrap failed: {error}"
                        ) from error
                    native_command = replace(
                        command,
                        arguments={**command.arguments, "resolved_start": encode_value(resolved)},
                    )
                    sink = JournalEventSink(self, command.child)
                    native = self.factory(resolved, capability, command.child)(
                        resolved_start=resolved,
                        event_sink=cast(BackendEventSink, sink),
                        message_files=self.message_files,
                    )
                    identity = dict(
                        resolved.role_materials.identity_environment_variables
                        if resolved.role_materials
                        else ()
                    )
                    child = _Child(
                        command.child, capability, native, ticket_id=identity.get("PLAN_TICKET_ID")
                    )
                    self.children[command.child.child_id] = child
                if child is None or child.reference != command.child:
                    outcome = CommandOutcome(
                        command.command_id,
                        command.child,
                        CommandDisposition.uncertain,
                        {"reason": "child_gone"},
                    )
                else:
                    async with child.lock:
                        if command.operation == ChildOperation.write_prompt:
                            if self.admission_stopped:
                                raise _CommandAdmissionRefused("executor is quiescing")
                            child.active_turn = command.turn
                        result = await invoke_child(child.native, native_command)
                        if command.operation == ChildOperation.answer_permission_ask:
                            ask = child.asks.get(str(command.arguments["ask_id"]))
                            if ask is not None:
                                application = AnswerApplication(command.command_id, ask, now_ms())
                                self._applications[command.command_id] = application
                                result = {**result, "answer_application": encode_value(application)}
                            child.asks.pop(str(command.arguments["ask_id"]), None)
                        elif command.operation == ChildOperation.answer_user_input:
                            pending = child.user_inputs.get(str(command.arguments["request_id"]))
                            if pending is not None:
                                application = AnswerApplication(
                                    command.command_id, pending[0], now_ms()
                                )
                                self._applications[command.command_id] = application
                                result = {**result, "answer_application": encode_value(application)}
                            child.user_inputs.pop(str(command.arguments["request_id"]), None)
                        elif command.operation == ChildOperation.stop:
                            self.children.pop(child.reference.child_id, None)
                        outcome = CommandOutcome(
                            command.command_id, command.child, CommandDisposition.succeeded, result
                        )
            except _CommandAdmissionRefused as error:
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.refused,
                    {"error_type": type(error).__name__, "error_message": str(error)},
                )
            except (ValueError, BackendAdapterError) as error:
                # A native write failure does not prove non-delivery.
                disposition = (
                    CommandDisposition.refused
                    if type(error).__name__
                    in ("BackendSpawnFailed", "SessionLoadFailed", "NeedsRebind")
                    else CommandDisposition.uncertain
                )
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    disposition,
                    {"error_type": type(error).__name__, "error_message": str(error)},
                )
            except Exception as error:
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.uncertain,
                    {"error_type": type(error).__name__, "error_message": str(error)},
                )
        self.outcomes[command.command_id] = outcome
        await self._publish_outcome(outcome)
        return outcome

    async def _publish_outcome(self, outcome: CommandOutcome) -> None:
        submission_id = "outcome_" + hashlib.sha256(outcome.command_id.encode()).hexdigest()
        await self._emit(
            outcome.child.conversation_id,
            EnvelopeKind.command_outcome,
            outcome,
            submission_id=submission_id,
        )
        self._outcomes_published.add(outcome.command_id)

    async def _emit(
        self,
        conversation_id: str,
        kind: EnvelopeKind,
        value: object,
        snapshots: tuple[FileSnapshot, ...] = (),
        submission_id: str | None = None,
    ) -> None:
        payload = cast(JsonObject, encode_value(value))
        await asyncio.to_thread(
            self.spool.mint_envelope,
            conversation_id=conversation_id,
            kind=kind,
            payload=payload,
            snapshots=snapshots,
            submission_id=submission_id,
        )

    async def report_event(
        self,
        reference: ChildReference,
        method: BackendEventMethod,
        turn: TurnToken | None,
        arguments: dict[str, object],
    ) -> None:
        await self.source_work(
            reference, lambda: self._report_event(reference, method, turn, arguments)
        )

    async def _report_event(
        self,
        reference: ChildReference,
        method: BackendEventMethod,
        turn: TurnToken | None,
        arguments: dict[str, object],
    ) -> None:
        child = self.children.get(reference.child_id)
        if child is None:
            raise ValueError("event from unknown child")
        if method == BackendEventMethod.permission_ask_raised:
            ask = cast(BackendPermissionAsk, arguments["ask"])
            if turn is None:
                raise ValueError("permission ask lacks turn")
            child.asks[ask.ask_id] = AskReference(reference, turn, ask.ask_id)
        elif method == BackendEventMethod.user_input_requested:
            request = cast(BackendUserInputRequest, arguments["request"])
            if turn is None:
                raise ValueError("user input request lacks turn")
            request_reference = AskReference(
                reference, turn, request.request_id, AskKind.user_input
            )
            previous = child.user_inputs.get(request.request_id)
            pending = (request_reference, request)
            if previous is not None and previous != pending:
                raise ValueError("user input request identity changed")
            child.user_inputs[request.request_id] = pending
        elif method == BackendEventMethod.turn_ended:
            if child.active_turn == turn:
                child.active_turn = None
                child.asks.clear()
                child.user_inputs.clear()
        event = BackendEvent(reference, method, turn, encode_backend_event(method, turn, arguments))
        snapshots: tuple[FileSnapshot, ...] = ()
        if (
            self.paths is not None
            and child.ticket_id is not None
            and method
            in (
                BackendEventMethod.agent_message_completed,
                BackendEventMethod.tool_call_finished,
                BackendEventMethod.turn_ended,
            )
        ):
            snapshots = await asyncio.to_thread(snapshot_ticket, self.paths, child.ticket_id)
        await self._emit(reference.conversation_id, EnvelopeKind.backend_event, event, snapshots)

    def _native_answer_command(self, answer: DurableAnswer) -> ChildCommand:
        if answer.ask.kind == AskKind.permission:
            if answer.option_id is None or answer.user_input_answers is not None:
                raise ValueError("permission answer needs exactly its option")
            operation = ChildOperation.answer_permission_ask
            arguments: JsonObject = {"ask_id": answer.ask.ask_id, "option_id": answer.option_id}
        else:
            if answer.option_id is not None or answer.user_input_answers is None:
                raise ValueError("user input answer needs exactly its complete map")
            operation = ChildOperation.answer_user_input
            arguments = {
                "request_id": answer.ask.ask_id,
                "answers": encode_value(answer.user_input_answers),
            }
        return ChildCommand(
            answer.answer_id, answer.ask.child, operation, answer.ask.turn, arguments
        )

    async def apply_answer(self, answer: DurableAnswer) -> AnswerApplication | None:
        command = self._native_answer_command(answer)
        previous_answer = self._answers.get(answer.answer_id)
        if previous_answer is not None and previous_answer != answer:
            raise ValueError("answer id reused with different recorded answer")
        if answer.answer_id in self._applications:
            application = self._applications[answer.answer_id]
            if application.ask != answer.ask or self._commands.get(answer.answer_id) != command:
                raise ValueError("answer id reused with another callback or map")
            await self._publish_application(application)
            return application
        child = self.children.get(answer.ask.child.child_id)
        if child is None or child.reference != answer.ask.child:
            return None
        if answer.ask.kind == AskKind.permission:
            if child.asks.get(answer.ask.ask_id) != answer.ask:
                return None
        else:
            pending = child.user_inputs.get(answer.ask.ask_id)
            if pending is None or pending[0] != answer.ask:
                return None
            assert answer.user_input_answers is not None  # shape checked above
            normalized = ordered_user_input_answers(pending[1].questions, answer.user_input_answers)
            if normalized is None or normalized != answer.user_input_answers:
                raise ValueError("recorded user input map is incomplete or not canonical")
        self._answers[answer.answer_id] = answer
        outcome = await self.execute_command(command)
        if outcome.disposition != CommandDisposition.succeeded:
            return None
        retained_application = self._applications.get(answer.answer_id)
        if retained_application is None:
            raise ValueError("successful answer lacks retained application evidence")
        await self._publish_application(retained_application)
        return retained_application

    async def _publish_application(self, application: AnswerApplication) -> None:
        if application.answer_id in self._applications_published:
            return
        submission_id = "application_" + hashlib.sha256(application.answer_id.encode()).hexdigest()
        await self._emit(
            application.ask.child.conversation_id,
            EnvelopeKind.answer_application,
            application,
            submission_id=submission_id,
        )
        self._applications_published.add(application.answer_id)

    async def report_authorized_action(self, action: AuthorizedAction) -> None:
        application = self._applications.get(action.answer_id)
        if application is None or application.ask != action.ask:
            raise ValueError("tool action has no exact applied answer correlation")
        await self.source_work(
            action.ask.child,
            lambda: self._emit(
                action.ask.child.conversation_id, EnvelopeKind.authorized_action, action
            ),
        )

    def inventory(
        self, identity: ExecutorIdentity, *, include_pending: bool = True
    ) -> ReconciliationInventory:
        return ReconciliationInventory(
            identity,
            tuple(
                ChildInventory(
                    child.reference,
                    child.active_turn,
                    tuple(child.asks.values()),
                    tuple(sorted(child.user_inputs)),
                )
                for child in self.children.values()
            ),
            self.spool.pending() if include_pending else (),
            tuple(
                outcome
                for command_id, outcome in self.outcomes.items()
                if command_id not in self._confirmed_command_ids
            ),
        )

    def confirm_command(self, command_id: str) -> None:
        # Retain only ID->command identity so duplicate IDs cannot cause another native write.
        # The VPS is authoritative for accepted results; commands are not auto-replayed.
        self._confirmed_command_ids.add(command_id)
        self._outcomes_published.add(command_id)
        outcome = self.outcomes.get(command_id)
        if outcome is not None and "answer_application" in outcome.result:
            application = TypeAdapter(AnswerApplication).validate_python(
                outcome.result["answer_application"]
            )
            self._applications_published.add(application.answer_id)
        self._tasks.pop(command_id, None)

    def _quiescence_counts(self) -> tuple[int, int, int, int]:
        active = sum(child.active_turn is not None for child in self.children.values())
        callbacks = sum(
            len(child.asks) + len(child.user_inputs) for child in self.children.values()
        )
        # Native calls with no confirmed result also prevent replacement. This includes
        # start before its child has a turn, and outcome publication still in progress.
        unresolved = sum(not task.done() for task in self._tasks.values()) + sum(
            outcome.disposition == CommandDisposition.uncertain
            for outcome in self.outcomes.values()
        )
        unresolved += sum(
            command_id not in self._outcomes_published for command_id in self.outcomes
        )
        unresolved += sum(
            answer_id not in self._applications_published for answer_id in self._applications
        )
        unresolved += sum(self._source_work.values())
        return active, callbacks, unresolved, self.spool.pending_count()

    async def quiesce(self) -> Quiescence:
        # Admission closes synchronously before any awaited native operation.
        self.admission_stopped = True
        counts = self._quiescence_counts()
        stopped = False
        if not any(counts[:3]):
            for child in tuple(self.children.values()):
                async with child.lock:
                    if any(self._quiescence_counts()[:3]):
                        break
                    await child.native.stop()
                    self.children.pop(child.reference.child_id, None)
            # stop can emit events or expose a pending operation. Check the journal and
            # all live blockers again after the awaited boundary before granting exit.
            counts = self._quiescence_counts()
            stopped = not self.children and not any(counts)
        return Quiescence(True, *counts, stopped)
