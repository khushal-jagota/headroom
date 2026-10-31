"""Opt-in scripted model lists for the existing browser host."""

from dataclasses import replace

from fastapi import FastAPI
from tests.support.executor_catalog import ScriptedBackendSnapshots

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendIdentity, BackendIdentityStatus, BackendSnapshot


class TicketModelSnapshots(ScriptedBackendSnapshots):
    credentials_unavailable = False

    async def snapshot(
        self,
        backend_key: ConversationBackendKey,
        *,
        refresh: bool = False,
        discover_update_advisory: bool = True,
    ) -> BackendSnapshot:
        result = await super().snapshot(
            backend_key, refresh=refresh, discover_update_advisory=discover_update_advisory
        )
        return replace(
            result,
            identity=BackendIdentity(
                BackendIdentityStatus.unauthenticated
                if self.credentials_unavailable
                else BackendIdentityStatus.authenticated
            ),
        )


def install_ticket_model_controls(app: FastAPI) -> None:
    snapshots: TicketModelSnapshots | None = None

    @app.post("/api/test/ticket-model-catalog")
    async def configure(body: dict[str, bool]) -> dict[str, int]:
        nonlocal snapshots
        if snapshots is None:
            snapshots = TicketModelSnapshots()
            snapshots.models = ("gpt-browser-initial", "gpt-browser-proof")
            snapshots.efforts = ()
            object.__setattr__(app.state.conversation, "backend_snapshots", snapshots)
        snapshots.credentials_unavailable = body.get("credentials_unavailable", False)
        return {"observations": snapshots.calls}
