"""Hashed launch credentials, bound to the canonical current conversation link."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

from planner.agent_identity.contracts import AgentIdentity
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError


def canonical_principal(conn: sqlite3.Connection, conversation_id: str) -> Principal | None:
    """Saved role text and environment never establish authority."""
    ticket = conn.execute(
        "SELECT id FROM tickets WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    if ticket is not None:
        return Principal(PrincipalKind.ticket, str(ticket[0]))
    chief = conn.execute(
        "SELECT 1 FROM agents WHERE agent_key='chief_of_staff' AND conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if chief is not None:
        return CHIEF_PRINCIPAL
    manager = conn.execute(
        "SELECT managers.id FROM managers JOIN agents "
        "ON agents.agent_key='manager_' || managers.id WHERE agents.conversation_id=?",
        (conversation_id,),
    ).fetchone()
    if manager is not None:
        return Principal(PrincipalKind.manager, str(manager[0]))
    return None


def issue_launch_token(conn: sqlite3.Connection, conversation_id: str) -> str:
    """Rotate this conversation's credential, returning the secret only to its launcher."""
    if canonical_principal(conn, conversation_id) is None:
        raise PlannerError(ErrorCode.agent_forbidden, "conversation has no current agent identity")
    token = secrets.token_urlsafe(32)
    with conn:
        conn.execute(
            "INSERT INTO agent_launch_tokens(token_hash,conversation_id) VALUES (?,?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET token_hash=excluded.token_hash",
            (_token_hash(token), conversation_id),
        )
    return token


def authenticate_launch_token(conn: sqlite3.Connection, token: str) -> AgentIdentity | None:
    row = conn.execute(
        "SELECT conversation_id FROM agent_launch_tokens WHERE token_hash=?",
        (_token_hash(token),),
    ).fetchone()
    if row is None:
        return None
    conversation_id = str(row[0])
    principal = canonical_principal(conn, conversation_id)
    return None if principal is None else AgentIdentity(principal, conversation_id)


def revoke_launch_tokens(conn: sqlite3.Connection, conversation_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM agent_launch_tokens WHERE conversation_id=?", (conversation_id,))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
