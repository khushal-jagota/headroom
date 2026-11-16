"""Only Mac Ticket children cross the executor seam; VPS roles stay local."""

from __future__ import annotations

from dataclasses import dataclass

from planner.agent_identity.data import canonical_principal
from planner.agent_identity.launch import AuthenticatedLocalChild
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    RetainedBackendChildFactory,
    RetainedBackendRun,
)
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.contracts import PrincipalKind
from planner.core.db import connect
from planner.execution_identity import ChildReference
from planner.machines.contracts import HOME_MACHINE_ID


@dataclass(frozen=True, slots=True)
class LocaleBackendChildFactory:
    db_path: str
    local_factory: BackendChildFactory
    remote_factory: RetainedBackendChildFactory

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return self.remote_factory.retained_runs()

    def recover(
        self, reference: ChildReference, *, event_sink: BackendEventSink | None = None
    ) -> BackendChild | None:
        """Recover only retained authority, without allocation or native execution."""
        return self.remote_factory.recover(reference, event_sink=event_sink)

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> BackendChild:
        conn = connect(self.db_path)
        try:
            principal = canonical_principal(conn, resolved_start.conversation_id)
            row = (
                conn.execute(
                    "SELECT machine_id FROM tickets WHERE id=?", (principal.id,)
                ).fetchone()
                if principal is not None and principal.kind is PrincipalKind.ticket
                else None
            )
            remote = row is not None and row["machine_id"] != HOME_MACHINE_ID
        finally:
            conn.close()
        factory = self.remote_factory if remote else self.local_factory
        child = factory(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )
        return child if remote else AuthenticatedLocalChild(child, self.db_path)
