"""Machine authority; only unconfirmed original handover keeps a recoverable secret."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import time
from uuid import UUID, uuid4

from planner.core.errors import ErrorCode, PlannerError
from planner.machines.contracts import (
    HOME_MACHINE_ID,
    ORIGINAL_MAC_MACHINE_ID,
    MachineCredentialGrant,
)

_LOG = logging.getLogger(__name__)


def _forbidden() -> PlannerError:
    return PlannerError(ErrorCode.agent_forbidden, "Executor credential is required or invalid")


def _digest(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()


def authenticate_executor(
    conn: sqlite3.Connection,
    claimed_id: str,
    credential: str | None,
    configured_original_id: str | None,
    authenticated_incarnation: str | None = None,
    *,
    authenticated_platform: str | None = None,
) -> str:
    machine_id = (
        ORIGINAL_MAC_MACHINE_ID
        if configured_original_id and claimed_id == configured_original_id
        else claimed_id
    )
    row = conn.execute(
        "SELECT credential_sha256,legacy_closed_at_ms,platform FROM machine_executor_credentials "
        "WHERE machine_id=?",
        (machine_id,),
    ).fetchone()
    if row is None or machine_id == HOME_MACHINE_ID:
        raise _forbidden()
    if credential is not None:
        if row[0] is None or not hmac.compare_digest(str(row[0]), _digest(credential)):
            raise _forbidden()
        if authenticated_platform is not None and authenticated_platform not in {"linux", "darwin"}:
            raise PlannerError(ErrorCode.validation, "Executor platform must be darwin or linux")
        if row[1] is None:
            _confirm_original_credential(conn, machine_id, credential, authenticated_incarnation)
        if authenticated_platform is not None and authenticated_platform != row[2]:
            with conn:
                conn.execute(
                    "UPDATE machine_executor_credentials SET platform=? "
                    "WHERE machine_id=? AND credential_sha256=? AND platform<>?",
                    (
                        authenticated_platform,
                        machine_id,
                        _digest(credential),
                        authenticated_platform,
                    ),
                )
        return machine_id
    # This is exactly the pre-existing original-device authority, not a fallback for
    # a wrong secret. First credential authentication closes it durably.
    if (
        machine_id == ORIGINAL_MAC_MACHINE_ID
        and configured_original_id is not None
        and claimed_id == configured_original_id
        and row[1] is None
    ):
        return machine_id
    raise _forbidden()


def _confirm_original_credential(
    conn: sqlite3.Connection, machine_id: str, credential: str,
    authenticated_incarnation: str | None,
) -> None:
    # Serialize acknowledgment against bootstrap retry and operator replacement.
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT credential_sha256,legacy_closed_at_ms,issued_at_ms,issued_incarnation "
            "FROM machine_executor_credentials WHERE machine_id=?", (machine_id,),
        ).fetchone()
        if row is None or not hmac.compare_digest(str(row[0]), _digest(credential)):
            raise _forbidden()
        closed_at_ms = time.time_ns() // 1_000_000
        first_confirmation = row[1] is None
        if first_confirmation:
            conn.execute(
                "UPDATE machine_executor_credentials SET legacy_closed_at_ms=?,"
                "pending_credential=NULL WHERE machine_id=?", (closed_at_ms, machine_id),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if first_confirmation:
        # The header supplies audit context only; it never grants authority.
        try:
            audit_incarnation = str(UUID(authenticated_incarnation or ""))
        except ValueError:
            audit_incarnation = None
        _LOG.warning(
            "Original executor credential confirmed at_ms=%s issued_at_ms=%s "
            "executor_incarnation=%s authenticated_incarnation=%s machine_id=%s; "
            "legacy authority permanently closed",
            closed_at_ms, row[2], row[3], audit_incarnation, machine_id,
        )


def _issue(
    conn: sqlite3.Connection, machine_id: str, incarnation: str | None
) -> MachineCredentialGrant:
    now_ms = time.time_ns() // 1_000_000
    credential = secrets.token_urlsafe(32)
    conn.execute(
        "UPDATE machine_executor_credentials SET credential_sha256=?,issued_at_ms=?,"
        "issued_incarnation=?,legacy_closed_at_ms=COALESCE(legacy_closed_at_ms,?),"
        "pending_credential=NULL "
        "WHERE machine_id=?",
        (_digest(credential), now_ms, incarnation, now_ms, machine_id),
    )
    return MachineCredentialGrant(machine_id, credential, now_ms)


def issue_original_credential(
    conn: sqlite3.Connection, incarnation: str
) -> MachineCredentialGrant:
    UUID(incarnation)
    issued = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT legacy_closed_at_ms,pending_credential,issued_at_ms "
            "FROM machine_executor_credentials WHERE machine_id=?",
            (ORIGINAL_MAC_MACHINE_ID,),
        ).fetchone()
        if row is None or row[0] is not None:
            raise _forbidden()
        if row[1] is not None:
            grant = MachineCredentialGrant(ORIGINAL_MAC_MACHINE_ID, str(row[1]), int(row[2]))
        else:
            now_ms = time.time_ns() // 1_000_000
            credential = secrets.token_urlsafe(32)
            conn.execute(
                "UPDATE machine_executor_credentials SET credential_sha256=?,issued_at_ms=?,"
                "issued_incarnation=?,pending_credential=? WHERE machine_id=?",
                (_digest(credential), now_ms, incarnation, credential, ORIGINAL_MAC_MACHINE_ID),
            )
            grant = MachineCredentialGrant(ORIGINAL_MAC_MACHINE_ID, credential, now_ms)
            issued = True
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if issued:
        _LOG.warning(
            "Original executor credential issued at_ms=%s executor_incarnation=%s machine_id=%s; "
            "awaiting first credential authentication",
            grant.issued_at_ms, incarnation, grant.machine_id,
        )
    return grant


def register_machine(
    conn: sqlite3.Connection, name: str, platform: str = "darwin"
) -> MachineCredentialGrant:
    if not isinstance(platform, str) or platform not in {"darwin", "linux"}:
        raise PlannerError(ErrorCode.validation, "Executor platform must be darwin or linux")
    name = name.strip()
    if not name or len(name) > 80:
        raise PlannerError(ErrorCode.validation, "Machine name must contain 1 to 80 characters")
    machine_id = str(uuid4())
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO machines(machine_id,name,icon,color,is_home) "
            "VALUES(?,?,'laptop','orchid',0)",
            (machine_id, name),
        )
        conn.execute(
            "INSERT INTO machine_executor_credentials(machine_id,platform) VALUES(?,?)",
            (machine_id, platform),
        )
        grant = _issue(conn, machine_id, None)
        conn.commit()
        return grant
    except BaseException:
        conn.rollback()
        raise


def replace_credential(conn: sqlite3.Connection, machine_id: str) -> MachineCredentialGrant:
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT 1 FROM machine_executor_credentials WHERE machine_id=?", (machine_id,),
        ).fetchone()
        if row is None or machine_id == HOME_MACHINE_ID:
            raise PlannerError(ErrorCode.validation, "Machine has no executor credential")
        grant = _issue(conn, machine_id, None)
        conn.commit()
        return grant
    except BaseException:
        conn.rollback()
        raise


def executor_platform(conn: sqlite3.Connection, machine_id: str) -> str:
    row = conn.execute(
        "SELECT platform FROM machine_executor_credentials WHERE machine_id=?", (machine_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.validation, "Machine has no executor credential")
    return str(row[0])
