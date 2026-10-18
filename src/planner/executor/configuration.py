"""Shared wire, journal, and resident interface configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROTOCOL_VERSION: Final = 4
SPOOL_FORMAT_VERSION: Final = 2
OUTBOX_BUSY_TIMEOUT_MS: Final = 5000
OUTBOX_COPY_BYTES: Final = 256 * 1024
MAX_ENVELOPE_BYTES: Final = 1024 * 1024
FILE_CHUNK_BYTES: Final = 256 * 1024
FRAME_LENGTH_BYTES: Final = 4
CLI_RESULT_TIMEOUT_SECONDS: Final = 30.0
RECONNECT_INTERVAL_SECONDS: Final = 5.0
EXIT_QUEUED: Final = 3
EXECUTOR_ROOT_ENV: Final = "PLAN_EXECUTOR_ROOT"
TICKET_FILES_DIR_ENV: Final = "PLAN_TICKET_FILES_DIR"
EXECUTOR_CODE_RELATIVE_PATH: Final = Path("Deployments/PanelsExecutor/current/app")
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
