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
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from planner.conversation.backends.codex_app_server.login import codex_home
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    BackendAdapterError,
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendUserInputRequest,
    NeedsRebind,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    MoveRetirementDisposition,
    MoveRetirementResult,
    ResolvedConversationStart,
)
from planner.conversation.logic.user_input import ordered_user_input_answers
from planner.conversation.message_files import ConversationMessageFiles
from planner.environments.hermes_home import resolve_planner_home
from planner.executor.codec import encode_backend_event, encode_value, invoke_child
from planner.executor.configuration import NATIVE_SKILLS_DIR_ENV, ExecutorPaths
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
from planner.executor.workspace import localize_workspace


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
    launch_context: object | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    backend_key: ConversationBackendKey | None = None
    resolved_model_id: str | None = None


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
        admission_observer: object | None = None,
    ) -> None:
        self.workspace_home = workspace_home or Path.home()
        self.admission_observer = admission_observer
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
        self._source_work: dict[ChildReference, int] = {}
        self._source_tasks: dict[ChildReference, set[asyncio.Task[object]]] = {}
        self._failed_source_work: set[ChildReference] = set()
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

    def begin_source_work(self, child: ChildReference, *, final_event: bool = False) -> None:
        native = self.children.get(child.child_id)
        if (
            child.child_id in self._retired_children
            or (child.child_id in self._retiring_children and not final_event)
            or (native is not None and native.ticket_id in self._retiring_tickets)
        ):
            raise PermissionError("source child is retired or retiring")
        key = child
        self._source_work[key] = self._source_work.get(key, 0) + 1

    async def source_work[T](
        self,
        child: ChildReference,
        work: Callable[[], Awaitable[T]],
        *,
        new_work: bool = False,
        final_event: bool = False,
    ) -> T:
        # Existing native work can still publish its final events after admission closes.
        if new_work and self.admission_stopped:
            raise PermissionError("executor admission stopped")
        self.begin_source_work(child, final_event=final_event)
        task = asyncio.ensure_future(work())
        tasks = self._source_tasks.setdefault(child, set())
        tasks.add(cast(asyncio.Task[object], task))
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
            failed = task.cancelled() or task.exception() is not None
            if final_event and failed:
                self._failed_source_work.add(child)
            raise
        except Exception:
            if final_event:
                self._failed_source_work.add(child)
            raise
        finally:
            tasks.discard(cast(asyncio.Task[object], task))
            if not tasks:
                self._source_tasks.pop(child, None)
            self.end_source_work(child)

    def end_source_work(self, child: ChildReference) -> None:
        key = child
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
                or any(
                    count
                    for ref, count in self._source_work.items()
                    if ref.conversation_id in conversations
                )
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
            if any(
                count
                for ref, count in self._source_work.items()
                if ref.conversation_id in conversations
            ) or any(self.spool.has_pending(conv) for conv in conversations):
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
            relative = arguments.get("workspace_home_relative")
            if "workspace_home_relative" in arguments and not isinstance(relative, str):
                raise ValueError("unsafe home-relative workspace")
            workspace = localize_workspace(
                resolved.workspace_folder,
                workspace_home=self.workspace_home,
                workspace_home_relative=relative if isinstance(relative, str) else None,
            )
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
            NATIVE_SKILLS_DIR_ENV,
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
        return replace(
            resolved,
            workspace_folder=workspace,
            role_materials=ConversationRoleMaterials(role_text, tuple(identity.items())),
        )

    def _reference_conflicts(self, reference: ChildReference, command_id: str) -> bool:
        child = self.children.get(reference.child_id)
        if child is not None and child.reference != reference:
            return True
        return any(
            other.child != reference
            for other in self._commands.values()
            if other.child.child_id == reference.child_id
            and other.command_id != command_id
            and other.child.executor_incarnation == self.incarnation
            and (
                (retained_outcome := self.outcomes.get(other.command_id)) is None
                or retained_outcome.result.get("reason")
                not in ("incarnation_gone", "child_reference_conflict")
            )
        )

    async def _stop_exact_child(self, command: ChildCommand) -> CommandOutcome:
        reference = command.child

        def outcome(disposition: CommandDisposition, reason: str) -> CommandOutcome:
            return CommandOutcome(command.command_id, reference, disposition, {"reason": reason})

        child = self.children.get(reference.child_id)
        if reference.executor_incarnation != self.incarnation:
            return outcome(CommandDisposition.refused, "incarnation_gone")
        if self._reference_conflicts(reference, command.command_id):
            return outcome(CommandDisposition.refused, "child_reference_conflict")
        current = asyncio.current_task()
        stop_owner = next(
            (
                task
                for key, task in self._tasks.items()
                if not task.done()
                and self._commands[key].child == reference
                and self._commands[key].operation == ChildOperation.stop
            ),
            current,
        )
        if stop_owner is not current:
            # Never join another stop: two owners otherwise wait on one another.
            return outcome(CommandDisposition.refused, "child_stop_in_progress")
        if child is not None and child.ticket_id in self._retiring_tickets:
            return outcome(CommandDisposition.refused, "source_retirement_in_progress")

        # Admission closes before the first await. Failed attempts retain this fence,
        # but a later explicit stop can retry once its previous owner finished.
        self._retiring_children.add(reference.child_id)
        commands = [
            task
            for key, task in self._tasks.items()
            if task is not current
            and self._commands[key].child == reference
            and self._commands[key].operation != ChildOperation.stop
            and not task.done()
        ]
        if child is None:
            unsettled = (
                bool(commands)
                or bool(self._source_work.get(reference, 0))
                or reference in self._failed_source_work
                or any(
                    other.child == reference and key not in self._outcomes_published
                    for key, other in self.outcomes.items()
                )
                or any(
                    application.ask.child == reference and key not in self._applications_published
                    for key, application in self._applications.items()
                )
                or self.spool.has_pending(reference.conversation_id)
            )
            if unsettled:
                return outcome(CommandDisposition.uncertain, "child_has_unsettled_work")
        else:
            try:
                # Native-lock release alone is insufficient: wrappers can still mint
                # command outcomes and callback applications after that release.
                if commands:
                    await asyncio.gather(*(asyncio.shield(task) for task in commands))
                await self._finish_source_work(reference)
                async with child.lock:
                    await child.native.stop()
                await self._finish_source_work(reference)
                if (
                    reference in self._failed_source_work
                    or any(
                        other.child == reference and key not in self._outcomes_published
                        for key, other in self.outcomes.items()
                    )
                    or any(
                        application.ask.child == reference
                        and key not in self._applications_published
                        for key, application in self._applications.items()
                    )
                ):
                    raise RuntimeError("required source publication failed")
            except Exception as error:
                return CommandOutcome(
                    command.command_id,
                    reference,
                    CommandDisposition.uncertain,
                    {"error_type": type(error).__name__, "error_message": str(error)},
                )
        self._retired_children.add(reference.child_id)
        self._retiring_children.discard(reference.child_id)
        if child is not None and self.children.get(reference.child_id) is child:
            self.children.pop(reference.child_id)
        return CommandOutcome(command.command_id, reference, CommandDisposition.succeeded, {})

    async def _finish_source_work(self, reference: ChildReference) -> None:
        while tasks := tuple(self._source_tasks.get(reference, ())):
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))
        # The source_work owner removes its count in its finally block. Its task
        # result can wake this waiter first, so let that owner finish accounting.
        while self._source_work.get(reference, 0):
            await asyncio.sleep(0)

    async def _invoke_admitted(self, child: _Child, command: ChildCommand) -> JsonObject:
        previous_turn = child.active_turn
        if command.operation is ChildOperation.write_prompt:
            child.active_turn = command.turn
        try:
            return await invoke_child(child.native, command)
        except AdmissionRefused:
            # A refused final writer emitted no user bytes and owns no new turn.
            child.active_turn = previous_turn
            raise

    async def _execute(self, command: ChildCommand) -> CommandOutcome:
        if command.child.executor_incarnation != self.incarnation:
            outcome = CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.refused,
                {"reason": "incarnation_gone"},
            )
        elif self._reference_conflicts(command.child, command.command_id):
            outcome = CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.refused,
                {"reason": "child_reference_conflict"},
            )
        elif command.operation == ChildOperation.stop:
            outcome = await self._stop_exact_child(command)
        elif (
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
                expectation = None
                if command.operation in (
                    ChildOperation.start,
                    ChildOperation.write_prompt,
                    ChildOperation.steer,
                ):
                    from planner.conversation.admission.service import parse_expectation

                    raw_expectation = command.arguments.get("admission_expectation")
                    if raw_expectation is not None:
                        try:
                            expectation = parse_expectation(raw_expectation)
                        except ValueError as malformed:
                            raise AdmissionRefused("invalid admission expectation") from malformed
                        if expectation.child != command.child or expectation.turn != command.turn:
                            raise AdmissionRefused("model verification command identity changed")
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
                    if (
                        ticket_id is not None
                        and resolved.backend_key is not ConversationBackendKey.hermes
                        and expectation is None
                    ):
                        raise AdmissionRefused(
                            "model verification legacy_unscoped",
                            reason="legacy_unscoped",
                            corrective_action="classify_selection",
                        )
                    if expectation is not None and (
                        expectation.conversation_id != resolved.conversation_id
                        or expectation.selection.backend_key != resolved.backend_key
                        or expectation.launch_token != resolved.model
                        or expectation.reasoning_effort != resolved.reasoning_effort
                        or expectation.workspace.workspace_folder != resolved.workspace_folder
                        or expectation.workspace.workspace_path_policy
                        != resolved.workspace_path_policy
                        or expectation.workspace.workspace_home_relative
                        != command.arguments.get("workspace_home_relative")
                    ):
                        raise AdmissionRefused("model verification start parameters changed")
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
                        if resolved.backend_key is ConversationBackendKey.codex:
                            native_home = codex_home(os.environ)
                        elif resolved.backend_key is ConversationBackendKey.hermes:
                            native_home = resolve_planner_home()
                        else:
                            configured_home = os.environ.get("CLAUDE_CONFIG_DIR", "")
                            native_home = (
                                Path(configured_home).expanduser()
                                if configured_home
                                else Path.home() / ".claude"
                            )
                        generation = install_native_skill_snapshot(
                            snapshot, paths=self.paths, native_home=native_home
                        )
                        assert resolved.role_materials is not None
                        identity = dict(resolved.role_materials.identity_environment_variables)
                        identity[NATIVE_SKILLS_DIR_ENV] = str(generation / "skills")
                        resolved = replace(
                            resolved,
                            role_materials=replace(
                                resolved.role_materials,
                                identity_environment_variables=tuple(identity.items()),
                            ),
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
                        command.child,
                        capability,
                        native,
                        ticket_id=identity.get("PLAN_TICKET_ID"),
                        launch_context=None if expectation is None else expectation.context,
                        model=resolved.model,
                        reasoning_effort=resolved.reasoning_effort,
                        backend_key=resolved.backend_key,
                        resolved_model_id=None
                        if expectation is None
                        else expectation.resolved_model_id,
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
                        if (
                            command.child.child_id in self._retired_children
                            or command.child.child_id in self._retiring_children
                        ):
                            raise _CommandAdmissionRefused("source child identity is retiring")
                        if command.operation in (ChildOperation.write_prompt, ChildOperation.steer):
                            if self.admission_stopped:
                                raise _CommandAdmissionRefused("executor is quiescing")
                        if command.operation in (
                            ChildOperation.start,
                            ChildOperation.write_prompt,
                            ChildOperation.steer,
                        ) and (
                            (
                                child.ticket_id is not None
                                and child.backend_key is not ConversationBackendKey.hermes
                            )
                            or expectation is not None
                        ):
                            from planner.conversation.admission.service import (
                                AdmissionContextObserver,
                                admission_scope,
                            )

                            if expectation is None:
                                raise AdmissionRefused(
                                    "model verification legacy_unscoped",
                                    reason="legacy_unscoped",
                                    corrective_action="classify_selection",
                                )
                            if child.launch_context != expectation.context:
                                raise AdmissionRefused(
                                    "model verification retained process context changed"
                                )
                            if (
                                command.operation
                                in (ChildOperation.write_prompt, ChildOperation.steer)
                                and child.backend_key is ConversationBackendKey.claude
                                and child.model == expectation.launch_token
                                and child.resolved_model_id != expectation.resolved_model_id
                            ):
                                raise NeedsRebind(
                                    "the provider alias target changed before admission"
                                )
                            if command.operation in (
                                ChildOperation.write_prompt,
                                ChildOperation.steer,
                            ) and (
                                (
                                    child.model
                                    if command.arguments.get("model_change") is None
                                    else command.arguments["model_change"]
                                )
                                != expectation.launch_token
                                or (
                                    child.reasoning_effort
                                    if command.arguments.get("reasoning_effort_change") is None
                                    else command.arguments["reasoning_effort_change"]
                                )
                                != expectation.reasoning_effort
                            ):
                                raise AdmissionRefused(
                                    "model verification prompt parameters changed"
                                )
                            observer = cast(
                                AdmissionContextObserver | None, self.admission_observer
                            )
                            with admission_scope(expectation, observer):
                                if observer is None or not observer.verify_context(
                                    expectation.context
                                ):
                                    raise AdmissionRefused("model verification context_changed")
                                result = await self._invoke_admitted(child, native_command)
                                if command.operation == ChildOperation.write_prompt:
                                    child.model = expectation.launch_token
                                    child.reasoning_effort = expectation.reasoning_effort
                                    child.resolved_model_id = expectation.resolved_model_id
                        else:
                            result = await self._invoke_admitted(child, native_command)
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
                        outcome = CommandOutcome(
                            command.command_id, command.child, CommandDisposition.succeeded, result
                        )
            except (_CommandAdmissionRefused, AdmissionRefused) as error:
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.refused,
                    {
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                        **(
                            {"reason": error.reason, "corrective_action": error.corrective_action}
                            if isinstance(error, AdmissionRefused)
                            else {}
                        ),
                    },
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
            reference,
            lambda: self._report_event(reference, method, turn, arguments),
            final_event=True,
        )

    async def _report_event(
        self,
        reference: ChildReference,
        method: BackendEventMethod,
        turn: TurnToken | None,
        arguments: dict[str, object],
    ) -> None:
        child = self.children.get(reference.child_id)
        if child is None or child.reference != reference:
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
        return await self.source_work(answer.ask.child, lambda: self._apply_answer(answer))

    async def _apply_answer(self, answer: DurableAnswer) -> AnswerApplication | None:
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
            final_event=True,
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
        task = self._tasks.get(command_id)
        if task is None:
            return

        def release_finished_wrapper(finished: asyncio.Task[CommandOutcome]) -> None:
            # ACKs can arrive from inventory before the local publication returns.
            # Keep that wrapper visible to stop and quiescence until it finishes.
            if self._tasks.get(command_id) is finished:
                self._tasks.pop(command_id)

        if task.done():
            release_finished_wrapper(task)
        else:
            task.add_done_callback(release_finished_wrapper)

    def _quiescence_counts(self) -> tuple[int, int, int, int]:
        active = sum(child.active_turn is not None for child in self.children.values())
        callbacks = sum(
            len(child.asks) + len(child.user_inputs) for child in self.children.values()
        )
        stopped_references = {
            command.child
            for command_id, command in self._commands.items()
            if command.operation == ChildOperation.stop
            and (outcome := self.outcomes.get(command_id)) is not None
            and outcome.disposition == CommandDisposition.succeeded
            and outcome.child == command.child
        }
        live_references = {child.reference for child in self.children.values()}
        # Acknowledged uncertainty stays historical only after its exact child stopped.
        # This permits maintenance exit without resolving unknown native effects.
        # Native calls with no confirmed result also prevent replacement. This includes
        # start before its child has a turn, and outcome publication still in progress.
        unresolved = sum(not task.done() for task in self._tasks.values()) + sum(
            outcome.disposition == CommandDisposition.uncertain
            and not (
                command_id in self._confirmed_command_ids
                and outcome.child in stopped_references
                and outcome.child not in live_references
            )
            for command_id, outcome in self.outcomes.items()
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
