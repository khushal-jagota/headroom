"""Opt-in synthetic provider observations for the existing connected browser host.

Validation, HTTP routes, canonical writes and prompt delivery remain production code.
No installed provider, executable, account or credential is inspected.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Never

from fastapi import FastAPI
from tests.support.executor_catalog import ScriptedCatalogObserver

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.contracts import (
    CatalogObservation,
    CatalogWorkspaceBinding,
    LaunchContext,
    PrerequisiteStatus,
)
from planner.tickets.contracts import TicketLocale


def _no_native_identity() -> Never:
    raise AssertionError("VPS browser fixture cannot request native identity")


class TicketModelObserver(ScriptedCatalogObserver):
    def __init__(self) -> None:
        super().__init__(_no_native_identity)
        self.models = ("gpt-browser-initial", "gpt-browser-proof")
        self.efforts = ()
        self.credentials = PrerequisiteStatus.available

    async def capture_context(
        self, backend_key: ConversationBackendKey, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext:
        return LaunchContext(
            "ticket-browser-scoped-context", TicketLocale.vps, backend_key, None,
            workspace, workspace.workspace_folder, "synthetic-cli", "1",
            "synthetic-provider-scope", None,
        )

    def verify_context(self, expected: LaunchContext) -> bool:
        return expected.context_ref == "ticket-browser-scoped-context"

    async def observe(
        self, backend_key: ConversationBackendKey, *, workspace: CatalogWorkspaceBinding,
        attempt_id: str, deadline: float,
    ) -> CatalogObservation:
        observation = await super().observe(
            backend_key, workspace=workspace, attempt_id=attempt_id, deadline=deadline
        )
        return replace(observation, credentials=self.credentials)


def install_ticket_model_controls(app: FastAPI) -> None:
    observer: TicketModelObserver | None = None

    @app.post("/api/test/ticket-model-catalog")
    async def configure(body: dict[str, bool]) -> dict[str, int]:
        nonlocal observer
        if observer is None:
            observer = TicketModelObserver()
            app.state.conversation.system.configure_destination_validation(observer)
        observer.credentials = (
            PrerequisiteStatus.unavailable
            if body.get("credentials_unavailable", False)
            else PrerequisiteStatus.available
        )
        return {"observations": observer.calls}
