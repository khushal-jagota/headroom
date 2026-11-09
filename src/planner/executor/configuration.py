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

# Request and connection timing.
CLI_RESULT_TIMEOUT_SECONDS: Final = 30.0
CLI_TRANSPORT_RESERVE_SECONDS: Final = 5.0
REQUEST_TIMEOUT_SECONDS: Final = 30.0
HTTP_CONNECT_TIMEOUT_SECONDS: Final = 10.0
HTTP_CLOSE_TIMEOUT_SECONDS: Final = 5.0
# A conversation whose outbox send fails retries alone, doubling up to the cap.
OUTBOX_RETRY_INITIAL_SECONDS: Final = 1.0
OUTBOX_RETRY_MAX_SECONDS: Final = 60.0
# A repeated connection failure of one kind is logged at most this often.
CONNECTION_FAILURE_LOG_INTERVAL_SECONDS: Final = 60.0
LAUNCH_AGENT_RESTART_THROTTLE_SECONDS: Final = 10

# Mailbox transport (protocol 22). "Server" limits bound the VPS's own work on a
# request; "timeout" values bound the Mac client's whole request.
# Presence: a lease every 10 s, judged by the VPS clock; away after 60 s without one.
LEASE_INTERVAL_SECONDS: Final = 10.0
LEASE_TIMEOUT_SECONDS: Final = 10.0
LEASE_SERVER_LIMIT_SECONDS: Final = 5.0
MAC_AWAY_AFTER_SECONDS: Final = 60.0
# A lease more than this long after the previous accepted one restarts continuous
# presence, which every Mac-dependent stall rule counts from.
LEASE_CONTINUITY_SECONDS: Final = 25.0
# The lease lists at most this many conversations with waiting outbox items.
MAX_LEASE_WAITING_CONVERSATIONS: Final = 64
# Contact without an accepted lease (refused leases, the old WebSocket stub, update
# routes) is kept in memory and written at most this often, and only if it changed.
CONTACT_FLUSH_INTERVAL_SECONDS: Final = 10.0
# Body budgets. A sender fills a multi-value body (poll reply, outcome report, item
# batch, live batch) value by value while the encoded body stays within its budget,
# and always sends at least one value. A single value is bounded by MAX_ENVELOPE_BYTES
# when it is created, so a one-value body always fits. Each route passes this limit to
# protocol.encode_bounded and decode_bounded on both sides:
#   1 lease: request and reply, MAX_ENVELOPE_BYTES (the default)
#   2 commands poll: reply, MAX_MAILBOX_POLL_BYTES
#   3 outcomes: report, MAX_COMMAND_OUTCOME_REPORT_BYTES; reply, MAX_ENVELOPE_BYTES
#   4 items: batch and reply, MAX_ITEM_BATCH_BYTES
#   5 upload: the X-Panels-File-Upload FileUploadRequest, MAX_ENVELOPE_BYTES
#   7 reads: request and reply, MAX_ENVELOPE_BYTES
#   8 live: batch, MAX_ENVELOPE_BYTES
# Command mailbox: a long poll held for at most 50 s, answering at most 32 commands.
COMMAND_POLL_HOLD_SECONDS: Final = 50.0
COMMAND_POLL_TIMEOUT_SECONDS: Final = 60.0
MAX_MAILBOX_POLL_COMMANDS: Final = 32
MAX_MAILBOX_POLL_BYTES: Final = 4 * 1024 * 1024
# Command results: at most 32 per report.
MAX_COMMAND_OUTCOMES_PER_REPORT: Final = 32
MAX_COMMAND_OUTCOME_REPORT_BYTES: Final = 4 * 1024 * 1024
COMMAND_OUTCOME_SERVER_LIMIT_SECONDS: Final = 5.0
COMMAND_OUTCOME_TIMEOUT_SECONDS: Final = 10.0
# Per-kind command limits on the VPS (fetch-time expiry and the bounded waits).
STEER_DELIVERY_LIMIT_SECONDS: Final = 10.0
MODEL_LIST_LIMIT_SECONDS: Final = 35.0
# Item batches: one conversation's items, at most 32 and 4 MiB of body. The VPS's one
# budget for a whole batch is core.config.MAC_ITEM_RETRY_LATER_SECONDS (20 s).
MAX_ITEM_BATCH_ITEMS: Final = 32
MAX_ITEM_BATCH_BYTES: Final = 4 * 1024 * 1024
ITEM_BATCH_TIMEOUT_SECONDS: Final = 30.0
# Uploads and input downloads fail when no bytes move for this long, on both sides.
FILE_TRANSFER_STALL_SECONDS: Final = 60.0
# An input download that fails retries on monotonic time, doubling from 1 s to 30 s,
# until route 6 answers 403, 404 or 409.
INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS: Final = 1.0
INPUT_DOWNLOAD_RETRY_MAX_SECONDS: Final = 30.0
# Agent reads: the VPS answers within 20 s, under the agent's 30 s IPC deadline.
LIVE_READ_SERVER_LIMIT_SECONDS: Final = 20.0
LIVE_READ_TIMEOUT_SECONDS: Final = 25.0
# Live events: lossy, at most 64 per request, one request in flight per conversation.
MAX_LIVE_EVENTS_PER_BATCH: Final = 64
LIVE_EVENT_SERVER_LIMIT_SECONDS: Final = 5.0
LIVE_EVENT_TIMEOUT_SECONDS: Final = 5.0
# Agent children run at this nice value, so builds cannot starve the executor.
AGENT_CHILD_NICE: Final = 10
# A recorded orphan agent gets SIGTERM, then SIGKILL after this long.
ORPHAN_AGENT_TERMINATION_GRACE_SECONDS: Final = 10.0


