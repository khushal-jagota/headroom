"""Sending a message to a Ticket's worker or to the Chief, and letting a conversation go.

This is the wiring between Panels' own state and the conversation contract. The pure
layering rules live in ``planner.runtime.logic.conversation_start_resolution``; what is
here is the part that touches a database, managed settings on disk, and the conversation
system itself.

Two shapes resolve values and never act: ``worker_resolve`` for a Ticket's worker and
``agent_resolve`` for the Chief. Where the settings and the workspace folder come from
is theirs to know, so a caller asks for values without knowing how they are found.

Two shapes act, and there is no third. **Sending** is the whole of talking to somebody: a
message names the conversation it is for, and one sent by a sender that has none is what
brings a conversation into being — on the values the message says it runs under, so the
message that makes a conversation never also has to change it. **Resetting** kills a
conversation and lets go of it, leaving an owner with none, which is the state sending
already knows how to answer.

Nothing here makes a conversation on its own. One made and not spoken into is one nobody
can see and nobody can send from, which is what a separate start door left behind.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationSystem,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import MessageContent
from planner.conversation.storage import ensure_started_conversation_record
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime.logic.conversation_start_resolution import (
    NO_CONVERSATION_START_OVERRIDES,
    ConversationStartConfiguration,
    ConversationStartOverrides,
    ConversationStartValues,
    resolve_agent_conversation_start,
    resolve_sprint_item_supervisor_conversation_start,
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

if TYPE_CHECKING:
    from planner.sprints.contracts import SprintItem

CONVERSATION_ID_PREFIX: Final = "conv_"


def _default_workspace_folder() -> Path:
    """The folder an agent runs in, from the server module that owns application paths.

    Imported at call time rather than at module level: the server module reaches this
    package through the Ticket routes, so a top-level import here would run while the
    server module is still being built.
    """
    from planner.core.server import resolve_worker_workspace_root

    return resolve_worker_workspace_root()


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

    A Ticket whose model column is null is a Ticket that has not chosen what it runs on:
    the column was left empty back when an empty one meant the backend's own model, and
    that is a value nobody picked. Such a Ticket does not answer for the launch
    configuration at all — its backend cannot be paired with a model chosen for another —
    so the Worker type's launch defaults answer whole, and the first conversation writes
    concrete values back onto the Ticket.
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
        ticket_last_chosen=(
            None
            if ticket.employee_launch_model is None
            else ConversationStartConfiguration(
                backend_key=ConversationBackendKey(ticket.employee_backend),
                model=ticket.employee_launch_model,
                reasoning_effort=ticket.employee_launch_reasoning_effort,
            )
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
    launch_defaults = read_chief_settings(database_parent).launch_defaults
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


def sprint_item_supervisor_resolve(
    item: SprintItem,
    overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    *,
    workspace_folder: Path | None = None,
) -> ConversationStartValues:
    launch = item.supervisor_launch_configuration
    return resolve_sprint_item_supervisor_conversation_start(
        sprint_item_id=item.id,
        launch_configuration=ConversationStartConfiguration(
            backend_key=launch.employee_backend,
            model=launch.employee_launch_model,
            reasoning_effort=launch.employee_launch_reasoning_effort,
        ),
        overrides=overrides,
        workspace_folder=(
            _default_workspace_folder() if workspace_folder is None else workspace_folder
        ),
    )


def _no_turn_to_steer_into() -> DeliveredMessage:
    """A steer aimed at nothing, said as the fate the contract already has for it."""
    return DeliveredMessage(
        conversation_id=None,
        fate=PromptDeliveryRefused(
            refusal_reason=PromptDeliveryRefusalReason.no_running_turn_to_steer_into
        ),
    )


@dataclass(frozen=True, slots=True)
class LinkedConversation:
    """The conversation an owner is now in, and whether this call is what made it.

    Two callers can decide to make one at the same moment — the readiness loop with a step
    to send, and a person typing into the panel — and only one link lands. The loser is
    handed the winner so its message still goes somewhere, and ``made_here`` is false so it
    knows to let go of the one it made rather than the one it was handed.
    """

    conversation_id: str
    made_here: bool


async def start_ticket_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket: Ticket,
    values: ConversationStartValues,
    *,
    conversation_id: str,
    now: int,
) -> LinkedConversation:
    """Start a conversation for this Ticket under a name its caller has already minted.

    The id is the caller's, as the conversation contract has it, because a caller can have
    put things under that name before there was a conversation to hold them — the files a
    first message carries are kept in the conversation's own folder, and they are kept
    before the send that would create it.

    The conversation is created first and the Ticket is pointed at it second, so the
    Ticket never names a conversation that does not exist. A crash between the two leaves a
    conversation record nobody points at, which is harmless and accepted. Losing the race
    for the link leaves the same thing, and that one the caller lets go of: it is a
    conversation that will never be spoken into, which is the whole of what this rule is
    against.
    """
    request = ConversationStartRequest(
        conversation_id=conversation_id,
        backend_key=values.backend_key,
        model=values.model,
        reasoning_effort=values.reasoning_effort,
        role_materials=values.role_materials,
        workspace_folder=values.workspace_folder,
        access=values.access,
    )
    await system.start_conversation(request)
    try:
        ensure_started_conversation_record(
            conn, resolve_conversation_start_request(request), created_at=now
        )
        linked = tickets_data.write_ticket_conversation_start(
            conn,
            ticket.id,
            conversation_id=conversation_id,
            backend=values.backend_key.value,
            model=values.model,
            reasoning_effort=values.reasoning_effort,
            now=now,
        )
    except BaseException as start_error:
        try:
            await system.kill(conversation_id)
        except BaseException as cleanup_error:
            start_error.add_note(
                f"cleanup also failed for conversation {conversation_id}: {cleanup_error}"
            )
        raise
    # Either this one landed or the Ticket already had one; both leave it naming a
    # conversation, and that is the one the caller has to send into.
    now_in = linked.conversation_id or conversation_id
    return LinkedConversation(conversation_id=now_in, made_here=now_in == conversation_id)


@dataclass(frozen=True, slots=True)
class DeliveredMessage:
    """What happened to a message, and which conversation it happened in.

    ``conversation_id`` is null only when the message was to create a conversation and did
    not land, because then there is no conversation: the one that would have held it went
    with it. A caller holding null has nothing to open and nothing to remember.
    """

    conversation_id: str | None
    fate: PromptDeliveryFate


async def send_to_ticket_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    content: MessageContent,
    *,
    conversation_id: str | None,
    created_conversation_id: str | None = None,
    runs_under: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    sender_label: str,
    mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
    sender_message_id: str | None = None,
    sent_at_unix_milliseconds: int | None = None,
    worker_type_registry: WorkerTypeRegistry | None = None,
    now: int,
    required_sprint_item_id: str | None = None,
) -> DeliveredMessage:
    """Send a message into this Ticket's conversation, making one if there is none yet.

    ``created_conversation_id`` is the name a conversation made here is given. It is the
    caller's because the caller may already have used it: the files a message carries are
    kept in its conversation's folder, and a first message's files are kept before there is
    a conversation. A send that does not create one never uses it.

    ``conversation_id`` is the conversation this message is for. Naming one is how a
    sender says which conversation it is looking at: the Ticket can be pointed at a
    different one by the time the send arrives — the readiness loop starts conversations
    too — and a message must land where the person was reading rather than wherever the
    Ticket has got to. A name that is not the Ticket's current conversation is refused, so
    a tab that missed a New cannot go on talking to a conversation that was killed.

    Passing none says the sender has no conversation, and this message is what brings one
    into being.

    ``runs_under`` is what the sender says this message is to run on, and an absent field
    is not a choice. It is one set of values because it is one thing said twice: to a
    conversation that does not exist yet it is what to create it on, and to one that does
    it is what to move it onto. That is what keeps the first message from carrying a
    change — there is nothing yet for it to change from — and a backend that takes its
    model when its process starts from having to start twice for one message.

    Creating and delivering are one act. If the delivery does not land, the conversation
    goes with it and the Ticket is left with none, which is the state it was in before.

    A message carrying a change also updates the Ticket's last-chosen columns, but only
    when the delivery started. A held message applies its change when it later runs, and a
    refused one changes nothing at all, so recording either would record a model the
    conversation may never adopt. That update names the conversation this send actually
    went into, so a Ticket pointed at a fresh conversation while the send was out keeps its
    own values.
    """
    ticket = tickets_data.read_ticket(conn, ticket_id)
    if required_sprint_item_id is not None:
        if ticket.sprint_item_id != required_sprint_item_id:
            raise PlannerError(
                ErrorCode.agent_forbidden,
                "the ticket is not a current child of this Sprint Item supervisor",
                {
                    "ticket_id": ticket_id,
                    "sprint_item_id": required_sprint_item_id,
                },
            )
        if ticket.conversation_id is None:
            raise PlannerError(
                ErrorCode.not_found,
                "the ticket has no current Worker conversation",
                {"ticket_id": ticket_id},
            )
    if conversation_id is None and ticket.conversation_id is None:
        if mode is PromptDeliveryMode.steer:
            # A steer is text for a turn that is already running, and there is no
            # conversation here, let alone a turn. Making one in order to refuse a steer
            # into it would leave a conversation nobody asked for.
            return _no_turn_to_steer_into()
        return await _make_a_conversation_and_send_into_it(
            system,
            conn,
            ticket,
            content,
            created_conversation_id=created_conversation_id,
            runs_under=runs_under,
            sender_label=sender_label,
            mode=mode,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            worker_type_registry=worker_type_registry,
            now=now,
        )
    # The sender had none but the Ticket has gained one — the readiness loop starts
    # conversations too. The message joins it rather than making a second, and what it says
    # it runs under arrives as the change it now is.
    sending_into = ticket.conversation_id if conversation_id is None else conversation_id
    if sending_into is None or sending_into != ticket.conversation_id:
        raise PlannerError(
            ErrorCode.not_found,
            "the ticket is not in that conversation",
            {"ticket_id": ticket_id, "conversation_id": conversation_id},
        )
    return await _send_into_the_conversation_the_ticket_is_in(
        system,
        conn,
        ticket_id,
        sending_into,
        content,
        runs_under=runs_under,
        sender_label=sender_label,
        mode=mode,
        sender_message_id=sender_message_id,
        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
        now=now,
    )


async def _send_into_the_conversation_the_ticket_is_in(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    sending_into: str,
    content: MessageContent,
    *,
    runs_under: ConversationStartOverrides,
    sender_label: str,
    mode: PromptDeliveryMode,
    sender_message_id: str | None,
    sent_at_unix_milliseconds: int | None,
    now: int,
) -> DeliveredMessage:
    """Deliver into a conversation that is already there, carrying what it changes.

    This is what a message does to a conversation it did not make: what it says it runs
    under arrives as the change it now is, because there is something to change from.

    A message carrying a change also updates the Ticket's last-chosen columns, but only
    when the delivery started. A held message applies its change when it later runs, and a
    refused one changes nothing at all, so recording either would record a model the
    conversation may never adopt. That update names the conversation this send actually
    went into, so a Ticket pointed at a fresh conversation while the send was out keeps its
    own values — and the value it keeps for a field this message did not change is read
    back now rather than taken from before the send, for the same reason.
    """
    fate = await system.send(
        sending_into,
        content,
        sender_label=sender_label,
        mode=mode,
        model_change=runs_under.model,
        reasoning_effort_change=runs_under.reasoning_effort,
        sender_message_id=sender_message_id,
        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
    )
    carries_a_change = runs_under.model is not None or runs_under.reasoning_effort is not None
    if carries_a_change and isinstance(fate, PromptDeliveryStarted):
        ticket = tickets_data.read_ticket(conn, ticket_id)
        tickets_data.write_ticket_last_chosen_configuration(
            conn,
            ticket_id,
            expected_conversation_id=sending_into,
            model=(ticket.employee_launch_model if runs_under.model is None else runs_under.model),
            reasoning_effort=(
                ticket.employee_launch_reasoning_effort
                if runs_under.reasoning_effort is None
                else runs_under.reasoning_effort
            ),
            now=now,
        )
    return DeliveredMessage(conversation_id=sending_into, fate=fate)


async def _make_a_conversation_and_send_into_it(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket: Ticket,
    content: MessageContent,
    *,
    created_conversation_id: str | None = None,
    runs_under: ConversationStartOverrides,
    sender_label: str,
    mode: PromptDeliveryMode,
    sender_message_id: str | None,
    sent_at_unix_milliseconds: int | None,
    worker_type_registry: WorkerTypeRegistry | None,
    now: int,
) -> DeliveredMessage:
    """Bring a conversation into being with the message that is its first, or neither.

    Every undo here names the conversation made here rather than asking the Ticket what it
    is pointing at. Delivering takes as long as spawning an agent takes, and a person can
    press New in that time — cutting the Ticket loose from whatever it names now would kill
    a conversation this send never touched.

    A delivery can raise rather than come back with a fate — content with nothing in it, a
    steer carrying a change, an adapter falling over in a way the contract has no refusal
    for — and every one of those leaves the same half-made conversation as a refusal does.
    So the undo is on the way out, not on the answer.
    """
    making = created_conversation_id or new_conversation_id()
    linked = await start_ticket_conversation(
        system,
        conn,
        ticket,
        worker_resolve(conn, ticket, runs_under, worker_type_registry=worker_type_registry),
        conversation_id=making,
        now=now,
    )
    if not linked.made_here:
        # The race went the other way while this was creating: the Ticket was given a
        # conversation by somebody else, and the one made here will never be spoken into.
        # It is let go of rather than left lying about, which is the whole rule. The
        # message then joins the Ticket's conversation on the terms every message joins
        # one on — what it says it runs under is a change to it, because it is somebody
        # else's conversation, started on values of its own.
        await _let_go_of_a_conversation_that_was_never_spoken_in(
            system, conn, ticket.id, making, now=now
        )
        return await _send_into_the_conversation_the_ticket_is_in(
            system,
            conn,
            ticket.id,
            linked.conversation_id,
            content,
            runs_under=runs_under,
            sender_label=sender_label,
            mode=mode,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            now=now,
        )
    # From here the conversation is this call's own: it was made here, on the values this
    # message says it runs under, so the message has nothing to change and carries none.
    try:
        fate = await system.send(
            making,
            content,
            sender_label=sender_label,
            mode=mode,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
        )
    except BaseException:
        await _let_go_of_a_conversation_that_was_never_spoken_in(
            system, conn, ticket.id, making, now=now
        )
        raise
    if isinstance(fate, PromptDeliveryRefused):
        await _let_go_of_a_conversation_that_was_never_spoken_in(
            system, conn, ticket.id, making, now=now
        )
        return DeliveredMessage(conversation_id=None, fate=fate)
    return DeliveredMessage(conversation_id=making, fate=fate)


async def _let_go_of_a_conversation_that_was_never_spoken_in(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    conversation_id: str,
    *,
    now: int,
) -> None:
    """Undo a conversation whose first message did not land, and only that one."""
    await system.kill(conversation_id)
    tickets_data.remove_ticket_conversation_start(
        conn, ticket_id, conversation_id=conversation_id, now=now
    )


def read_agent_conversation(conn: sqlite3.Connection, agent_key: str) -> str | None:
    """Which conversation this agent is currently having, or none.

    An agent nobody has spoken to has no row yet, and one that has been reset has a row
    holding nothing. Both are the same answer to the only question asked here, so both
    read as none.
    """
    row = conn.execute(
        "SELECT conversation_id FROM agents WHERE agent_key = ?",
        (agent_key,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def read_agent_conversations(
    conn: sqlite3.Connection, agent_keys: Collection[str]
) -> dict[str, str]:
    """Read the current conversation links for a small roster of non-Ticket agents.

    An absent row and a row with no conversation both mean that agent has no current
    conversation, so neither appears in the answer. The caller owns the roster and
    supplies its own quiet defaults for those agents.
    """
    keys = tuple(dict.fromkeys(agent_keys))
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT agent_key, conversation_id FROM agents "
        f"WHERE agent_key IN ({placeholders}) AND conversation_id IS NOT NULL",
        keys,
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


async def start_agent_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    agent_key: str,
    values: ConversationStartValues,
    *,
    conversation_id: str,
    required_sprint_item_id: str | None = None,
) -> LinkedConversation:
    """Start a conversation for an agent that is not a Ticket, under a minted name.

    The same two steps a Ticket takes, in the same order: the conversation is created
    first and the agent is pointed at it second, so the agent never names a conversation
    that does not exist. The link lands only on an agent that has none, for the reason a
    Ticket's does — an agent has one conversation, and the loser of a race would otherwise
    be left live and unreachable.
    """
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
    with conn:
        if required_sprint_item_id is None:
            conn.execute(
                "INSERT INTO agents (agent_key, conversation_id) VALUES (?, ?) "
                "ON CONFLICT(agent_key) DO UPDATE SET conversation_id = excluded.conversation_id "
                "WHERE agents.conversation_id IS NULL",
                (agent_key, conversation_id),
            )
        else:
            linked = conn.execute(
                "UPDATE agents SET conversation_id=? WHERE agent_key=? "
                "AND conversation_id IS NULL AND EXISTS ("
                "SELECT 1 FROM sprint_items WHERE id=? AND kind='normal' "
                "AND supervisor_agent_key=agents.agent_key)",
                (conversation_id, agent_key, required_sprint_item_id),
            )
            if linked.rowcount == 0 and read_agent_conversation(conn, agent_key) is None:
                await system.kill(conversation_id)
                raise PlannerError(
                    ErrorCode.not_found,
                    "Sprint Item supervisor no longer exists",
                    {"sprint_item_id": required_sprint_item_id},
                )
    now_in = read_agent_conversation(conn, agent_key) or conversation_id
    return LinkedConversation(conversation_id=now_in, made_here=now_in == conversation_id)


async def send_to_agent_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    agent_key: str,
    content: MessageContent,
    values: ConversationStartValues,
    *,
    conversation_id: str | None,
    created_conversation_id: str | None = None,
    runs_under: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    sender_label: str,
    mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
    sender_message_id: str | None = None,
    sent_at_unix_milliseconds: int | None = None,
    required_sprint_item_id: str | None = None,
) -> DeliveredMessage:
    """Send a message into this agent's conversation, making one if there is none yet.

    The Ticket door's rules, for an agent that is not a Ticket: the message names the
    conversation it is for and is refused if that is not the one the agent is in, and a
    message sent with no conversation brings one into being on the values it says it runs
    under. If it does not land, the conversation goes with it.
    """
    in_now = read_agent_conversation(conn, agent_key)
    if conversation_id is None and in_now is None:
        if mode is PromptDeliveryMode.steer:
            return _no_turn_to_steer_into()
        making = created_conversation_id or new_conversation_id()
        linked = await start_agent_conversation(
            system,
            conn,
            agent_key,
            values,
            conversation_id=making,
            required_sprint_item_id=required_sprint_item_id,
        )
        if linked.made_here:
            # Made here, on the values this message says it runs under, so the message has
            # nothing to change and carries none.
            try:
                fate = await system.send(
                    making,
                    content,
                    sender_label=sender_label,
                    mode=mode,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                )
            except BaseException:
                await _let_go_of_an_agent_conversation(system, conn, agent_key, making)
                raise
            if isinstance(fate, PromptDeliveryRefused):
                await _let_go_of_an_agent_conversation(system, conn, agent_key, making)
                return DeliveredMessage(conversation_id=None, fate=fate)
            return DeliveredMessage(conversation_id=making, fate=fate)
        # The race went the other way. The conversation made here will never be spoken
        # into, so it is let go of, and the message joins the agent's on the ordinary
        # terms — what it says it runs under is a change to a conversation it did not make.
        await _let_go_of_an_agent_conversation(system, conn, agent_key, making)
        in_now = linked.conversation_id
    sending_into = in_now if conversation_id is None else conversation_id
    if sending_into is None or sending_into != in_now:
        raise PlannerError(
            ErrorCode.not_found,
            "the agent is not in that conversation",
            {"agent_key": agent_key, "conversation_id": conversation_id},
        )
    fate = await system.send(
        sending_into,
        content,
        sender_label=sender_label,
        mode=mode,
        model_change=runs_under.model,
        reasoning_effort_change=runs_under.reasoning_effort,
        sender_message_id=sender_message_id,
        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
    )
    return DeliveredMessage(conversation_id=sending_into, fate=fate)


async def _let_go_of_an_agent_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    agent_key: str,
    conversation_id: str,
) -> None:
    """Undo a conversation nothing was ever said in, and only that one.

    The named conversation is the one this call made. The unlink names it too, so an agent
    that is pointing at somebody else's — the race went the other way — is left alone.
    """
    await system.kill(conversation_id)
    with conn:
        conn.execute(
            "UPDATE agents SET conversation_id = NULL WHERE agent_key = ? AND conversation_id = ?",
            (agent_key, conversation_id),
        )


async def reset_agent_conversation(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    agent_key: str,
) -> None:
    """Kill this agent's conversation and unlink it, so the next start is a fresh one.

    Killing rather than interrupting, for the reason a Ticket's reset gives: freeing the
    agent would let the messages it was holding run, and starting again must not be the
    thing that finally delivers them.

    The agent's row stays and its conversation is what is let go, because the agent did
    not stop existing. The unlink names the conversation that was killed, so an agent
    already pointed at a newer one is left pointing at it.

    Nothing is deleted here — not the conversation's record and not the files its messages
    carry. Both outlive the reset on purpose: the record still holds those messages, so a
    file removed now would turn a picture somebody sent into a picture nobody can see.
    """
    conversation_id = read_agent_conversation(conn, agent_key)
    if conversation_id is None:
        return
    await system.kill(conversation_id)
    with conn:
        conn.execute(
            "UPDATE agents SET conversation_id = NULL WHERE agent_key = ? AND conversation_id = ?",
            (agent_key, conversation_id),
        )


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

    The unlink names the conversation that was killed, so a Ticket already pointed at a
    newer one is left pointing at it: only the conversation this call silenced is the one
    it may cut loose.

    Nothing is deleted here — not the conversation's record and not the files its messages
    carry. Both outlive the reset on purpose, for the reason
    ``planner.conversation.message_files`` gives: the record still holds those messages.
    """
    conversation_id = tickets_data.read_ticket(conn, ticket_id).conversation_id
    if conversation_id is None:
        return
    await system.kill(conversation_id)
    tickets_data.clear_ticket_conversation_link(
        conn,
        ticket_id,
        expected_conversation_id=conversation_id,
        now=now,
    )
