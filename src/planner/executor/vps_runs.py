"""Retained VPS run control for Ticket Workers executing on a Mac."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4

from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendSpawnFailed,
    DeliveryCancelled,
    RetainedBackendRun,
)
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.core import db
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.execution_identity import (
    ChildReference,
    ExecutorIdentity,
)
from planner.executor.link import Link
from planner.executor.link_files import settled_io
from planner.executor.protocol import (
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    JsonObject,
    MacRunConversations,
    ReconnectAsk,
    ReconnectResult,
    WireMessageKind,
    WithdrawCommands,
    WithdrawResult,
    encode_value,
    type_adapter,
)
from planner.executor.skills import read_canonical_skill_snapshot
from planner.executor.store import ExecutorStore
from planner.executor.vps_remote_child import MacRemoteChild


def now_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass
class _PendingCommand:
    command: ChildCommand
    future: asyncio.Future[CommandOutcome]
    possibly_emitted: bool = False
    emissions: set[asyncio.Task[bool]] = field(default_factory=set)


class MacRuns:
    """Retained Mac children, command fate, reconnect and saved answers."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.store = ExecutorStore(db_path)
        self.conversations: MacRunConversations
        self.identity: ExecutorIdentity | None = None
        self.link: Link | None = None
        self.ready = False
        self.children: dict[str, MacRemoteChild] = {}
        self.pending_commands: dict[str, _PendingCommand] = {}
        self._cancelled_initial_children: set[ChildReference] = set()
        self._emitted_prompts: dict[ChildReference, set[str]] = {}
        self._tasks: set[asyncio.Task[object]] = set()

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> MacRemoteChild:
        del message_files
        environment = dict(
            resolved_start.role_materials.identity_environment_variables
            if resolved_start.role_materials
            else ()
        )
        ticket_id = environment.get("PLAN_TICKET_ID")
        if environment.get("PLAN_ACTOR") != "worker" or not ticket_id:
            raise BackendSpawnFailed("Mac runs require a Ticket Worker identity")
        reference = self.assign_child(resolved_start, event_sink)
        child = self._make_child(reference, initial_history_complete=True)
        self.children[reference.child_id] = child
        return child

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return self.store.retained_runs()

    def recover(self, reference: ChildReference) -> MacRemoteChild | None:
        if self.store.child_assignment(reference) is None:
            return None
        return self._make_child(reference)

    async def close(self) -> None:
        self.ready = False
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def admit(self, link: Link) -> None:
        identity = link.identity
        old_link = self.link
        await settled_io(self.store.begin_connection, identity, now_ms())
        self.identity, self.link, self.ready = identity, link, False
        if old_link is not None:
            old_link.close()

    async def interrupt_replaced(self, old_identity: ExecutorIdentity | None) -> None:
        identity = self.identity
        if identity is None:
            return
        replaced = (
            old_identity is not None
            and old_identity.executor_incarnation != identity.executor_incarnation
        )
        if replaced:
            for pending in tuple(self.pending_commands.values()):
                command = pending.command
                if not pending.future.done():
                    pending.future.set_result(
                        CommandOutcome(
                            command.command_id,
                            command.child,
                            CommandDisposition.uncertain,
                            {"reason": "executor_process_replaced"},
                        )
                    )
        for run in await settled_io(self.store.retained_runs):
            if (
                not run.retirement_confirmed
                and run.reference.executor_incarnation != identity.executor_incarnation
            ):
                await settled_io(self.store.close_child, run.reference, now_ms())
                replaced = True
        if replaced and hasattr(self, "conversations"):
            await self.conversations.interrupt_replaced_executor_runs(identity.executor_incarnation)

    async def link_up(self, link: Link, request_id: str) -> None:
        identity = link.identity
        registered_before_ask = set(self.pending_commands)
        unknown = await self._reconnect(identity, link)
        if not await self._reconciliation_current(identity, link):
            return
        await settled_io(self.store.mark_ready, identity, now_ms())
        if not await link.tell(
            WireMessageKind.reconciliation_ready,
            {"ready": True},
            request_id=request_id,
            admit=lambda: self.link is link and self.identity == identity,
        ):
            return
        if self.link is not link or self.identity != identity:
            return
        reconnect_commands = tuple(
            pending
            for command_id, pending in self.pending_commands.items()
            if command_id in unknown or command_id not in registered_before_ask
        )
        self.ready = True
        for child in tuple(self.children.values()):
            for command in tuple(child.saved_answers.values()):
                self.queue_answer(command)
        for pending in reconnect_commands:
            command = pending.command
            if (
                not pending.future.done()
                and command.child.executor_incarnation == identity.executor_incarnation
            ):
                if not await self._emit_command(pending, identity, link):
                    self._release_closed_answer(command, pending.future)

    async def link_down(self, link: Link) -> None:
        if self.link is link:
            self.ready = False
            self.link = None
            await settled_io(self.store.mark_lost, link.identity, now_ms())

    def _read_skill_snapshot(self, start: ResolvedConversationStart) -> CanonicalSkillSnapshot:
        environment = dict(
            start.role_materials.identity_environment_variables if start.role_materials else ()
        )
        ticket_id = environment.get("PLAN_TICKET_ID")
        if environment.get("PLAN_ACTOR") != "worker" or not ticket_id:
            raise BackendSpawnFailed("Canonical skill bootstrap requires a Worker Ticket identity")
        conn = db.connect(self.db_path)
        try:
            conn.execute("BEGIN")
            return read_canonical_skill_snapshot(conn, ticket_id)
        finally:
            conn.close()

    def assign_child(
        self, start: ResolvedConversationStart, sink: BackendEventSink
    ) -> ChildReference:
        if self.identity is None or not self.ready:
            raise BackendSpawnFailed("The Mac executor has no reconciled connection")
        del sink  # Conversation owns event delivery; run control retains only the child.
        child = ChildReference(
            self.identity.executor_incarnation, str(uuid4()), start.conversation_id
        )
        environment = dict(
            start.role_materials.identity_environment_variables if start.role_materials else ()
        )
        ticket_id = environment.get("PLAN_TICKET_ID")
        if environment.get("PLAN_ACTOR") != "worker" or not ticket_id:
            raise BackendSpawnFailed("Mac runs require a Ticket Worker identity")
        self.store.register_child(child, "ticket", ticket_id, now_ms())
        return child

    def _make_child(
        self, child: ChildReference, *, initial_history_complete: bool = False
    ) -> MacRemoteChild:
        """Bind the retained identity without starting it or granting new authority."""
        return MacRemoteChild(
            child,
            self.send_new_command,
            self._read_skill_snapshot,
            self.cancel_initial_delivery,
            initial_history_complete=initial_history_complete,
            retire_reference=self.retire_child,
            send_answer=self.queue_answer,
        )

    def retire_child(self, child: ChildReference) -> None:
        """Drop only the exact retired proxy and its reconciliation material."""
        proxy = self.children.get(child.child_id)
        if proxy is not None and proxy.reference == child:
            self.children.pop(child.child_id, None)
        self._emitted_prompts.pop(child, None)
        self._cancelled_initial_children.discard(child)

    def _initial_command_cancelled(self, command: ChildCommand) -> bool:
        return (
            command.operation in (ChildOperation.start, ChildOperation.write_prompt)
            and command.child in self._cancelled_initial_children
        )

    async def cancel_initial_delivery(
        self, child: ChildReference, command_ids: tuple[str, ...]
    ) -> bool:
        """Fence before cleanup; retained native outcomes remain untouched."""
        self._cancelled_initial_children.add(child)
        prompt_ids = set(command_ids) & self._emitted_prompts.get(child, set())
        prompt_may_have_sent = bool(prompt_ids)
        for pending in tuple(self.pending_commands.values()):
            command = pending.command
            if command.child != child or not self._initial_command_cancelled(command):
                continue
            for emission in pending.emissions:
                emission.cancel()
            if not pending.future.done() and not pending.possibly_emitted:
                pending.future.set_exception(DeliveryCancelled())
        if prompt_may_have_sent and self.ready and self.link is not None:
            identity = self.identity
            try:
                response = await self.link.request(
                    WireMessageKind.withdraw, _object(WithdrawCommands(child, command_ids))
                )
                if (
                    response.kind is not WireMessageKind.withdraw_result
                    or identity != self.identity
                ):
                    raise ValueError("invalid withdrawal reply")
                result = type_adapter(WithdrawResult).validate_python(response.payload)
                if prompt_ids <= set(result.withdrawn):
                    prompt_may_have_sent = False
            except (OSError, ConnectionError, ValueError, TimeoutError):
                pass  # Loss supplies no proof of non-invocation.
        return prompt_may_have_sent

    async def _reconciliation_current(self, identity: ExecutorIdentity, writer: Link) -> bool:
        if self.link is not writer or writer.is_closing() or self.identity != identity:
            return False
        try:
            await settled_io(self.store.verify_connection, identity)
        except PlannerError:
            return False
        return self.link is writer and not writer.is_closing() and self.identity == identity

    async def _emit_command(
        self, pending: _PendingCommand, identity: ExecutorIdentity, writer: Link
    ) -> bool:
        command = pending.command
        emit = (
            self._emit_answer(command, identity, writer)
            if command.operation
            in (
                ChildOperation.answer_permission_ask,
                ChildOperation.answer_user_input,
            )
            else self._emit_input(pending, identity, writer)
        )
        task = asyncio.create_task(emit)
        pending.emissions.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if not self._initial_command_cancelled(command):
                raise
            return False
        finally:
            pending.emissions.discard(task)

    async def _emit_input(
        self, pending: _PendingCommand, identity: ExecutorIdentity, writer: Link
    ) -> bool:
        command = pending.command

        def admit() -> bool:
            if self._initial_command_cancelled(command) or pending.future.done():
                return False
            if identity != self.identity or writer is not self.link:
                raise ConnectionError("command input generation changed")
            if not self.ready:
                raise ConnectionError("executor link unavailable")
            pending.possibly_emitted = True
            if command.operation is ChildOperation.write_prompt:
                self._emitted_prompts.setdefault(command.child, set()).add(command.command_id)
            return True

        return await writer.tell(
            WireMessageKind.command,
            _object(command),
            request_id=command.command_id,
            admit=admit,
        )

    async def _emit_answer(
        self, command: ChildCommand, identity: ExecutorIdentity, writer: Link
    ) -> bool:
        child = self.children.get(command.child.child_id)
        emitted = False

        def admit_answer() -> bool:
            if not self.ready or self.link is not writer or self.identity != identity:
                raise ConnectionError("answer connection changed")
            return (
                child is not None
                and child is self.children.get(command.child.child_id)
                and child.reference == command.child
                and child.can_emit_answer(command)
            )

        async def emit() -> None:
            nonlocal emitted
            if not admit_answer():
                return
            emitted = await writer.tell(
                WireMessageKind.command,
                _object(command),
                request_id=command.command_id,
                admit=admit_answer,
            )

        if command.turn is not None:
            await self.conversations.emit_answer(command.turn, emit)
        return emitted

    def _release_closed_answer(
        self, command: ChildCommand, future: asyncio.Future[CommandOutcome]
    ) -> None:
        if command.operation in (
            ChildOperation.answer_permission_ask,
            ChildOperation.answer_user_input,
        ):
            child = self.children.get(command.child.child_id)
            if child is not None:
                child.saved_answers.pop(command.command_id, None)
            future.cancel()

    def queue_answer(self, command: ChildCommand) -> None:
        if not self.ready or command.command_id in self.pending_commands:
            return
        task = asyncio.create_task(self.send_command(command))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def send_new_command(self, command: ChildCommand) -> CommandOutcome:
        """Prepare a new command, send it, and record its outcome."""
        await settled_io(self.store.prepare_command, command)
        outcome = await self.send_command(command)
        await settled_io(self.store.record_prompt_outcome, command, outcome)
        if (
            command.operation is ChildOperation.stop
            and outcome.disposition is CommandDisposition.succeeded
        ):
            await settled_io(self.store.close_child, command.child, now_ms())
        return outcome

    async def send_command(self, command: ChildCommand) -> CommandOutcome:
        if self._initial_command_cancelled(command):
            raise DeliveryCancelled()
        existing = self.pending_commands.get(command.command_id)
        if existing is not None:
            if existing.command != command:
                raise ValueError("command identity reused with different content")
            return await asyncio.shield(existing.future)
        if (
            self.identity is not None
            and command.child.executor_incarnation != self.identity.executor_incarnation
        ):
            # A delivery writer can register after _session settled old futures.
            # It must finish as unknown, never wait for or send to the new process.
            return CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.uncertain,
                {"reason": "executor_process_replaced"},
            )
        future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        pending = _PendingCommand(command, future)
        self.pending_commands[command.command_id] = pending
        try:
            if self.ready and self.link is not None:
                identity, writer = self.identity, self.link
                assert identity is not None
                try:
                    if not future.done() and not await self._emit_command(
                        pending, identity, writer
                    ):
                        self._release_closed_answer(command, future)
                except (OSError, ConnectionError):
                    pass  # The exact command remains pending across a link loss.
            return await asyncio.shield(future)
        finally:
            if future.done():
                self.pending_commands.pop(command.command_id, None)

    def authority(self, child: ChildReference) -> Principal:
        binding = self.store.child_assignment(child)
        if binding is None:
            raise PlannerError(ErrorCode.agent_forbidden, "child has no VPS assignment", {})
        return Principal(PrincipalKind(binding[0]), binding[1])

    def _closed_reconciliation_child(
        self, child: ChildReference, identity: ExecutorIdentity
    ) -> bool:
        # Historical assignment is evidence for settling original outcomes only.
        # It never makes this child adoptable or restores its work authority.
        if child.executor_incarnation != identity.executor_incarnation:
            raise ValueError("reconciliation child incarnation conflicts with connection")
        if self.store.has_closed_child_assignment(child):
            return True
        self.authority(child)
        return False

    def validate_stop(self, outcome: CommandOutcome) -> None:
        # Closed assignments admit only their retained exact stop proof.
        # They grant no callback, command-result or new native-work authority.
        pending = self.pending_commands.get(outcome.command_id)
        if not (
            self.store.has_closed_child_assignment(outcome.child)
            and pending is not None
            and pending.command.child == outcome.child
            and pending.command.operation is ChildOperation.stop
        ):
            self.authority(outcome.child)
        if outcome.disposition is not CommandDisposition.succeeded:
            raise ValueError("child_stopped requires successful exact-child stop")
        if pending is not None and pending.command.operation is not ChildOperation.stop:
            raise ValueError("child_stopped conflicts with pending command")

    def _resolve_command(self, outcome: CommandOutcome, *, stop_proven: bool = False) -> None:
        pending = self.pending_commands.get(outcome.command_id)
        if pending is None or pending.command.child != outcome.child:
            return
        if (
            pending.command.operation is ChildOperation.stop
            and outcome.disposition is CommandDisposition.succeeded
            and not stop_proven
        ):
            return
        if outcome.disposition is not CommandDisposition.uncertain:
            child = self.children.get(outcome.child.child_id)
            if child is not None and child.reference == outcome.child:
                child.saved_answers.pop(outcome.command_id, None)
        if not pending.future.done():
            pending.future.set_result(outcome)

    def owns_conversation(self, conversation_id: str) -> bool:
        return any(
            run.reference.conversation_id == conversation_id and not run.retirement_confirmed
            for run in self.store.retained_runs()
        )

    async def _reconnect(self, identity: ExecutorIdentity, writer: Link) -> set[str]:
        pending = {
            key: item.command
            for key, item in tuple(self.pending_commands.items())
            if not item.future.done()
            and item.command.child.executor_incarnation == identity.executor_incarnation
        }
        response = await writer.request(
            WireMessageKind.reconnect_ask, _object(ReconnectAsk(tuple(pending)))
        )
        if not await self._reconciliation_current(identity, writer):
            raise ConnectionError("reconnect generation changed")
        if response.kind is not WireMessageKind.reconnect_result:
            raise ValueError("reconnect ask refused")
        result = type_adapter(ReconnectResult).validate_python(response.payload)
        if result.identity != identity or tuple(x.command_id for x in result.commands) != tuple(
            pending
        ):
            raise ValueError("reconnect command identities changed")
        unknown = self._reconcile_commands(result, pending)
        await self._reconcile_children(result.children, identity, writer)
        return unknown

    def _reconcile_commands(
        self, result: ReconnectResult, pending: dict[str, ChildCommand]
    ) -> set[str]:
        unknown: set[str] = set()
        for state in result.commands:
            if state.phase == "finished":
                if state.outcome is None or state.outcome.command_id != state.command_id:
                    raise ValueError("finished command lacks its result")
                if state.outcome.child != pending[state.command_id].child:
                    raise ValueError("finished command child changed")
                self._resolve_command(state.outcome)
            elif state.outcome is not None:
                raise ValueError("unfinished command has an outcome")
            elif state.phase == "unknown":
                unknown.add(state.command_id)
        return unknown

    async def _reconcile_children(
        self, inventory: tuple[ChildInventory, ...], identity: ExecutorIdentity, writer: Link
    ) -> None:
        children: dict[ChildReference, ChildInventory] = {}
        seen: set[str] = set()
        for item in inventory:
            if item.child.child_id in seen:
                raise ValueError("duplicate reconnect child")
            seen.add(item.child.child_id)
            if await settled_io(self._closed_reconciliation_child, item.child, identity):
                self.retire_child(item.child)
                continue
            if not await self._reconciliation_current(identity, writer):
                raise ConnectionError("reconnect generation changed")
            children[item.child] = item
        # Inventory absence is not termination evidence. Stop every retained run
        # after a VPS restart, including one that never reached Mac creation.
        for run in await settled_io(self.store.retained_runs):
            if (
                run.retirement_confirmed
                or run.reference.executor_incarnation != identity.executor_incarnation
                or run.reference.child_id in self.children
            ):
                continue
            if not await self._reconciliation_current(identity, writer):
                raise ConnectionError("reconnect generation changed")
            proxy = self._make_child(run.reference)
            self.children[run.reference.child_id] = proxy
            await self.conversations.stop_interrupted_remote_child(run.reference, proxy)
        if not await self._reconciliation_current(identity, writer):
            raise ConnectionError("reconnect generation changed")
        for proxy in tuple(self.children.values()):
            proxy.observe_callbacks(children.get(proxy.reference))


def _object(value: object) -> JsonObject:
    encoded = encode_value(value)
    if not isinstance(encoded, dict):
        raise ValueError("expected object")
    return encoded
