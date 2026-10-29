"""Shared wire, journal, and executor interface configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROTOCOL_VERSION: Final = 12
SPOOL_FORMAT_VERSION: Final = 2
OUTBOX_BUSY_TIMEOUT_MS: Final = 5000
OUTBOX_COPY_BYTES: Final = 256 * 1024
MAX_ENVELOPE_BYTES: Final = 1024 * 1024
FILE_CHUNK_BYTES: Final = 256 * 1024
FRAME_LENGTH_BYTES: Final = 4
CLI_RESULT_TIMEOUT_SECONDS: Final = 30.0
CLI_TRANSPORT_RESERVE_SECONDS: Final = 5.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0
CATALOG_TIMEOUT_SECONDS: Final = 35.0
CATALOG_CANCEL_TIMEOUT_SECONDS: Final = 5.0
MAX_CATALOG_OBSERVATIONS: Final = 2
INPUT_PROGRESS_TIMEOUT_SECONDS: Final = 30.0
INPUT_FINALIZATION_TIMEOUT_SECONDS: Final = 120.0
INPUT_CANCEL_TIMEOUT_SECONDS: Final = 5.0
RECONNECT_INTERVAL_SECONDS: Final = 5.0
EXIT_QUEUED: Final = 3
EXECUTOR_ROOT_ENV: Final = "PLAN_EXECUTOR_ROOT"
TICKET_FILES_DIR_ENV: Final = "PLAN_TICKET_FILES_DIR"
NATIVE_SKILLS_DIR_ENV: Final = "PLAN_NATIVE_SKILLS_DIR"
NATIVE_WORKER_CONTEXT: Final = (
    "For native execution, if PLAN_NATIVE_SKILLS_DIR exists, start with "
    "PLAN_NATIVE_SKILLS_DIR/panels/SKILL.md and read subsequent skill layers from "
    "that same root. If PLAN_TICKET_FILES_DIR exists, it supersedes all VPS-local "
    "Ticket artifact write paths in the skills. Write Ticket artifacts beneath "
    "PLAN_TICKET_FILES_DIR. Keep served /files/tickets/<ticket-id>/<relative-path> "
    "links in Ticket fields."
)
EXECUTOR_IPC_NAME: Final = "executor.sock"


@dataclass(frozen=True, slots=True)
class ExecutorPaths:
    """Persistent data only; replacing application code never replaces this root."""

    root: Path

    @property
    def spool(self) -> Path:
        return self.root / "spool"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts" / "tickets"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def run(self) -> Path:
        return self.root / "run"

    @property
    def ipc_socket(self) -> Path:
        return self.run / EXECUTOR_IPC_NAME


def executor_paths() -> ExecutorPaths:
    """Resolve paths without creating, reading, or migrating the spool."""
    configured = os.environ.get(EXECUTOR_ROOT_ENV, "").strip()
    root = (
        Path(configured).expanduser()
        if configured
        else (Path.home() / "Library" / "Application Support" / "Panels" / "executor")
    )
    return ExecutorPaths(root=root.resolve())


EXECUTOR_CHILD_ID_ENV: Final = "PLAN_EXECUTOR_CHILD_ID"
EXECUTOR_CAPABILITY_ENV: Final = "PLAN_EXECUTOR_CAPABILITY"

# Standard app-service transport limits.
HTTP_CONNECT_TIMEOUT_SECONDS: Final = 10.0
HTTP_CLOSE_TIMEOUT_SECONDS: Final = 5.0
WS_HEARTBEAT_SECONDS: Final = 15.0
MAX_TRANSFERS: Final = 4
EXECUTOR_ID_HEADER: Final = "X-Panels-Executor-ID"
EXECUTOR_INCARNATION_HEADER: Final = "X-Panels-Executor-Incarnation"
EXECUTOR_GENERATION_HEADER: Final = "X-Panels-Executor-Generation"

TRANSFER_CHUNK_BYTES: Final = 64 * 1024
