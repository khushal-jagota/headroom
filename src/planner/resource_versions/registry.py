"""The single domain composition point for generic resource versions."""

from __future__ import annotations

from planner.environments.resource_versions import DeploymentResources
from planner.feedback.resource_versions import SOURCE_TRIGGERS as FEEDBACK_SOURCES
from planner.feedback.resource_versions import FeedbackResources
from planner.managers.resource_versions import ManagerDocumentResources
from planner.resource_versions.contracts import ResourceProvider
from planner.tickets.workspace.resource_versions import SOURCE_TRIGGERS as TICKET_SOURCES
from planner.tickets.workspace.resource_versions import TicketWorkspaceResources
from planner.worker_settings.resource_versions import SOURCE_TRIGGERS as CHIEF_SOURCES
from planner.worker_settings.resource_versions import ChiefResources
from planner.worker_types.resource_versions import WorkerTypeResources

PROVIDERS: tuple[ResourceProvider, ...] = (
    FeedbackResources(),
    TicketWorkspaceResources(),
    ManagerDocumentResources(),
    ChiefResources(),
    WorkerTypeResources(),
    DeploymentResources(),
)
SOURCE_TRIGGERS = (*FEEDBACK_SOURCES, *CHIEF_SOURCES, *TICKET_SOURCES)
