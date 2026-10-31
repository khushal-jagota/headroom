"""Starting, sending into, and resetting a Ticket's conversation, against the fake.

The conversation system here is the in-memory reference implementation of the contract,
and the database is a real SQLite file, so every claim about what was written is read
back out of the Ticket row.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from typing import Literal

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import (
    AddressedPromptDeliveryReceipt,
    ConversationAccess,
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationTurnReference,
    DeploymentRecoveryReference,
    HeldPrompt,
    HeldPromptPromotionFate,
    HeldPromptPromotionMode,
    NativeMessageOperation,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    WorkspacePathPolicy,
)
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.model_resolution.contracts import SelectionKind
from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.contracts import Principal
from planner.core.errors import PlannerError
from planner.projects.data import create_project, update_project
from planner.runtime.conversation_start import (
    new_conversation_id,
    reset_ticket_conversation,
    send_to_ticket_conversation,
    start_ticket_conversation,
    worker_resolve,
)
from planner.runtime.logic.conversation_start_resolution import (
    ConversationStartOverrides,
    ConversationStartValues,
    worker_conversation_role_materials,
)
from planner.tickets.contracts import Ticket
from planner.tickets.data import create_ticket, read_ticket
from planner.worker_types.store import read_definition, write_definition

_WORKSPACE = Path("/tmp/panels-workspace")


@pytest.fixture
def ticket(tmp_db: Connection) -> Ticket:
    return create_ticket(
        tmp_db,
        title="A conversation ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )


def _values(ticket_id: str) -> ConversationStartValues:
    return ConversationStartValues(
        backend_key=ConversationBackendKey.claude,
        model="opus",
        reasoning_effort="high",
        role_materials=worker_conversation_role_materials(ticket_id),
        workspace_folder=_WORKSPACE,
        access=ConversationAccess.full,
    )


def _history(conn: Connection, ticket_id: str) -> list[str]:
    return [
        str(row["conversation_id"])
        for row in conn.execute(
            "SELECT conversation_id FROM ticket_conversations "
            "WHERE ticket_id = ? ORDER BY conversation_id",
            (ticket_id,),
        )
    ]


class _LinkWatchingConversationSystem:
    """The fake, plus a note of what the Ticket's link said the moment it existed."""

    def __init__(
        self, system: InMemoryConversationSystem, conn: Connection, ticket_id: str
    ) -> None:
        self._system = system
        self._conn = conn
        self._ticket_id = ticket_id
        self.start_requests: list[ConversationStartRequest] = []
        self.link_when_the_conversation_existed: str | None = "not observed"

    @property
    def workspace_admission(self) -> WorkspaceAdmission:
        return self._system.workspace_admission

    def workspace_activity(self) -> Literal["idle", "busy", "unknown"]:
        return self._system.workspace_activity()

    async def run_with_workspace_admission(
        self, operation: Callable[[], None], *, require_idle: bool,
    ) -> bool:
        return await self._system.run_with_workspace_admission(operation, require_idle=require_idle)

    async def run_if_idle(self, conversation_id: str, operation: Callable[[], None]) -> bool:
        return await self._system.run_if_idle(conversation_id, operation)

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        self.start_requests.append(request)
        await self._system.start_conversation(request)
        self.link_when_the_conversation_existed = read_ticket(
            self._conn, self._ticket_id
        ).conversation_id

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        owner_read_through_sequence: int | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recovery_references: tuple[DeploymentRecoveryReference, ...] = (),
        native_message_operation: NativeMessageOperation | None = None,
    ) -> PromptDeliveryFate:
        return await self._system.send(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            reasoning_effort_is_set=reasoning_effort_is_set,
            selection_kind=selection_kind,
            owner_read_through_sequence=owner_read_through_sequence,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recovery_references=recovery_references,
            native_message_operation=native_message_operation,
        )

    async def send_with_receipt(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        reasoning_effort_is_set: bool = False,
        selection_kind: SelectionKind | None = None,
        owner_read_through_sequence: int | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
        recovery_references: tuple[DeploymentRecoveryReference, ...] = (),
        record_refusal: bool = False,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> AddressedPromptDeliveryReceipt:
        return await self._system.send_with_receipt(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            reasoning_effort_is_set=reasoning_effort_is_set,
            selection_kind=selection_kind,
            owner_read_through_sequence=owner_read_through_sequence,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            recovery_references=recovery_references,
            record_refusal=record_refusal,
            native_message_operation=native_message_operation,
        )

    async def record_message_to_owner(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        sender: Principal,
        recipient: Principal,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        credit_explicit_reply: bool = True,
        native_message_operation: NativeMessageOperation | None = None,
    ) -> None:
        await self._system.record_message_to_owner(
            conversation_id,
            content,
            sender_label=sender_label,
            sender=sender,
            recipient=recipient,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            credit_explicit_reply=credit_explicit_reply,
            native_message_operation=native_message_operation,
        )

    async def active_turn_reference(self, conversation_id: str) -> ConversationTurnReference | None:
        return await self._system.active_turn_reference(conversation_id)

    async def turn_expects_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> bool:
        return await self._system.turn_expects_reply(turn, recipient)

    async def record_explicit_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> None:
        await self._system.record_explicit_reply(turn, recipient)

    async def interrupt(self, conversation_id: str) -> None:
        await self._system.interrupt(conversation_id)

    async def held_prompts(self, conversation_id: str) -> tuple[HeldPrompt, ...]:
        return await self._system.held_prompts(conversation_id)

    async def promote_held_prompt(
        self,
        conversation_id: str,
        held_prompt_id: str,
        mode: HeldPromptPromotionMode,
    ) -> HeldPromptPromotionFate | None:
        return await self._system.promote_held_prompt(conversation_id, held_prompt_id, mode)

    async def discard_held_prompt(self, conversation_id: str, held_prompt_id: str) -> bool:
        return await self._system.discard_held_prompt(conversation_id, held_prompt_id)

    async def retirement_is_confirmed(self, conversation_id: str) -> bool:
        return await self._system.retirement_is_confirmed(conversation_id)

    async def kill(self, conversation_id: str) -> None:
        await self._system.kill(conversation_id)

    async def is_running(self, conversation_id: str) -> bool:
        return await self._system.is_running(conversation_id)

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return await self._system.has_pending_permission_ask(conversation_id)

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return await self._system.has_pending_user_input(conversation_id)


class _WhoseFirstWriteFails(_LinkWatchingConversationSystem):
    """The fake, with every conversation it makes unable to take a write.

    Armed as the conversation comes into being, because that is the only moment between a
    first message making one and that message being written to it.
    """

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self._system.start_conversation(request)
        self._system.arm_backend_write_failure(request.conversation_id)


async def _started(
    system: object,
    conn: Connection,
    ticket: Ticket,
    values: ConversationStartValues,
    *,
    now: int,
) -> str:
    """The conversation a Ticket is in after one is started for it."""
    conversation_id = new_conversation_id()
    linked = await start_ticket_conversation(
        system,  # type: ignore[arg-type]
        conn,
        ticket,
        values,
        conversation_id=conversation_id,
        now=now,
    )
    return linked.conversation_id


async def _sent(
    system: object,
    conn: Connection,
    ticket_id: str,
    text: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    mode: PromptDeliveryMode = PromptDeliveryMode.queue,
    sender_label: str = "loop",
    now: int,
) -> PromptDeliveryFate:
    """A message into the conversation the Ticket is in, and what became of it."""
    delivered = await send_to_ticket_conversation(
        system,  # type: ignore[arg-type]
        conn,
        ticket_id,
        text_message_content(text),
        conversation_id=read_ticket(conn, ticket_id).conversation_id,
        runs_under=ConversationStartOverrides(model=model, reasoning_effort=reasoning_effort),
        sender_label=sender_label,
        mode=mode,
        now=now,
    )
    return delivered.fate


def test_creation_launch_choices_reach_the_first_worker_session(
    tmp_db: Connection,
) -> None:
    definition = read_definition(tmp_db, "coding")
    write_definition(
        tmp_db,
        replace(
            definition,
            worker_profile=replace(
                definition.worker_profile,
                default_backend="codex",
                default_model="gpt-5.6-sol",
                default_reasoning_effort="medium",
            ),
        ),
        now=1,
    )
    chosen = create_ticket(
        tmp_db,
        title="Start with the requested launch choices",
        principal=OWNER_PRINCIPAL,
        now=2,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="claude",
        employee_launch_model="opus",
        employee_launch_reasoning_effort="high",
    )
    stored = read_ticket(tmp_db, chosen.id)
    assert stored.conversation_id is None
    assert stored.employee_backend == "claude"
    assert stored.employee_launch_model == "opus"
    assert stored.employee_launch_reasoning_effort == "high"

    async def exercise() -> None:
        system = InMemoryConversationSystem()
        watching = _LinkWatchingConversationSystem(system, tmp_db, chosen.id)
        delivered = await send_to_ticket_conversation(
            watching,
            tmp_db,
            chosen.id,
            text_message_content("Work the first step"),
            conversation_id=None,
            sender_label="loop",
            now=3,
        )

        assert isinstance(delivered.fate, PromptDeliveryStarted)
        conversation_id = delivered.conversation_id
        assert conversation_id is not None
        assert len(watching.start_requests) == 1
        first_start = watching.start_requests[0]
        assert first_start.backend_key is ConversationBackendKey.claude
        assert first_start.model == "opus"
        assert first_start.reasoning_effort == "high"
        assert system.backend_model(conversation_id) == "opus"
        assert system.backend_reasoning_effort(conversation_id) == "high"
        assert len(system.backend_prompt_writes(conversation_id)) == 1
        persisted = tmp_db.execute(
            "SELECT backend_key,model,reasoning_effort FROM conversations "
            "WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        assert persisted is not None
        assert tuple(persisted) == ("claude", "opus", "high")
        assert _history(tmp_db, chosen.id) == [conversation_id]

    asyncio.run(exercise())


def test_a_change_on_a_refused_delivery_records_nothing(tmp_db: Connection, ticket: Ticket) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        conversation_id = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        system.arm_backend_write_failure(conversation_id)

        fate = await _sent(system, tmp_db, ticket.id, "work the step", model="sonnet", now=20)

        assert isinstance(fate, PromptDeliveryRefused)
        assert fate.refusal_reason is PromptDeliveryRefusalReason.write_to_backend_failed
        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == ticket.employee_launch_model
        assert after.updated_at == 10

    asyncio.run(exercise())


def test_resetting_stops_the_conversation_and_unlinks_it(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        conversation_id = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        await system.send(
            conversation_id,
            text_message_content("running work"),
            sender_label="loop",
        )

        await reset_ticket_conversation(system, tmp_db, ticket.id, now=30)

        assert system.backend_cancellations(conversation_id) == 1
        assert await system.is_running(conversation_id) is False
        after = read_ticket(tmp_db, ticket.id)
        assert after.conversation_id is None
        assert _history(tmp_db, ticket.id) == [conversation_id]
        # Resolved conversation values never replace the Ticket's requested intent.
        assert after.employee_backend == ticket.employee_backend
        assert after.employee_launch_model == ticket.employee_launch_model
        assert after.employee_launch_reasoning_effort == ticket.employee_launch_reasoning_effort
        assert after.updated_at == 30

    asyncio.run(exercise())


def test_an_existing_conversation_keeps_its_workspace_after_the_project_folder_changes(
    tmp_db: Connection, ticket: Ticket, tmp_path: Path
) -> None:
    async def exercise() -> None:
        original_folder = tmp_path / "original"
        replacement_folder = tmp_path / "replacement"
        original_folder.mkdir()
        replacement_folder.mkdir()
        project = create_project(
            tmp_db,
            name="Moved Project",
            folder_path=original_folder,
            now=2,
        )
        tmp_db.execute("UPDATE tickets SET project_id = ? WHERE id = ?", (project.id, ticket.id))
        system = InMemoryConversationSystem()
        conversation_id = await _started(
            system,
            tmp_db,
            read_ticket(tmp_db, ticket.id),
            worker_resolve(tmp_db, read_ticket(tmp_db, ticket.id)),
            now=3,
        )

        update_project(tmp_db, project.id, folder_path=replacement_folder, now=4)
        fate = await _sent(system, tmp_db, ticket.id, "continue", now=5)

        assert isinstance(fate, PromptDeliveryStarted)
        stored_workspace = tmp_db.execute(
            "SELECT workspace_folder FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        assert stored_workspace is not None
        assert Path(str(stored_workspace["workspace_folder"])) == original_folder.resolve()

    asyncio.run(exercise())


def test_worker_resolve_preserves_disabled_history_but_rejects_new_disabled_override(
    tmp_db: Connection, ticket: Ticket
) -> None:
    chosen = worker_resolve(tmp_db, ticket, workspace_folder=_WORKSPACE)
    write_model_enablement(tmp_db, chosen.backend_key, chosen.model, False)
    write_model_enablement(tmp_db, chosen.backend_key, "disabled-alternative", False)
    tmp_db.commit()

    assert worker_resolve(tmp_db, ticket, workspace_folder=_WORKSPACE) == chosen
    with pytest.raises(PlannerError, match="disabled-alternative"):
        worker_resolve(
            tmp_db,
            ticket,
            ConversationStartOverrides(model="disabled-alternative"),
            workspace_folder=_WORKSPACE,
        )
    assert read_ticket(tmp_db, ticket.id) == ticket


def test_mac_project_path_is_resolved_on_the_execution_host(
    tmp_db: Connection, tmp_path: Path, ticket: Ticket
) -> None:
    target = tmp_path / "vps-target"
    target.mkdir()
    alias = tmp_path / "checkout"
    alias.symlink_to(target, target_is_directory=True)
    project = create_project(tmp_db, name="Mac project", folder_path=alias, now=1)
    tmp_db.execute(
        "UPDATE tickets SET project_id = ?, locale = 'macos' WHERE id = ?",
        (project.id, ticket.id),
    )
    ticket = read_ticket(tmp_db, ticket.id)
    values = worker_resolve(tmp_db, ticket)
    assert values.workspace_folder == alias
    assert values.workspace_path_policy is WorkspacePathPolicy.native_absolute
    # The checkout may not exist on the VPS. Starting work must retain the Mac path.
    update_project(tmp_db, project.id, folder_path=tmp_path / "mac-only", now=2)
    values = worker_resolve(tmp_db, ticket)
    system = InMemoryConversationSystem()
    conversation_id = asyncio.run(_started(system, tmp_db, ticket, values, now=3))
    row = tmp_db.execute(
        "SELECT workspace_folder FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    assert row[0] == str(tmp_path / "mac-only")
