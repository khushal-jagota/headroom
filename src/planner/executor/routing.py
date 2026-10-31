"""Only Mac Ticket children cross the executor seam; VPS roles stay local."""

from __future__ import annotations

from dataclasses import dataclass

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    RetainedBackendChildFactory,
    RetainedBackendRun,
)
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.db import connect
from planner.execution_identity import ChildReference
from planner.executor.worker_identity import ticket_id_of


@dataclass(frozen=True, slots=True)
class LocaleBackendChildFactory:
    db_path: str
    local_factory: BackendChildFactory
    remote_factory: RetainedBackendChildFactory

    def retained_runs(self) -> tuple[RetainedBackendRun, ...]:
        return self.remote_factory.retained_runs()

    def recover(self, reference: ChildReference) -> BackendChild | None:
        """Recover only retained authority, without allocation or native execution."""
        return self.remote_factory.recover(reference)

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> BackendChild:
        ticket_id = ticket_id_of(resolved_start)
        remote = False
        if ticket_id:
            conn = connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT locale FROM tickets WHERE id=?", (ticket_id,)
                ).fetchone()
                remote = row is not None and row["locale"] == "macos"
            finally:
                conn.close()
        factory = self.remote_factory if remote else self.local_factory
        return factory(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )
