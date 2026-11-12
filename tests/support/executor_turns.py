"""Canonical persisted targets for executor tests, independent of a live proxy."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from planner.conversation.backends.contracts import BackendEventSink, RetainedBackendRun
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.lifecycle_storage import apply_facts
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.db import connect
from planner.execution_identity import AskKind, AskReference, ChildReference
from planner.executor.protocol import (
    CanonicalSkillSnapshot,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    JsonValue,
)
from planner.executor.store import ExecutorStore
from planner.executor.vps_remote_child import MacRemoteChild


def _public_request_id(
    ask: AskReference, asks: tuple[AskReference, ...], runtime: Mapping[str, JsonValue]
) -> str:
    field = (
        "permission_provider_ids" if ask.kind is AskKind.permission else "user_input_provider_ids"
    )
    mapping = runtime.get(field)
    if isinstance(mapping, dict):
        for public, provider in mapping.items():
            if provider == ask.ask_id:
                return public
    if ask.turn.turn_number > 1 or sum(a.ask_id == ask.ask_id for a in asks) > 1:
        return f"{ask.turn.turn_number}:{ask.kind.value}:{ask.ask_id}"
    return ask.ask_id


@dataclass
class ScriptedRemoteCommands:
    """A remote child's commands answered by a scripted send, instead of the mailbox.

    put, steer, stop and answers each await send_command, so a test can hold or observe
    the exact command. With db_path, mac_runs is kept as the mailbox's put keeps it.
    A command whose scripted outcome is refused with executor_process_replaced counts as
    refused at put, as the mailbox refuses a command of a replaced process.
    """

    send_command: Callable[[ChildCommand], Awaitable[CommandOutcome]]
    db_path: str = ""
    cancel_initial: Callable[[ChildReference, tuple[str, ...]], Awaitable[bool]] | None = None
    retire_reference: Callable[[ChildReference], None] | None = None
    present: bool = True

    async def _send(self, command: ChildCommand) -> CommandOutcome:
        store = ExecutorStore(self.db_path) if self.db_path else None
        if store is not None and command.operation in (
            ChildOperation.start,
            ChildOperation.write_prompt,
            ChildOperation.stop,
        ):
            store.prepare_command(command)
        outcome = await self.send_command(command)
        if store is not None:
            store.record_prompt_outcome(command, outcome)
        return outcome

    async def put(self, command: ChildCommand) -> bool:
        outcome = await self._send(command)
        return not (
            outcome.disposition is CommandDisposition.refused
            and outcome.result.get("reason") == "executor_process_replaced"
        )

    def mac_is_present(self) -> bool:
        return self.present

    async def deliver_steer(self, command: ChildCommand) -> CommandOutcome | None:
        return await self._send(command)

    async def stop(self, command: ChildCommand) -> CommandOutcome:
        outcome = await self._send(command)
        if self.db_path and outcome.disposition is CommandDisposition.succeeded:
            ExecutorStore(self.db_path).close_child(command.child, 1)
        return outcome

    async def save_answer(self, command: ChildCommand) -> bool:
        return (await self._send(command)).disposition is not CommandDisposition.refused

    async def cancel_initial_delivery(
        self, child: ChildReference, command_ids: tuple[str, ...]
    ) -> bool:
        if self.cancel_initial is None:
            return False
        return await self.cancel_initial(child, command_ids)

    def retire_child(self, child: ChildReference) -> None:
        if self.retire_reference is not None:
            self.retire_reference(child)


def scripted_remote_child(
    reference: ChildReference,
    send_command: Callable[[ChildCommand], Awaitable[CommandOutcome]],
    skill_snapshot_supplier: Callable[[ResolvedConversationStart], CanonicalSkillSnapshot],
    *,
    db_path: str = "",
    initial_history_complete: bool = True,
    event_sink: BackendEventSink | None = None,
) -> MacRemoteChild:
    """A MacRemoteChild over ScriptedRemoteCommands."""
    return MacRemoteChild(
        reference,
        ScriptedRemoteCommands(send_command, db_path),
        skill_snapshot_supplier,
        event_sink=event_sink,
        initial_history_complete=initial_history_complete,
    )


@dataclass
class MacRemoteChildFactory:
    """Scripted transport over the real run store; never substitutes conversation custody."""

    assign_child: Callable[[ResolvedConversationStart, BackendEventSink], ChildReference]
    send_command: Callable[[ChildCommand], Awaitable[CommandOutcome]]
    skill_snapshot_supplier: Callable[[ResolvedConversationStart], CanonicalSkillSnapshot]
    cancel_initial_delivery: Callable[[ChildReference, tuple[str, ...]], Awaitable[bool]] | None = (
        None
    )
    retire_reference: Callable[[ChildReference], None] | None = None
    db_path: str = ""

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return ExecutorStore(self.db_path).retained_runs()

    def recover(
        self, reference: ChildReference, *, event_sink: BackendEventSink | None = None
    ) -> MacRemoteChild:
        return self._child(reference, restored=True, event_sink=event_sink)

    def _child(
        self,
        reference: ChildReference,
        *,
        restored: bool = False,
        event_sink: BackendEventSink | None = None,
    ) -> MacRemoteChild:
        return MacRemoteChild(
            reference,
            ScriptedRemoteCommands(
                self.send_command,
                self.db_path,
                self.cancel_initial_delivery,
                self.retire_reference,
            ),
            self.skill_snapshot_supplier,
            event_sink=event_sink,
            initial_history_complete=not restored,
        )

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> MacRemoteChild:
        reference = self.assign_child(resolved_start, event_sink)
        store = ExecutorStore(self.db_path)
        environment = dict(
            resolved_start.role_materials.identity_environment_variables
            if resolved_start.role_materials
            else ()
        )
        if store.child_assignment(reference) is None:
            # The scripted Mac is the current process, as its lease would make it.
            from tests.support.executor_presence import mac_identity, make_current

            make_current(self.db_path, mac_identity(reference.executor_incarnation))
            store.register_child(
                reference, "ticket", environment.get("PLAN_TICKET_ID", "test-ticket"), 1
            )
        return self._child(reference, event_sink=event_sink)


def persist_pending_turn(
    store: ExecutorStore,
    *asks: AskReference,
    runtime_overrides: Mapping[str, JsonValue] | None = None,
) -> None:
    """Install the assignment and turn that make an accepted ask owner-answerable.

    The ask's executor process is made current first, as its lease would.
    """
    first = asks[0]
    from tests.support.executor_presence import mac_identity, make_current

    make_current(store.db_path, mac_identity(first.child.executor_incarnation))
    connection = connect(store.db_path)
    try:
        connection.execute(
            "INSERT OR IGNORE INTO conversations(conversation_id,backend_key,model,"
            "workspace_folder,access,created_at) "
            "VALUES (?,'codex','scripted','/tmp','full',1)",
            (first.child.conversation_id,),
        )
    finally:
        connection.close()
    store.register_child(first.child, "ticket", "test-ticket", 1000)
    runtime: dict[str, JsonValue] = {}
    runtime.update(runtime_overrides or {})
    connection = connect(store.db_path)
    try:
        connection.execute(
            "UPDATE mac_runs SET turn_number=?,delivery_uncertain=?,stop_state=? "
            "WHERE conversation_id=?",
            (
                first.turn.turn_number,
                bool(runtime.get("delivery_uncertain")),
                "closed"
                if runtime.get("source_retired")
                else "stopping"
                if runtime.get("callbacks_revoked")
                else "open",
                first.child.conversation_id,
            ),
        )
        apply_facts(
            connection,
            first.child.conversation_id,
            (
                LifecycleFact(
                    LifecycleKind.turn,
                    f"turn:{first.turn.turn_number}",
                    first.turn.turn_number,
                    LifecycleOperation.turn,
                    LifecycleStatus.completed if runtime.get("ended") else LifecycleStatus.running,
                ),
                *(
                    LifecycleFact(
                        LifecycleKind.request,
                        _public_request_id(ask, asks, runtime),
                        ask.turn.turn_number,
                        LifecycleOperation.user_input
                        if ask.kind is AskKind.user_input
                        else LifecycleOperation.permission,
                        LifecycleStatus.unavailable
                        if (
                            ask.kind is AskKind.permission
                            and runtime.get("pending_permission_ask_ids") == []
                        )
                        else LifecycleStatus.pending,
                        ask.ask_id,
                    )
                    for ask in asks
                ),
            ),
        )
    finally:
        connection.close()
