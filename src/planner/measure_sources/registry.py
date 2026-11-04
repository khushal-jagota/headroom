"""One composition point for the measure source daily participants."""

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.clock import Clock
from planner.core.config import Config
from planner.goals.scheduling import GoalCompletionParticipant
from planner.measure_sources.contracts import SourceRuntime
from planner.measure_sources.participants import SlotParticipant
from planner.measure_sources.runtime import Runtime
from planner.measure_sources.scheduling import SourceScheduleParticipant


def build_participants(
    config: Config,
    clock: Clock,
    *,
    workspace_admission: WorkspaceAdmission | None = None,
    source_runtime: SourceRuntime | None = None,
) -> tuple[SlotParticipant, ...]:
    return (
        SourceScheduleParticipant(
            config,
            clock,
            Runtime() if source_runtime is None else source_runtime,
            workspace_admission=workspace_admission,
        ),
        GoalCompletionParticipant(),
    )
