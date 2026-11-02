"""What the Mac knows about its one link to the VPS."""

import os
from dataclasses import dataclass

from planner.execution_identity import ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.link import Link
from planner.executor.protocol import ConnectionState, ExecutorHealth


@dataclass
class MacConnection:
    identity: ExecutorIdentity
    link: Link | None = None  # Set only after hello is accepted.
    state: ConnectionState = ConnectionState.disconnected
    lost_at_ms: int | None = None
    admission_stopped: bool = False

    def ready_link(self) -> Link | None:
        return self.link if self.state is ConnectionState.ready else None

    def accepting(self, link: Link) -> bool:
        return self.ready_link() is link and not self.admission_stopped

    def health(self) -> ExecutorHealth:
        return ExecutorHealth(
            self.identity,
            os.getpid(),
            self.state,
            self.state is ConnectionState.ready,
            now_ms(),
            self.lost_at_ms,
        )

    def lost(self) -> None:
        self.link = None
        self.state = ConnectionState.disconnected
        if self.lost_at_ms is None:
            self.lost_at_ms = now_ms()
