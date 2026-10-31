"""One composition point for the ordinary Ticket and source daily participants."""

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.clock import Clock
from planner.core.config import Config
from planner.measure_sources.runtime import Runtime
from planner.measure_sources.scheduling import SourceScheduleParticipant
from planner.scheduled_tickets.participants import SlotParticipant, TicketScheduleParticipant


def build_participants(
    config: Config, clock: Clock, *, workspace_admission: WorkspaceAdmission | None = None,
) -> tuple[SlotParticipant, ...]:
    return (
        TicketScheduleParticipant(),
        SourceScheduleParticipant(
            config, clock, Runtime(), workspace_admission=workspace_admission
        ),
    )
