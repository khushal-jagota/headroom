"""Native children and callbacks live here, independently of every connection."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast
from uuid import uuid4

from planner.conversation.backends.claude_agent_sdk import ClaudeAgentSdkBackendChildFactory
from planner.conversation.backends.codex_app_server import CodexAppServerBackendChildFactory
from planner.conversation.backends.codex_app_server.login import codex_home
from planner.conversation.backends.contracts import (
    BackendAdapterError,
    BackendChild,
    BackendChildFactory,
    BackendPermissionAsk,
    BackendPromptRejected,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerRefused,
    BackendUserInputRequest,
    NeedsRebind,
    PromptWriteRefused,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.backends.hermes_acp import HermesAcpBackendChildFactory
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding, PlanEntry, ToolCallStatus
from planner.conversation.logic.addressed_reply import with_runtime_prompt_context
from planner.conversation.logic.user_input import ordered_user_input_answers
from planner.conversation.message_content import MessageContent, MessageFile
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.production_backends import production_backend_launches
from planner.environments.hermes_home import resolve_planner_home
from planner.execution_identity import (
    AskKind,
    AskReference,
    ChildReference,
)
from planner.executor.configuration import (
    MAX_TOOL_DETAIL_CHARACTERS,
    NATIVE_SKILLS_DIR_ENV,
    NATIVE_WORKER_CONTEXT,
    ExecutorPaths,
)
from planner.executor.files import (
    contained_path,
    copy_verified,
    directory_descriptor,
    safe_component,
)
from planner.executor.link import Link
from planner.executor.link_files import get_file, settled_io
from planner.executor.protocol import (
    LIVE_BACKEND_EVENT_METHODS,
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
    MacOutboxWriter,
    OutboxEntry,
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
from planner.executor.skills import install_native_skill_snapshot, validate_skill_snapshot
from planner.workspace_localization import localize_workspace


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class _CommandAdmissionRefused(Exception):
    """An admission guard proved that the native command was not dispatched."""


@dataclass
class RunningChild:
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
    launch_input: StartRun | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    backend_key: ConversationBackendKey | None = None
    localized_start: ResolvedConversationStart | None = None


class NativeEventSink:
    def __init__(self, registry: MacRunner, reference: ChildReference) -> None:
        self.registry = registry
        self.reference = reference

    async def _report(
        self, method: BackendEventMethod, turn: TurnToken | None, arguments: dict[str, object]
    ) -> None:
        if isinstance(detail := arguments.get("detail"), str):
            arguments["detail"] = detail[:MAX_TOOL_DETAIL_CHARACTERS]
        if method in LIVE_BACKEND_EVENT_METHODS:
            if self.registry.send_live_event is not None:
                await self.registry.send_live_event(
                    BackendEvent(
                        self.reference, method, turn, encode_backend_event(method, turn, arguments)
                    )
                )
            return
        await self.registry.report_event(self.reference, method, turn, arguments)


    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        await self._report(
            BackendEventMethod.agent_message_delta, turn_token, {"text_delta": text_delta}
        )

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        await self._report(BackendEventMethod.model_thinking_happened, turn_token, {})

    async def agent_message_completed(self, turn_token: TurnToken, content: MessageContent) -> None:
        await self._report(
            BackendEventMethod.agent_message_completed, turn_token, {"content": content}
        )

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        await self._report(
            BackendEventMethod.tool_call_started,
            turn_token,
            {
                "tool_call_id": tool_call_id,
                "title": title,
                "tool_kind": tool_kind,
                "detail": detail,
            },
        )

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        await self._report(
            BackendEventMethod.tool_call_progress,
            turn_token,
            {"tool_call_id": tool_call_id, "detail": detail},
        )

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        await self._report(
            BackendEventMethod.tool_call_finished,
            turn_token,
            {"tool_call_id": tool_call_id, "tool_call_status": tool_call_status, "detail": detail},
        )

    async def plan_updated(self, turn_token: TurnToken, entries: tuple[PlanEntry, ...]) -> None:
        await self._report(BackendEventMethod.plan_updated, turn_token, {"entries": entries})

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        await self._report(
            BackendEventMethod.token_usage_reported,
            turn_token,
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cost_usd": cost_usd,
            },
        )

    async def context_compacted(self, turn_token: TurnToken) -> None:
        await self._report(BackendEventMethod.context_compacted, turn_token, {})

    async def permission_ask_raised(self, turn_token: TurnToken, ask: BackendPermissionAsk) -> None:
        await self._report(BackendEventMethod.permission_ask_raised, turn_token, {"ask": ask})

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        await self._report(
            BackendEventMethod.user_input_requested, turn_token, {"request": request}
        )

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        await self._report(
            BackendEventMethod.user_input_failed,
            turn_token,
            {"request_id": request_id, "detail": detail},
        )

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        await self._report(
            BackendEventMethod.turn_ended,
            turn_token,
            {
                "ending": ending,
                "error_summary": error_summary,
                "standard_error_tail": standard_error_tail,
            },
        )

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        await self._report(
            BackendEventMethod.vendor_session_cursor_rebound,
            None,
            {"vendor_session_cursor": vendor_session_cursor},
        )

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        await self._report(
            BackendEventMethod.composer_catalog_reported,
            None,
            {"composer_catalog": composer_catalog},
        )


@dataclass
class _CommandRecord:
    command: ChildCommand
    task: asyncio.Task[CommandOutcome] | None = None
    preparation: asyncio.Task[None] | None = None
    outcome: CommandOutcome | None = None
    invoking: bool = False


class MacRunner:
    def __init__(
        self,
        *,
        incarnation: str,
        spool: MacOutboxWriter,
        factory: Callable[[ResolvedConversationStart, str, ChildReference], BackendChildFactory]
        | None = None,
        panels_server_url: str = "",
        connection: Callable[[], Link | None] = lambda: None,
        ready: Callable[[], bool] = lambda: False,
        message_files: ConversationMessageFiles,
        paths: ExecutorPaths | None = None,
        workspace_home: Path | None = None,
    ) -> None:
        self.workspace_home = workspace_home or Path.home()
        self.paths = paths
        self.incarnation = incarnation
        self.spool = spool
        self.send_live_event: Callable[[BackendEvent], Awaitable[None]] | None = (
            self._send_live_event
        )
        self.connection = connection
        self.ready = ready
        self._transfers = 0
        self.factory = factory or self._factory(panels_server_url)
        self.message_files = message_files
        self._children: dict[str, RunningChild] = {}
        self.prepare_inputs: Callable[[ChildCommand], Awaitable[None]] | None = self._prepare_inputs
        self._withdrawn_commands: set[str] = set()
        self._commands: dict[str, _CommandRecord] = {}
        self._applications: dict[str, AnswerApplication] = {}
        self._answers: dict[str, DurableAnswer] = {}
        self._stop_proofs_published: set[str] = set()
        self._applications_published: set[str] = set()
        self.admission_stopped = False
        self._retired_children: set[str] = set()
        self._retiring_children: set[str] = set()
        self._source_work: dict[ChildReference, int] = {}
        self._source_idle: dict[ChildReference, asyncio.Event] = {}
        self._source_tasks: dict[ChildReference, set[asyncio.Task[object]]] = {}
        self._failed_source_work: set[ChildReference] = set()

    def authenticate(self, child_id: str | None, capability: str | None) -> RunningChild:
        child = self._children.get(child_id or "")
        if (
            child is None
            or child_id in self._retired_children
            or child_id in self._retiring_children
            or capability is None
            or not secrets.compare_digest(child.capability, capability)
        ):
            raise PermissionError("unknown executor child capability")
        return child

    def begin_source_work(self, child: ChildReference, *, final_event: bool = False) -> None:
        if child.child_id in self._retired_children or (
            child.child_id in self._retiring_children and not final_event
        ):
            raise PermissionError("source child is retired or retiring")
        key = child
        if key not in self._source_work:
            self._source_idle[key] = asyncio.Event()
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

    def record_lost_output(self, child: ChildReference) -> None:
        """An authored write failed to enter the durable outbox; stop stays unproved."""
        self._failed_source_work.add(child)

    def end_source_work(self, child: ChildReference) -> None:
        key = child
        self._source_work[key] -= 1
        if not self._source_work[key]:
            del self._source_work[key]
            self._source_idle.pop(key).set()

    def _factory(
        self, server_url: str
    ) -> Callable[[ResolvedConversationStart, str, ChildReference], BackendChildFactory]:
        def factory(
            resolved: ResolvedConversationStart, capability: str, reference: ChildReference
        ) -> BackendChildFactory:
            launches = production_backend_launches(panels_server_url=server_url)
            identity = dict(
                resolved.role_materials.identity_environment_variables
                if resolved.role_materials is not None
                else ()
            )
            ticket_id = identity.get("PLAN_TICKET_ID", resolved.conversation_id)
            assert self.paths is not None
            artifact_root = self.paths.artifacts / safe_component(ticket_id)
            artifact_root.mkdir(parents=True, exist_ok=True)
            # These override transport facts only. HOME/provider state remain inherited.
            overrides = (
                ("PLAN_EXECUTOR_ROOT", str(self.paths.root)),
                ("PLAN_EXECUTOR_CHILD_ID", reference.child_id),
                ("PLAN_EXECUTOR_CAPABILITY", capability),
                ("PLAN_TICKET_FILES_DIR", str(artifact_root)),
            )
            if resolved.backend_key == ConversationBackendKey.codex:
                return CodexAppServerBackendChildFactory(
                    replace(
                        launches.codex,
                        environment_overrides=launches.codex.environment_overrides + overrides,
                    )
                )
            if resolved.backend_key == ConversationBackendKey.hermes:
                return HermesAcpBackendChildFactory(
                    replace(
                        launches.hermes,
                        environment_overrides=launches.hermes.environment_overrides + overrides,
                    )
                )
            return ClaudeAgentSdkBackendChildFactory(
                replace(
                    launches.claude,
                    environment_overrides=launches.claude.environment_overrides + overrides,
                ),
            )

        return factory

    async def _send_live_event(self, event: BackendEvent) -> None:
        link = self.connection()
        if link is not None and self.ready():
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
                    await self.get_file(command, piece.stored_file_id)

    async def get_file(self, command: ChildCommand, stored_file_id: str) -> None:
        link = self.connection()
        if link is None:
            raise ConnectionError("executor link unavailable")
        self._transfers += 1
        assert self.paths is not None
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
                root = self.paths.root / "artifacts" / "files" / "conversations"
                target = contained_path(
                    root, f"{safe_component(manifest.owner_id)}/{manifest.relative_path}"
                )
                self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
                await get_file(link, slot, temporary)
                if link is not self.connection():
                    raise ConnectionError("input generation changed")

                def install() -> None:
                    with directory_descriptor(target.parent, create=True) as parent:
                        staged = target.parent / (".tmp-" + uuid4().hex)
                        try:
                            copy_verified(temporary, staged, manifest)
                            os.replace(
                                staged.name, target.name, src_dir_fd=parent, dst_dir_fd=parent
                            )
                            os.fsync(parent)
                        finally:
                            staged.unlink(missing_ok=True)

                await settled_io(install)
        finally:
            temporary.unlink(missing_ok=True)
            self._transfers -= 1

    async def run(self, command: ChildCommand) -> CommandOutcome:
        record = self._commands.get(command.command_id)
        if record is not None and record.command != command:
            raise ValueError("command id reused with different content")
        if record is None:
            record = _CommandRecord(command)
            self._commands[command.command_id] = record
        if record.outcome is not None:
            await self._publish_required_output(command, record.outcome)
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
                await self._publish_required_output(record.command, record.outcome)

    def ask(self, command_ids: tuple[str, ...]) -> tuple[CommandState, ...]:
        states: list[CommandState] = []
        for command_id in command_ids:
            record = self._commands.get(command_id)
            states.append(
                CommandState(
                    command_id,
                    "unknown"
                    if record is None
                    else "finished"
                    if record.outcome is not None
                    else "invoking"
                    if record.invoking
                    else "preparing",
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
                tuple(sorted(child.user_inputs)),
            )
            for child in self._children.values()
        )

    def _localize_start(
        self,
        resolved: ResolvedConversationStart,
        reference: ChildReference,
        capability: str,
        workspace_home_relative: str | None,
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
            assert self.paths is not None
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
        child = self._children.get(reference.child_id)
        if child is not None and child.reference != reference:
            return True
        return any(
            record.command.child != reference
            for record in self._commands.values()
            if record.command.child.child_id == reference.child_id
            and record.command.command_id != command_id
            and record.command.child.executor_incarnation == self.incarnation
            and (
                record.outcome is None
                or record.outcome.result.get("reason")
                not in ("incarnation_gone", "child_reference_conflict")
            )
        )

    async def _stop_exact_child(self, command: ChildCommand) -> CommandOutcome:
        reference = command.child

        def outcome(disposition: CommandDisposition, reason: str) -> CommandOutcome:
            return CommandOutcome(command.command_id, reference, disposition, {"reason": reason})

        child = self._children.get(reference.child_id)
        if reference.executor_incarnation != self.incarnation:
            return outcome(CommandDisposition.refused, "incarnation_gone")
        if self._reference_conflicts(reference, command.command_id):
            return outcome(CommandDisposition.refused, "child_reference_conflict")
        current = asyncio.current_task()
        stop_owner = next(
            (
                record.task
                for record in self._commands.values()
                if record.task is not None
                and not record.task.done()
                and record.command.child == reference
                and record.command.operation == ChildOperation.stop
            ),
            current,
        )
        if stop_owner is not current:
            # Never join another stop: two owners otherwise wait on one another.
            return outcome(CommandDisposition.refused, "child_stop_in_progress")

        # Admission closes before the first await. Failed attempts retain this fence,
        # but a later explicit stop can retry once its previous owner finished.
        self._retiring_children.add(reference.child_id)
        preparing = {key for key, row in self._commands.items() if row.preparation is not None}
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
            and record.command.operation != ChildOperation.stop
            and not record.task.done()
        ]
        if child is None:
            settling = [
                record.task
                for key in preparing
                if (record := self._commands[key]).command.child == reference
                and record.task is not None
            ]
            if settling:
                await asyncio.gather(*(asyncio.shield(task) for task in settling))
                commands = [task for task in commands if not task.done()]
            unsettled = (
                bool(commands)
                or bool(self._source_work.get(reference, 0))
                or reference in self._failed_source_work
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
                    self._mark_invoking(command)
                    await child.native.stop()
                await self._finish_source_work(reference)
                if reference in self._failed_source_work or any(
                    application.ask.child == reference and key not in self._applications_published
                    for key, application in self._applications.items()
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
        if child is not None and self._children.get(reference.child_id) is child:
            self._children.pop(reference.child_id)
        return CommandOutcome(command.command_id, reference, CommandDisposition.succeeded, {})

    async def _finish_source_work(self, reference: ChildReference) -> None:
        while idle := self._source_idle.get(reference):
            tasks = tuple(self._source_tasks.get(reference, ()))
            if tasks:
                await asyncio.gather(*(asyncio.shield(task) for task in tasks))
            # A finished inner task does not release its outer owner's count.
            # Await accounting instead of spinning on already finished tasks.
            await idle.wait()

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
        if ticket_id is not None and any(
            existing.ticket_id == ticket_id for existing in self._children.values()
        ):
            raise BackendSpawnFailed("Ticket already has a retained source child")
        capability = secrets.token_urlsafe(32)
        resolved = self._localize_start(
            resolved, command.child, capability, args.workspace_home_relative
        )
        try:
            validate_skill_snapshot(args.canonical_skill_snapshot)
            if self.paths is None:
                raise ValueError("executor paths are required for canonical skills")
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
                args.canonical_skill_snapshot, paths=self.paths, native_home=native_home
            )
            assert resolved.role_materials is not None
            identity = dict(resolved.role_materials.identity_environment_variables)
            identity[NATIVE_SKILLS_DIR_ENV] = str(generation / "skills")
            resolved = replace(
                resolved,
                role_materials=replace(
                    resolved.role_materials, identity_environment_variables=tuple(identity.items())
                ),
            )
        except Exception as error:
            raise BackendSpawnFailed(f"Canonical skill bootstrap failed: {error}") from error
        native = self.factory(resolved, capability, command.child)(
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
        if (
            child.reference.child_id in self._retired_children
            or child.reference.child_id in self._retiring_children
        ):
            raise _CommandAdmissionRefused("source child identity is retiring")

    @staticmethod
    def _with_native_worker_context(
        resolved_start: ResolvedConversationStart | None, content: MessageContent
    ) -> MessageContent:
        """Tell Mac Workers about the skills and Ticket files on this host."""
        materials = None if resolved_start is None else resolved_start.role_materials
        identity = {} if materials is None else dict(materials.identity_environment_variables)
        if identity.get("PLAN_ACTOR") == "worker" and identity.get("PLAN_TICKET_ID"):
            return with_runtime_prompt_context(content, NATIVE_WORKER_CONTEXT)
        return content

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
                self._with_native_worker_context(child.localized_start, args.content),
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
            self._with_native_worker_context(child.localized_start, args.content),
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
        previous = self._answers.get(answer.answer_id)
        if previous is not None and previous != answer:
            raise ValueError("answer id reused with different recorded answer")
        self._answers[answer.answer_id] = answer
        if child.active_turn is not None and child.active_turn != answer.ask.turn:
            raise _CommandAdmissionRefused("answer turn is no longer active")
        if answer.ask.kind is AskKind.permission:
            if child.asks.get(answer.ask.ask_id) != answer.ask:
                raise _CommandAdmissionRefused("permission callback is gone")
        else:
            pending = child.user_inputs.get(answer.ask.ask_id)
            if pending is None or pending[0] != answer.ask:
                raise _CommandAdmissionRefused("user input callback is gone")
            normalized = ordered_user_input_answers(
                pending[1].questions, answer.user_input_answers or ()
            )
            if normalized is None or normalized != answer.user_input_answers:
                raise ValueError("recorded user input map is incomplete")

    def _answer_application(self, command: ChildCommand, ask: AskReference | None) -> JsonObject:
        if ask is None:
            return {}
        application = AnswerApplication(command.command_id, ask, now_ms())
        self._applications[command.command_id] = application
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
        pending = child.user_inputs.pop(args.request_id, None)
        return self._answer_application(command, None if pending is None else pending[0])

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
        if command.operation is not ChildOperation.stop and self.prepare_inputs is not None:

            async def prepare() -> None:
                assert self.prepare_inputs is not None
                await self.prepare_inputs(command)

            task = asyncio.create_task(prepare())
            self._commands[command.command_id].preparation = task
            if command.command_id in self._withdrawn_commands:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if command.command_id not in self._withdrawn_commands:
                    raise
            except Exception as error:
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.refused,
                    {
                        "error_type": "BackendSpawnFailed"
                        if command.operation is ChildOperation.start
                        else "PromptWriteRefused",
                        "error_message": str(error),
                        "reason": "input_unavailable",
                    },
                )
                self._commands[command.command_id].outcome = outcome
                return outcome
            finally:
                self._commands[command.command_id].preparation = None
        if command.command_id in self._withdrawn_commands:
            outcome = CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.refused,
                {"reason": "withdrawn_before_invocation"},
            )
            self._commands[command.command_id].outcome = outcome
            return outcome
        return await self._execute_ready(command)

    async def _execute_ready(self, command: ChildCommand) -> CommandOutcome:
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
        else:
            try:
                args = command.arguments
                if isinstance(args, StartRun):
                    result = await self._start_run(command, args)
                else:
                    child = self._children.get(command.child.child_id)
                    if child is None or child.reference != command.child:
                        return await self._remember_outcome(
                            command,
                            CommandOutcome(
                                command.command_id,
                                command.child,
                                CommandDisposition.uncertain,
                                {"reason": "child_gone"},
                            ),
                        )
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
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.uncertain
                    if result.get("kind") == "steer_uncertain"
                    else CommandDisposition.succeeded,
                    result,
                )
            except BackendPromptRejected as error:
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.refused,
                    {
                        "error_type": "BackendPromptRejected",
                        "error_message": str(error),
                    },
                )
            except _CommandAdmissionRefused as error:
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.refused,
                    {
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                    },
                )
            except (ValueError, BackendAdapterError) as error:
                # A native write failure does not prove non-delivery.
                refusal_type = next(
                    (
                        kind
                        for kind in (
                            BackendSpawnFailed,
                            SessionLoadFailed,
                            NeedsRebind,
                            PromptWriteRefused,
                        )
                        if isinstance(error, kind)
                    ),
                    None,
                )
                disposition = (
                    CommandDisposition.refused
                    if refusal_type is not None
                    else CommandDisposition.uncertain
                )
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    disposition,
                    {
                        "error_type": (
                            refusal_type.__name__
                            if refusal_type is not None
                            else type(error).__name__
                        ),
                        "error_message": str(error),
                    },
                )
            except Exception as error:
                outcome = CommandOutcome(
                    command.command_id,
                    command.child,
                    CommandDisposition.uncertain,
                    {"error_type": type(error).__name__, "error_message": str(error)},
                )
        return await self._remember_outcome(command, outcome)

    async def _remember_outcome(
        self, command: ChildCommand, outcome: CommandOutcome
    ) -> CommandOutcome:
        self._commands[command.command_id].outcome = outcome
        await self._publish_required_output(command, outcome)
        return outcome

    async def _publish_required_output(
        self, command: ChildCommand, outcome: CommandOutcome
    ) -> None:
        application = self._applications.get(command.command_id)
        if application is not None:
            await self._publish_application(application)
        if (
            command.operation is ChildOperation.stop
            and outcome.disposition is CommandDisposition.succeeded
            and command.command_id not in self._stop_proofs_published
        ):
            # Last in this child's FIFO: accepting this item proves prior output arrived.
            await self._emit(
                outcome.child.conversation_id,
                EnvelopeKind.child_stopped,
                outcome,
                submission_id="stopped_" + hashlib.sha256(outcome.command_id.encode()).hexdigest(),
            )
            self._stop_proofs_published.add(command.command_id)

    async def _emit(
        self,
        conversation_id: str,
        kind: EnvelopeKind,
        value: object,
        submission_id: str | None = None,
    ) -> None:
        payload = cast(JsonObject, encode_value(value))
        await asyncio.to_thread(
            self.spool.add,
            OutboxEntry(conversation_id, kind, payload, submission_id),
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
        child = self._children.get(reference.child_id)
        if child is None or child.reference != reference:
            raise ValueError("event from unknown child")
        if method == BackendEventMethod.permission_ask_raised:
            ask = cast(BackendPermissionAsk, arguments["ask"])
            if turn is None:
                raise ValueError("permission ask lacks turn")
            child.asks[ask.ask_id] = AskReference(reference, turn, ask.ask_id, AskKind.permission)
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
        await asyncio.to_thread(
            self.spool.add,
            OutboxEntry(
                reference.conversation_id,
                EnvelopeKind.backend_event,
                cast(JsonObject, encode_value(event)),
            ),
            ticket_id=child.ticket_id
            if method in (
                BackendEventMethod.agent_message_completed,
                BackendEventMethod.tool_call_finished,
                BackendEventMethod.turn_ended,
            )
            else None,
        )

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

    def _quiescence_counts(self) -> tuple[int, int, int, int]:
        active = sum(child.active_turn is not None for child in self._children.values())
        callbacks = sum(
            len(child.asks) + len(child.user_inputs) for child in self._children.values()
        )
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
            record.command.operation is ChildOperation.stop
            and outcome.disposition is CommandDisposition.succeeded
            and command_id not in self._stop_proofs_published
            for command_id, record in self._commands.items()
            if (outcome := record.outcome) is not None
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
