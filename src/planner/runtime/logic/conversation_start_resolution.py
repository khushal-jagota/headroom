"""What a Ticket's or the Chief's conversation starts on, resolved from its layers.

Three layers answer that question for a Ticket, in order: the Worker type's launch
defaults, the Ticket's own last-chosen values, and the explicit overrides a picker set
for this start. The Chief has two: its launch defaults and the same overrides.

The backend, the model and the reasoning effort resolve **as a unit**. A layer that
names a backend answers for all three: where it names no model or no reasoning effort,
the conversation starts on that backend's own native defaults rather than inheriting the
model of a different backend — a model name means nothing to a backend that has never
heard of it. Ticket creation already applies exactly this rule when an explicit backend
differs from the Worker type's default; this states the same rule for starts.

The Ticket layer takes part whenever a Ticket exists: ``tickets.employee_backend`` is
NOT NULL and is filled in when the Ticket is created, so no live Ticket is without one.
The three-layer shape is kept all the same, because the layers are three different facts
rather than one fact read three times: the Worker type's settings stay where the owner
last put them, while the Ticket's columns are kept up to date with what its conversation
actually runs on, so the two drift apart over the Ticket's life.

Nothing here reads a database, a settings file or a clock. The layers arrive as values
and one set of resolved values leaves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from planner.conversation.contracts import (
    FLOOR_DEFAULT_ACCESS,
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
)

# The role each agent is told to be. These are copies of the texts the old ACP kickoff
# layer sends, held here because that layer is deleted when the new conversation system
# replaces it and the text has to outlive it.
_ROLE_DIRECTIVE_PREFIX: Final = (
    "Start with the `panels` skill. It explains the system and is necessary, "
    "then drill through to your identity through the skills layers."
)
WORKER_ROLE_TEXT: Final = f"{_ROLE_DIRECTIVE_PREFIX} You are a ticket worker."
CHIEF_ROLE_TEXT: Final = f"{_ROLE_DIRECTIVE_PREFIX} You are a chief of staff."


@dataclass(frozen=True, slots=True)
class ConversationStartConfiguration:
    """One layer's whole answer to what a conversation runs on.

    A layer that has a backend has this shape. ``model`` and ``reasoning_effort`` are
    optional because a backend's own native defaults are a real answer, and null in a
    Ticket's columns means exactly that.
    """

    backend_key: ConversationBackendKey
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationStartOverrides:
    """The values a picker set for this start. An absent field is not a choice.

    Absent leaves the layer below its say. Because absent is spelled ``None``, an
    override cannot on its own put a conversation back on its backend's native default
    model: naming a different backend is what clears the model, and that is the only
    clearing this shape can express.
    """

    backend_key: ConversationBackendKey | None = None
    model: str | None = None
    reasoning_effort: str | None = None


NO_CONVERSATION_START_OVERRIDES: Final = ConversationStartOverrides()


@dataclass(frozen=True, slots=True)
class ConversationStartValues:
    """Everything a conversation is started with apart from its id.

    These are the concrete, already-resolved values the conversation contract asks
    callers to pass. Nothing here is left for the conversation system to guess.
    """

    backend_key: ConversationBackendKey
    model: str | None
    reasoning_effort: str | None
    role_materials: ConversationRoleMaterials
    workspace_folder: Path
    access: ConversationAccess


def worker_conversation_role_materials(ticket_id: str) -> ConversationRoleMaterials:
    """What a Ticket's worker is told to be, and the identity its process runs under."""
    return ConversationRoleMaterials(
        role_text=WORKER_ROLE_TEXT,
        identity_environment_variables=(
            ("PLAN_ACTOR", "worker"),
            ("PLAN_TICKET_ID", ticket_id),
        ),
    )


def chief_conversation_role_materials() -> ConversationRoleMaterials:
    """What the Chief is told to be, and the identity its process runs under."""
    return ConversationRoleMaterials(
        role_text=CHIEF_ROLE_TEXT,
        identity_environment_variables=(("PLAN_ACTOR", "chief"),),
    )


def _with_overrides(
    base: ConversationStartConfiguration,
    overrides: ConversationStartOverrides,
) -> ConversationStartConfiguration:
    """Lay the overrides over one resolved configuration, keeping the triple a unit."""
    if overrides.backend_key is not None and overrides.backend_key != base.backend_key:
        # A different backend is a different set of models, so nothing below carries
        # over: whatever the override does not name starts on the new backend's own
        # native defaults.
        return ConversationStartConfiguration(
            backend_key=overrides.backend_key,
            model=overrides.model,
            reasoning_effort=overrides.reasoning_effort,
        )
    return ConversationStartConfiguration(
        backend_key=base.backend_key,
        model=base.model if overrides.model is None else overrides.model,
        reasoning_effort=(
            base.reasoning_effort
            if overrides.reasoning_effort is None
            else overrides.reasoning_effort
        ),
    )


def resolve_worker_conversation_start(
    *,
    ticket_id: str,
    worker_type_launch_defaults: ConversationStartConfiguration,
    ticket_last_chosen: ConversationStartConfiguration | None,
    overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    workspace_folder: Path,
) -> ConversationStartValues:
    """Resolve what a Ticket's worker conversation starts on.

    The Worker type's launch defaults are the bottom layer. The Ticket's last-chosen
    values replace them whole when the Ticket has them — including their nulls, which
    mean the backend's native defaults and are a real answer, not a gap to fill from the
    layer below. The overrides then land on top, field by field, except that an override
    naming a different backend clears whatever it does not name itself.
    """
    base = worker_type_launch_defaults if ticket_last_chosen is None else ticket_last_chosen
    resolved = _with_overrides(base, overrides)
    return ConversationStartValues(
        backend_key=resolved.backend_key,
        model=resolved.model,
        reasoning_effort=resolved.reasoning_effort,
        role_materials=worker_conversation_role_materials(ticket_id),
        workspace_folder=workspace_folder,
        access=FLOOR_DEFAULT_ACCESS,
    )


def resolve_agent_conversation_start(
    *,
    chief_launch_defaults: ConversationStartConfiguration,
    overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    workspace_folder: Path,
) -> ConversationStartValues:
    """Resolve what the Chief's conversation starts on.

    The Chief has no per-Ticket layer to drift from its settings, so its defaults are
    the only layer under the overrides. The overrides land the same way they do for a
    worker: an override naming a different backend clears what it does not name itself.
    """
    resolved = _with_overrides(chief_launch_defaults, overrides)
    return ConversationStartValues(
        backend_key=resolved.backend_key,
        model=resolved.model,
        reasoning_effort=resolved.reasoning_effort,
        role_materials=chief_conversation_role_materials(),
        workspace_folder=workspace_folder,
        access=FLOOR_DEFAULT_ACCESS,
    )
