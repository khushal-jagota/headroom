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

    def recover(self, reference: ChildReference) -> MacRemoteChild:
        return self._child(reference, restored=True)

    def _child(self, reference: ChildReference, *, restored: bool = False) -> MacRemoteChild:
        async def send(command: ChildCommand) -> CommandOutcome:
            store = ExecutorStore(self.db_path)
            store.prepare_command(command)
            result = await self.send_command(command)
            store.record_prompt_outcome(command, result)
            if (
                command.operation is ChildOperation.stop
                and result.disposition is CommandDisposition.succeeded
            ):
                store.close_child(reference, 1)
            return result

        return MacRemoteChild(
            reference,
            send,
            self.skill_snapshot_supplier,
            self.cancel_initial_delivery,
            initial_history_complete=not restored,
            retire_reference=self.retire_reference,
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
            store.register_child(
                reference, "ticket", environment.get("PLAN_TICKET_ID", "test-ticket"), 1
            )
        return self._child(reference)


def persist_pending_turn(
    store: ExecutorStore,
    *asks: AskReference,
    runtime_overrides: Mapping[str, JsonValue] | None = None,
) -> None:
    """Install the assignment and turn that make an accepted ask owner-answerable."""
    first = asks[0]
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
