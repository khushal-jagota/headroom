"""Only Mac Ticket children cross the executor seam; VPS roles stay local."""

from __future__ import annotations

from dataclasses import dataclass

from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
)
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.db import connect
from planner.executor.contracts import ChildReference
from planner.executor.remote import RemoteBackendChild, RemoteBackendChildFactory
from planner.executor.store import ExecutorStore


@dataclass(frozen=True, slots=True)
class LocaleBackendChildFactory:
    db_path: str
    local_factory: BackendChildFactory
    remote_factory: BackendChildFactory

    def recover_remote_child(self, reference: ChildReference) -> RemoteBackendChild:
        """Recover only retained authority, without allocation or native execution."""
        if ExecutorStore(self.db_path).child_assignment(reference) is None:
            raise ValueError("Retained remote child has no open assignment")
        if not isinstance(self.remote_factory, RemoteBackendChildFactory):
            raise ValueError("Configured remote factory cannot recover an exact child")
        return self.remote_factory.recover(reference)

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> BackendChild:
        env = (
            {}
            if resolved_start.role_materials is None
            else dict(resolved_start.role_materials.identity_environment_variables)
        )
        remote = False
        if env.get("PLAN_ACTOR") == "worker" and env.get("PLAN_TICKET_ID"):
            conn = connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT locale FROM tickets WHERE id=?", (env["PLAN_TICKET_ID"],)
                ).fetchone()
                remote = row is not None and row["locale"] == "macos"
            finally:
                conn.close()
        factory = self.remote_factory if remote else self.local_factory
        return factory(
            resolved_start=resolved_start, event_sink=event_sink, message_files=message_files
        )
