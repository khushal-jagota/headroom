"""The single domain composition point for generic resource versions."""

from __future__ import annotations

from planner.environments.resource_versions import DeploymentResources
from planner.feedback.resource_versions import SOURCE_TRIGGERS as FEEDBACK_SOURCES
from planner.feedback.resource_versions import FeedbackResources
from planner.managers.resource_versions import ManagerDocumentResources
from planner.measure_sources.resource_versions import SOURCE_TRIGGERS as MEASURE_SOURCES
from planner.measure_sources.resource_versions import MeasureResources
from planner.resource_versions.contracts import ResourceProvider
from planner.tickets.workspace.resource_versions import SOURCE_TRIGGERS as TICKET_SOURCES
from planner.tickets.workspace.resource_versions import TicketWorkspaceResources
from planner.worker_settings.resource_versions import SOURCE_TRIGGERS as CHIEF_SOURCES
from planner.worker_settings.resource_versions import ChiefResources
from planner.worker_types.resource_versions import WorkerTypeResources

PROVIDERS: tuple[ResourceProvider, ...] = (
    MeasureResources(),
    FeedbackResources(),
    TicketWorkspaceResources(),
    ManagerDocumentResources(),
    ChiefResources(),
    WorkerTypeResources(),
    DeploymentResources(),
)
BOUNDED_WORKSPACE_SOURCES = (*FEEDBACK_SOURCES, *CHIEF_SOURCES, *TICKET_SOURCES)
SOURCE_TRIGGERS = (*BOUNDED_WORKSPACE_SOURCES, *MEASURE_SOURCES)
