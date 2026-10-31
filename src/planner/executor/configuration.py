"""Shared wire, journal, and executor interface configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

# Wire and file limits.
MAX_ENVELOPE_BYTES: Final = 1024 * 1024
MAX_TOOL_DETAIL_CHARACTERS: Final = 64 * 1024
FILE_MODIFIED_WINDOW_NS: Final = 2_000_000_000
FILE_CHUNK_BYTES: Final = 256 * 1024
TRANSFER_CHUNK_BYTES: Final = 64 * 1024
MAX_TRANSFERS: Final = 4

# Request and connection timing.
CLI_RESULT_TIMEOUT_SECONDS: Final = 30.0
CLI_TRANSPORT_RESERVE_SECONDS: Final = 5.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0
CATALOG_TIMEOUT_SECONDS: Final = 35.0
RECONNECT_INTERVAL_SECONDS: Final = 5.0
HTTP_CONNECT_TIMEOUT_SECONDS: Final = 10.0
HTTP_CLOSE_TIMEOUT_SECONDS: Final = 5.0
WS_HEARTBEAT_SECONDS: Final = 15.0
LAUNCH_AGENT_RESTART_THROTTLE_SECONDS: Final = 10

# Agent environment and transport identity.
EXECUTOR_ROOT_ENV: Final = "PLAN_EXECUTOR_ROOT"
EXECUTOR_CHILD_ID_ENV: Final = "PLAN_EXECUTOR_CHILD_ID"
EXECUTOR_CAPABILITY_ENV: Final = "PLAN_EXECUTOR_CAPABILITY"
NATIVE_SKILLS_DIR_ENV: Final = "PLAN_NATIVE_SKILLS_DIR"
EXECUTOR_ID_HEADER: Final = "X-Panels-Executor-ID"
EXECUTOR_INCARNATION_HEADER: Final = "X-Panels-Executor-Incarnation"
EXECUTOR_GENERATION_HEADER: Final = "X-Panels-Executor-Generation"
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
    def workspace(self) -> Path:
        """The persistent application-owned default for unconfigured native Workers."""
        return self.root / "workspace"

    @property
    def spool(self) -> Path:
        return self.root / "spool"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts" / "tickets"

    @property
    def message_files_anchor(self) -> Path:
        """Path anchor for message files; no database is opened here."""
        return self.root / "artifacts" / "message-files.db"

    @property
    def skills(self) -> Path:
        return self.root / "skills"

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


def validate_server_url(value: str) -> str:
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("executor server URL contains whitespace or control characters")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("executor server URL has invalid port") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (port is not None and port < 1)
    ):
        raise ValueError("executor server URL must be an HTTPS origin")
    return value.rstrip("/")


def validate_executor_launch(executor_id: str, application_revision: str, server_url: str) -> None:
    """Validate explicit launch inputs without opening a connection or journal."""
    if (
        re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", executor_id) is None
        or not application_revision.strip()
    ):
        raise ValueError("invalid explicit executor HTTPS launch configuration")
    validate_server_url(server_url)