@dataclass(frozen=True, slots=True)
class MacHttpConnectorSettings:
    """One aiohttp connector. Each Mac loop has its own, so no loop waits on another's
    connection pool."""

    limit: int
    force_close: bool = False
    keepalive_timeout: float | None = None


MAC_LEASE_CONNECTOR: Final = MacHttpConnectorSettings(limit=1, force_close=True)
MAC_POLL_CONNECTOR: Final = MacHttpConnectorSettings(limit=1, force_close=True)
MAC_OUTCOME_CONNECTOR: Final = MacHttpConnectorSettings(limit=2, force_close=True)
# No pool limit (0): each conversation keeps at most one item request or upload, and each
# agent one input download, in flight, so a shared pool would only let one conversation's
# slow files hold up another's.
MAC_ITEMS_CONNECTOR: Final = MacHttpConnectorSettings(limit=0, keepalive_timeout=15.0)
MAC_DOWNLOADS_CONNECTOR: Final = MacHttpConnectorSettings(limit=0, keepalive_timeout=15.0)
MAC_READS_CONNECTOR: Final = MacHttpConnectorSettings(limit=4, keepalive_timeout=15.0)
MAC_LIVE_CONNECTOR: Final = MacHttpConnectorSettings(limit=4, keepalive_timeout=5.0)

# Executor routes (protocol 22). Path parameters are filled in by the caller.
EXECUTOR_LEASE_PATH: Final = "/api/executor/lease"
EXECUTOR_COMMANDS_PATH: Final = "/api/executor/commands"
EXECUTOR_COMMAND_OUTCOMES_PATH: Final = "/api/executor/commands/outcomes"
EXECUTOR_ITEMS_PATH: Final = "/api/executor/conversations/{conversation_id}/items"
EXECUTOR_UPLOAD_PATH: Final = "/api/executor/uploads/{sha256}"
EXECUTOR_COMMAND_FILE_PATH: Final = "/api/executor/commands/{command_id}/files/{stored_file_id}"
EXECUTOR_READS_PATH: Final = "/api/executor/reads"
EXECUTOR_LIVE_PATH: Final = "/api/executor/conversations/{conversation_id}/live"

# Agent environment and transport identity.
EXECUTOR_ROOT_ENV: Final = "PLAN_EXECUTOR_ROOT"
EXECUTOR_CHILD_ID_ENV: Final = "PLAN_EXECUTOR_CHILD_ID"
EXECUTOR_CAPABILITY_ENV: Final = "PLAN_EXECUTOR_CAPABILITY"
NATIVE_SKILLS_DIR_ENV: Final = "PLAN_NATIVE_SKILLS_DIR"
EXECUTOR_ID_HEADER: Final = "X-Panels-Executor-ID"
EXECUTOR_INCARNATION_HEADER: Final = "X-Panels-Executor-Incarnation"
EXECUTOR_PROTOCOL_HEADER: Final = "X-Panels-Executor-Protocol"
# Route 5: base64url of the JSON FileUploadRequest. Route 6: the served bytes' digest.
EXECUTOR_FILE_UPLOAD_HEADER: Final = "X-Panels-File-Upload"
EXECUTOR_FILE_SHA256_HEADER: Final = "X-Panels-File-Sha256"
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
