"""Starting a Ticket's conversation, sending into it, and resetting it.

This is the wiring between Panels' own state and the conversation contract. The pure
layering rules live in ``planner.runtime.logic.conversation_start_resolution``; what is
here is the part that touches a database, managed settings on disk, and the conversation
system itself.

Two shapes resolve values and never act: ``worker_resolve`` for a Ticket's worker and
``agent_resolve`` for the Chief. Where the settings and the workspace folder come from
is theirs to know, so a caller asks for values without knowing how they are found.

Three shapes act: starting a Ticket's conversation, sending text into it, and resetting
it. Each is awaited because the conversation system is.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final
from uuid import uuid4

from planner.conversation2.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationSystem,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryStarted,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime.logic.conversation_start_resolution import (
    NO_CONVERSATION_START_OVERRIDES,
    ConversationStartConfiguration,
    ConversationStartOverrides,
    ConversationStartValues,
    resolve_agent_conversation_start,
    resolve_worker_conversation_start,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket
from planner.worker_settings.service import (
    database_parent_from_connection,
    read_chief_settings,
    read_worker_launch_defaults_for_ticket_creation,
)
from planner.worker_types.configuration import configured_worker_type_registry
from planner.worker_types.registry import WorkerTypeRegistry

CONVERSATION_ID_PREFIX: Final = "conv_"


def _default_workspace_folder() -> Path:
    """The folder an agent runs in, from the server module that owns application paths.

    Imported at call time rather than at module level: the server module reaches this
    package through the Ticket routes, so a top-level import here would run while the
    server module is still being built.
    """
    from planner.core.server import resolve_employee_workspace_root

    return resolve_employee_workspace_root()


def new_conversation_id() -> str:
    """Mint the caller-owned id a conversation is known by everywhere afterwards."""
    return f"{CONVERSATION_ID_PREFIX}{uuid4().hex}"


def worker_resolve(
    conn: sqlite3.Connection,
    ticket: Ticket,
    overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    *,
    worker_type_registry: WorkerTypeRegistry | None = None,
    workspace_folder: Path | None = None,
) -> ConversationStartValues:
    """Everything a conversation for this Ticket's worker would be started with.

    It computes and nothing else: no conversation is created and no row is written, so
    asking twice costs nothing and changes nothing.
    """
    registry = worker_type_registry
    if registry is None:
        registry = configured_worker_type_registry()
    launch_defaults = read_worker_launch_defaults_for_ticket_creation(
        conn, registry, ticket.worker_type
    )
    return resolve_worker_conversation_start(
        ticket_id=ticket.id,
        worker_type_launch_defaults=ConversationStartConfiguration(
            backend_key=ConversationBackendKey(launch_defaults.employee_backend),
            model=launch_defaults.employee_launch_model,
            reasoning_effort=launch_defaults.employee_launch_reasoning_effort,
        ),
        ticket_last_chosen=ConversationStartConfiguration(
            backend_key=ConversationBackendKey(ticket.employee_backend),
            model=ticket.employee_launch_model,
            reasoning_effort=ticket.employee_launch_reasoning_effort,
        ),
        overrides=overrides,
        workspace_folder=(
            _default_workspace_folder() if workspace_folder is None else workspace_folder
        ),
    )


def agent_resolve(
    conn: sqlite3.Connection,
    overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    *,
    worker_type_registry: WorkerTypeRegistry | None = None,
    workspace_folder: Path | None = None,
) -> ConversationStartValues:
    """Everything a conversation for the Chief would be started with. Compute only."""
    registry = worker_type_registry
    if registry is None:
        registry = configured_worker_type_registry()
    database_parent = database_parent_from_connection(conn)
    if database_parent is None:
        raise RuntimeError("managed Chief settings need a database that lives in a folder")
    launch_defaults = read_chief_settings(database_parent, registry).launch_defaults
    return resolve_agent_conversation_start(
        chief_launch_defaults=ConversationStartConfiguration(
            backend_key=ConversationBackendKey(launch_defaults.employee_backend),
            model=launch_defaults.employee_launch_model,
            reasoning_effort=launch_defaults.employee_launch_reasoning_effort,
        ),
        overrides=overrides,
        workspace_folder=(
            _default_workspace_folder() if workspace_folder is None else workspace_folder
        ),
    )


async def start_ticket_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket: Ticket,
    values: ConversationStartValues,
    *,
    now: int,
) -> str:
    """Start a conversation for this Ticket and link the Ticket to it. Returns its id.

    The conversation is created first and the Ticket is pointed at it second, so the
    Ticket never names a conversation that does not exist. A crash between the two
    leaves a conversation record nobody points at, which is harmless and accepted.
    """
    conversation_id = new_conversation_id()
    await system.start_conversation(
        ConversationStartRequest(
            conversation_id=conversation_id,
            backend_key=values.backend_key,
            model=values.model,
            reasoning_effort=values.reasoning_effort,
            role_materials=values.role_materials,
            workspace_folder=values.workspace_folder,
            access=values.access,
        )
    )
    tickets_data.write_ticket_conversation_start(
        conn,
        ticket.id,
        conversation_id=conversation_id,
        backend=values.backend_key.value,
        model=values.model,
        reasoning_effort=values.reasoning_effort,
        now=now,
    )
    return conversation_id


async def send_to_ticket_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    text: str,
    *,
    sender_label: str,
    mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
    model_change: str | None = None,
    reasoning_effort_change: str | None = None,
    now: int,
) -> PromptDeliveryFate:
    """Send text into the Ticket's conversation and report the delivery's fate as it is.

    A message carrying a model or reasoning-effort change also updates the Ticket's
    last-chosen columns, but only when the delivery started. A held message applies its
    change when it later runs, and a refused one changes nothing at all, so recording
    either would record a model the conversation may never adopt.

    A Ticket with no conversation to send into is an error rather than a fate: there is
    no delivery to report on.
    """
    ticket = tickets_data.read_ticket(conn, ticket_id)
    conversation_id = ticket.employee_session_id
    if conversation_id is None:
        raise PlannerError(
            ErrorCode.not_found,
            "ticket has no conversation to send into",
            {"ticket_id": ticket_id},
        )
    fate = await system.send(
        conversation_id,
        text,
        sender_label=sender_label,
        mode=mode,
        model_change=model_change,
        reasoning_effort_change=reasoning_effort_change,
    )
    carries_a_change = model_change is not None or reasoning_effort_change is not None
    if carries_a_change and isinstance(fate, PromptDeliveryStarted):
        tickets_data.write_ticket_last_chosen_configuration(
            conn,
            ticket_id,
            model=ticket.employee_launch_model if model_change is None else model_change,
            reasoning_effort=(
                ticket.employee_launch_reasoning_effort
                if reasoning_effort_change is None
                else reasoning_effort_change
            ),
            now=now,
        )
    return fate


async def reset_ticket_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
) -> None:
    """Kill the Ticket's conversation and unlink it, so the next start is a fresh one.

    Killing stops the running turn and discards every message the conversation system
    was holding, so the old worker is silenced rather than merely interrupted — nothing
    it was queued to do runs after this. That is what the contract says ``kill`` is for,
    and it is why interrupting is not enough here: freeing the agent would let the held
    messages run.

    The last-chosen launch columns stay: they are what the next conversation starts
    from. A Ticket with no conversation has nothing to reset.
    """
    conversation_id = tickets_data.read_ticket(conn, ticket_id).employee_session_id
    if conversation_id is None:
        return
    await system.kill(conversation_id)
    tickets_data.clear_ticket_conversation_link(conn, ticket_id, now=now)
