"""Retained VPS run control for Ticket Workers executing on a Mac."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from uuid import uuid4

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import (
    AdmissionRefused,
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
)
from planner.executor.skills import read_canonical_skill_snapshot
from planner.executor.store import ExecutorStore
from planner.executor.vps_remote_child import MacRemoteChild, command_emission_decision


def now_ms() -> int:
    return time.time_ns() // 1_000_000


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
        self.pending_commands: dict[str, tuple[ChildCommand, asyncio.Future[CommandOutcome]]] = {}
        self._cancelled_initial_children: set[ChildReference] = set()
        self._possibly_emitted_commands: set[str] = set()
        self._newly_recorded_commands: dict[str, ChildCommand] = {}
        self._policy_held_commands: set[str] = set()
        self._prompt_command_ids: set[str] = set()
        self._emission_tasks: dict[str, set[asyncio.Task[bool]]] = {}
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
        child = MacRemoteChild(
            reference,
            self.send_new_command,
            self._read_skill_snapshot,
            self.cancel_initial_delivery,
            retire_reference=self.retire_child,
            send_answer=self.queue_answer,
        )
        self.children[reference.child_id] = child
        return child

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return self.store.retained_runs()

    def recover(self, reference: ChildReference) -> MacRemoteChild | None:
        if self.store.child_assignment(reference) is None:
            return None
        return self.recover_child(reference)

    @staticmethod
    def interrupt_after_restart(conn: sqlite3.Connection, at_ms: int) -> None:
        conn.execute(
            "UPDATE executor_connections SET connection_state='disconnected',lost_at_ms=? "
            "WHERE active=1",
            (at_ms,),
        )
        conn.execute("UPDATE mac_runs SET stop_state='stopping' WHERE stop_state='open'")

    @staticmethod
    def status(conn: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
        return tuple(
            (row[0], row[1])
            for row in conn.execute(
                "SELECT identity_json,connection_state FROM executor_connections WHERE active=1"
            )
        )

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
            for command, future in tuple(self.pending_commands.values()):
                if not future.done():
                    future.set_result(
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
        for command, future in reconnect_commands:
            if (
                not future.done()
                and command.child.executor_incarnation == identity.executor_incarnation
            ):
                if not await self._emit_command(command, identity, link):
                    await self._release_closed_answer(command, future)

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

    def recover_child(
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
        prompt_may_have_sent = any(
            command_id in self._possibly_emitted_commands
            for command_id in command_ids
            if command_id in self._prompt_command_ids
        )
        for command, future in tuple(self.pending_commands.values()):
            if command.child != child or not self._initial_command_cancelled(command):
                continue
            if command.operation is ChildOperation.write_prompt:
                prompt_may_have_sent |= command.command_id in self._possibly_emitted_commands
            for emission in self._emission_tasks.get(command.command_id, ()):
                emission.cancel()
            if not future.done() and command.command_id not in self._possibly_emitted_commands:
                future.set_exception(DeliveryCancelled())
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
                result = TypeAdapter(WithdrawResult).validate_python(response.payload)
                prompt_ids = set(command_ids) & self._prompt_command_ids
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
        self,
        command: ChildCommand,
        identity: ExecutorIdentity | None = None,
        writer: Link | None = None,
    ) -> bool:
        identity = self.identity if identity is None else identity
        writer = self.link if writer is None else writer
        task = asyncio.create_task(self._emit_command_unsettled(command, identity, writer))
        tasks = self._emission_tasks.setdefault(command.command_id, set())
        tasks.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if not self._initial_command_cancelled(command):
                raise
            return False
        finally:
            tasks.discard(task)
            if not tasks:
                self._emission_tasks.pop(command.command_id, None)

    async def _emit_command_unsettled(
        self,
        command: ChildCommand,
        expected_identity: ExecutorIdentity | None = None,
        expected_writer: Link | None = None,
    ) -> bool:
        """The final guard is shared by immediate sends and reconnect replay."""
        if command.operation not in (
            ChildOperation.answer_permission_ask,
            ChildOperation.answer_user_input,
        ):
            if self._initial_command_cancelled(command):
                return False
            if expected_identity != self.identity or expected_writer is not self.link:
                raise ConnectionError("command input generation changed")
            if self.link is None or not self.ready:
                raise ConnectionError("executor link unavailable")
            if not await self._command_policy_allows_emission(command, expected_identity):
                return False
            writer = self.link
            assert writer is not None

            def admit() -> bool:
                if self._initial_command_cancelled(command):
                    return False
                pending = self.pending_commands.get(command.command_id)
                if pending is not None and pending[1].done():
                    return False
                if expected_identity != self.identity or expected_writer is not self.link:
                    raise ConnectionError("command input generation changed")
                if not self.ready:
                    raise ConnectionError("executor link unavailable")
                self._possibly_emitted_commands.add(command.command_id)
                if command.operation is ChildOperation.write_prompt:
                    self._prompt_command_ids.add(command.command_id)
                return True

            return await writer.tell(
                WireMessageKind.command,
                _object(command),
                request_id=command.command_id,
                admit=admit,
            )
        child = self.children.get(command.child.child_id)
        answer_writer = expected_writer
        emitted = False

        def admit_answer() -> bool:
            if (
                not self.ready
                or self.link is not answer_writer
                or self.identity != expected_identity
            ):
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
            assert answer_writer is not None
            emitted = await answer_writer.tell(
                WireMessageKind.command,
                _object(command),
                request_id=command.command_id,
                admit=admit_answer,
            )

        if command.turn is not None:
            await self.conversations.emit_answer(command.turn, emit)
        return emitted

    async def _release_closed_answer(
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
        """Only MacRemoteChild._call supplies freshly minted command identities.

        Recovery and reconnect replay use send_command directly. Registration is
        in-process evidence only; a reopened journal never gains unsent provenance.
        """

        await settled_io(self.store.prepare_command, command)
        if command.operation in (ChildOperation.start, ChildOperation.write_prompt):
            self._newly_recorded_commands.setdefault(command.command_id, command)
        outcome = await self.send_command(command)
        await settled_io(self.store.record_prompt_outcome, command, outcome)
        if (
            command.operation is ChildOperation.stop
            and outcome.disposition is CommandDisposition.succeeded
        ):
            await settled_io(self.store.close_child, command.child, now_ms())
        return outcome

    async def _command_policy_allows_emission(
        self, command: ChildCommand, identity: ExecutorIdentity | None
    ) -> bool:
        if command.operation not in (ChildOperation.start, ChildOperation.write_prompt):
            return True
        decision = await settled_io(
            command_emission_decision,
            self.db_path,
            command,
            identity,
            self._newly_recorded_commands.get(command.command_id) == command
            and command.command_id not in self._possibly_emitted_commands,
        )
        if isinstance(decision, CommandOutcome):
            pending = self.pending_commands.get(command.command_id)
            if pending is not None and not pending[1].done():
                pending[1].set_result(decision)
            self._newly_recorded_commands.pop(command.command_id, None)
            return False
        if isinstance(decision, AdmissionRefused):
            if command.command_id not in self._policy_held_commands:
                logging.getLogger("planner.executor").warning(
                    "Command %s on %s remains uncertain; policy blocks redispatch: %s. "
                    "Original evidence or definitive executor settlement is required.",
                    command.command_id,
                    command.child,
                    decision,
                )
                self._policy_held_commands.add(command.command_id)
            return False
        return decision

    async def send_command(self, command: ChildCommand) -> CommandOutcome:
        if self._initial_command_cancelled(command):
            raise DeliveryCancelled()
        existing = self.pending_commands.get(command.command_id)
        if existing is not None:
            if existing[0] != command:
                raise ValueError("command identity reused with different content")
            return await asyncio.shield(existing[1])
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
        self.pending_commands[command.command_id] = (command, future)
        try:
            if self.ready and self.link is not None:
                identity, writer = self.identity, self.link
                try:
                    if not future.done() and not await self._emit_command(
                        command, identity, writer
                    ):
                        await self._release_closed_answer(command, future)
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

    def validate_stop(self, outcome: CommandOutcome, identity: ExecutorIdentity) -> None:
        self._validate_outcome_assignment(outcome, identity)
        if outcome.disposition is not CommandDisposition.succeeded:
            raise ValueError("child_stopped requires successful exact-child stop")
        pending = self.pending_commands.get(outcome.command_id)
        if pending is not None and pending[0].operation is not ChildOperation.stop:
            raise ValueError("child_stopped conflicts with pending command")

    def _validate_outcome_assignment(
        self, outcome: CommandOutcome, identity: ExecutorIdentity
    ) -> None:
        # Closed assignments admit only their retained exact stop proof.
        # They grant no callback, command-result or new native-work authority.
        pending = self.pending_commands.get(outcome.command_id)
        if not (
            self.store.has_closed_child_assignment(outcome.child)
            and pending is not None
            and pending[0].child == outcome.child
            and pending[0].operation is ChildOperation.stop
        ):
            self.authority(outcome.child)

    async def _resolve_command(self, outcome: CommandOutcome, *, stop_proven: bool = False) -> None:
        pending = self.pending_commands.get(outcome.command_id)
        if pending is None or pending[0].child != outcome.child:
            return
        if (
            pending[0].operation is ChildOperation.stop
            and outcome.disposition is CommandDisposition.succeeded
            and not stop_proven
            and not await settled_io(self.store.has_accepted_command_outcome, outcome)
        ):
            return
        if outcome.disposition is not CommandDisposition.uncertain:
            child = self.children.get(outcome.child.child_id)
            if child is not None and child.reference == outcome.child:
                child.saved_answers.pop(outcome.command_id, None)
        if outcome.disposition is not CommandDisposition.uncertain:
            self._newly_recorded_commands.pop(outcome.command_id, None)
            self._policy_held_commands.discard(outcome.command_id)
        pending = self.pending_commands.get(outcome.command_id)
        if pending is not None and pending[0].child == outcome.child and not pending[1].done():
            pending[1].set_result(outcome)

    def owns_conversation(self, conversation_id: str) -> bool:
        return any(
            run.reference.conversation_id == conversation_id and not run.retirement_confirmed
            for run in self.store.retained_runs()
        )

    async def _reconnect(self, identity: ExecutorIdentity, writer: Link) -> set[str]:
        pending = {
            key: command
            for key, (command, future) in tuple(self.pending_commands.items())
            if not future.done()
            and command.child.executor_incarnation == identity.executor_incarnation
        }
        response = await writer.request(
            WireMessageKind.reconnect_ask, _object(ReconnectAsk(tuple(pending)))
        )
        if not await self._reconciliation_current(identity, writer):
            raise ConnectionError("reconnect generation changed")
        if response.kind is not WireMessageKind.reconnect_result:
            raise ValueError("reconnect ask refused")
        result = TypeAdapter(ReconnectResult).validate_python(response.payload)
        if result.identity != identity or tuple(x.command_id for x in result.commands) != tuple(
            pending
        ):
            raise ValueError("reconnect command identities changed")
        unknown: set[str] = set()
        for state in result.commands:
            if state.phase == "finished":
                if state.outcome is None or state.outcome.command_id != state.command_id:
                    raise ValueError("finished command lacks its result")
                if state.outcome.child != pending[state.command_id].child:
                    raise ValueError("finished command child changed")
                await self._resolve_command(state.outcome)
            elif state.outcome is not None:
                raise ValueError("unfinished command has an outcome")
            elif state.phase == "unknown":
                unknown.add(state.command_id)
        children: list[ChildInventory] = []
        seen: set[str] = set()
        for item in result.children:
            if item.child.child_id in seen:
                raise ValueError("duplicate reconnect child")
            seen.add(item.child.child_id)
            if await settled_io(self._closed_reconciliation_child, item.child, identity):
                self.retire_child(item.child)
                continue
            if not await self._reconciliation_current(identity, writer):
                raise ConnectionError("reconnect generation changed")
            children.append(item)
            if item.child.child_id not in self.children:
                proxy = self.recover_child(item.child)
                self.children[item.child.child_id] = proxy
                await self.conversations.stop_interrupted_remote_child(item.child, proxy)
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
            proxy = self.recover_child(run.reference)
            self.children[run.reference.child_id] = proxy
            await self.conversations.stop_interrupted_remote_child(run.reference, proxy)
        if not await self._reconciliation_current(identity, writer):
            raise ConnectionError("reconnect generation changed")
        for proxy in tuple(self.children.values()):
            proxy.observe_callbacks(
                next((item for item in children if item.child == proxy.reference), None)
            )
        return unknown


def _object(value: object) -> JsonObject:
    encoded = encode_value(value)
    if not isinstance(encoded, dict):
        raise ValueError("expected object")
    return encoded
