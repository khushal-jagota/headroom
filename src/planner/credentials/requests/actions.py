"""One canonical transition for each request, with secret writes inside its transaction."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from planner.conversation.events import ConversationEventPayload
from planner.core.authctx import RequestContext, normalize_manager_identity
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.credentials import actions as credential_actions
from planner.credentials import data as credential_data
from planner.credentials.contracts import (
    BearerSecret,
    CredentialAuthKind,
    CredentialMetadata,
    CredentialReference,
    CredentialScheme,
    CredentialWrite,
    HeaderSecret,
)
from planner.credentials.logic.validation import invalid, text
from planner.credentials.requests import data
from planner.credentials.requests.contracts import (
    CredentialRequestRowBody,
    RequestCredential,
)
from planner.credentials.requests.logic.validation import validate_request
from planner.credentials.store import DatabaseCredentialResolver, FileCredentialCipher
from planner.managers import data as manager_data
from planner.runtime import conversation_start
from planner.tickets import data as ticket_data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


def origin_conversation(conn: sqlite3.Connection, ctx: RequestContext) -> str:
    """Bind an employee request to its exact live association, never a new conversation."""
    principal, origin = ctx.principal, ctx.conversation_id
    if principal.kind == PrincipalKind.owner or not origin:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "credential requests require the employee conversation identity",
        )
    if principal.kind == PrincipalKind.ticket:
        current = ticket_data.read_ticket(conn, principal.id).conversation_id
    elif principal.kind == PrincipalKind.chief:
        current = conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
    elif principal.kind == PrincipalKind.manager:
        manager = manager_data.read_manager(conn, principal.id).item
        current = conversation_start.read_agent_conversation(conn, manager.manager_agent_key)
    elif principal.kind == PrincipalKind.goal:
        current = conversation_start.read_agent_conversation(conn, "goal_" + principal.id)
    else:
        current = None
    if current != origin:
        raise PlannerError(
            ErrorCode.agent_forbidden, "the requesting conversation is no longer current"
        )
    row = conn.execute(
        "SELECT identity_environment_variables FROM conversations WHERE conversation_id=?",
        (origin,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.agent_forbidden, "the requesting conversation is absent")
    identity = dict(json.loads(row[0]))
    actor = identity.get("PLAN_ACTOR")
    matches = (
        principal.kind == PrincipalKind.ticket
        and actor == "worker"
        and identity.get("PLAN_TICKET_ID") == principal.id
        or principal.kind == PrincipalKind.chief
        and actor == "chief"
        or principal.kind == PrincipalKind.goal
        and actor == "goal"
        and identity.get("PLAN_GOAL_ID") == principal.id
        or principal.kind == PrincipalKind.manager
        and actor in {"manager", "sprint_item_supervisor"}
        and normalize_manager_identity(
            identity.get("PLAN_MANAGER_ID"), identity.get("PLAN_SPRINT_ITEM_ID")
        )
        == principal.id
    )
    if not matches or identity.get("PLAN_CONVERSATION_ID", origin) != origin:
        raise PlannerError(
            ErrorCode.agent_forbidden, "the requesting conversation identity differs"
        )
    return origin


def public_credential(metadata: CredentialMetadata) -> RequestCredential:
    if metadata.auth_kind not in (CredentialAuthKind.bearer, CredentialAuthKind.header):
        raise invalid("credential authentication type differs")
    return {
        "id": metadata.id,
        "reference": {"scheme": metadata.reference.scheme, "name": metadata.reference.name},
        "auth_kind": cast(Literal["bearer", "header"], str(metadata.auth_kind)),
        "revision": metadata.revision,
    }


def _stale(metadata: CredentialMetadata) -> PlannerError:
    return PlannerError(
        ErrorCode.stale_claim,
        "credential changed",
        {"current_credential": credential_data.metadata_json(metadata)},
    )


def _attempt(conn: sqlite3.Connection, body: CredentialRequestRowBody, now: int) -> str:
    attempt_id = str(uuid4())
    reference = body["credential"]["reference"] if body["credential"] is not None else None
    message = data.encoded(
        {
            "credential_request_id": body["request_id"],
            "outcome": body["state"],
            "credential_reference": reference,
        }
    )
    prompt = (
        "API key request result: " + message + ". Continue the authorized work with this outcome. "
        "A saved value does not prove that a service accepts it."
    )
    conn.execute(
        "INSERT INTO credential_request_delivery_attempts("
        "attempt_id,request_id,sender_message_id,prompt,status,created_at) "
        "VALUES(?,?,?,?, 'intent',?)",
        (attempt_id, body["request_id"], "credential-request:" + attempt_id, prompt, now),
    )
    body["delivery"] = {"attempt_id": attempt_id, "status": "pending", "refusal_reason": None}
    return attempt_id


def create(
    conn: sqlite3.Connection, raw: object, *, ctx: RequestContext, now: int, key_path: Path
) -> tuple[tuple[CredentialRequestRowBody, str | None], tuple[ConversationEventPayload, ...]]:
    origin = origin_conversation(conn, ctx)
    requested = validate_request(raw)
    request_id = requested["request_id"]
    previous = conn.execute(
        "SELECT * FROM credential_requests WHERE request_id=?", (request_id,)
    ).fetchone()
    if previous is not None:
        if (
            previous["conversation_id"] != origin
            or previous["requester_kind"] != ctx.principal.kind
            or previous["requester_id"] != ctx.principal.id
            or previous["request_json"] != data.encoded(requested)
        ):
            raise invalid("request_id already names a different credential request")
        return ((data.read(conn, request_id), None), ())
    replacement = requested["replacement"]
    credential = None
    if replacement is not None:
        metadata = credential_data.read_metadata(conn, replacement["credential_id"])
        if metadata.revision != replacement["revision"]:
            raise _stale(metadata)
        if (
            requested["name"] != metadata.reference.name
            or replacement["name"] != metadata.reference.name
        ):
            raise invalid("a replacement keeps the current credential name")
        if metadata.auth_kind != requested["auth_kind"]:
            raise invalid("credential authentication type differs")
    else:
        existing = credential_data.find_metadata(
            conn, CredentialReference(CredentialScheme.local, requested["name"])
        )
        if existing is not None:
            if existing.auth_kind != requested["auth_kind"]:
                raise invalid("credential authentication type differs")
            if requested["auth_kind"] == "header":
                resolver = DatabaseCredentialResolver(
                    conn, FileCredentialCipher(key_path, has_records=True)
                )
                saved = resolver.resolve(existing.reference)
                if (
                    not isinstance(saved.secret, HeaderSecret)
                    or saved.secret.header_name != requested["header_name"]
                ):
                    raise invalid(
                        "saved credential header differs. Request an explicit replacement"
                    )
            credential = public_credential(existing)
    body: CredentialRequestRowBody = {
        "kind": "credential_request",
        **requested,
        "state": "reused" if credential is not None else "pending",
        "credential": credential,
        "delivery": None,
    }
    conn.execute(
        "INSERT INTO credential_requests(request_id,conversation_id,requester_kind,"
        "requester_id,request_json,body,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            request_id,
            origin,
            ctx.principal.kind,
            ctx.principal.id,
            data.encoded(requested),
            data.encoded(body),
            now,
            now,
        ),
    )
    attempt = _attempt(conn, body, now) if credential is not None else None
    return ((body, attempt), (data.update(conn, body, now),))


def save(
    conn: sqlite3.Connection,
    request_id: str,
    *,
    name: object,
    value: object,
    expected_revision: object,
    principal: Principal,
    now: int,
    key_path: Path,
) -> tuple[tuple[CredentialRequestRowBody, str | None], tuple[ConversationEventPayload, ...]]:
    credential_actions.require_plaintext_owner(principal)
    body = data.read(conn, request_id)
    if body["state"] != "pending":
        return ((body, None), ())
    name = text(name, maximum=200).strip()
    value = text(value)
    replacement = body["replacement"]
    if replacement is not None:
        current = credential_data.read_metadata(conn, replacement["credential_id"])
        if (
            type(expected_revision) is not int
            or expected_revision != replacement["revision"]
            or current.revision != expected_revision
        ):
            raise _stale(current)
        if name != replacement["name"] or current.reference.name != replacement["name"]:
            raise invalid("a replacement keeps the current credential name")
    elif expected_revision is not None:
        raise invalid("new credentials do not have a replacement revision")
    secret = (
        BearerSecret(value)
        if body["auth_kind"] == "bearer"
        else HeaderSecret(body["header_name"] or "", value)
    )
    if replacement is None:
        metadata = credential_actions.create_credential_in_transaction(
            conn,
            CredentialWrite(
                CredentialReference(CredentialScheme.local, name),
                CredentialAuthKind(body["auth_kind"]),
                secret,
            ),
            principal=principal,
            now=now,
            key_path=key_path,
        )
        body["state"] = "saved"
    else:
        metadata = credential_actions.replace_credential_in_transaction(
            conn,
            replacement["credential_id"],
            secret,
            expected_revision=cast(int, expected_revision),
            principal=principal,
            now=now,
            key_path=key_path,
        )
        body["state"] = "replaced"
    body["name"] = metadata.reference.name
    body["credential"] = public_credential(metadata)
    attempt = _attempt(conn, body, now)
    return ((body, attempt), (data.update(conn, body, now),))


def reject(
    conn: sqlite3.Connection, request_id: str, *, principal: Principal, now: int
) -> tuple[tuple[CredentialRequestRowBody, str | None], tuple[ConversationEventPayload, ...]]:
    credential_actions.require_plaintext_owner(principal)
    body = data.read(conn, request_id)
    if body["state"] != "pending":
        return ((body, None), ())
    body["state"] = "rejected"
    attempt = _attempt(conn, body, now)
    return ((body, attempt), (data.update(conn, body, now),))


def review(
    conn: sqlite3.Connection, request_id: str, *, principal: Principal, now: int
) -> tuple[CredentialRequestRowBody, tuple[ConversationEventPayload, ...]]:
    credential_actions.require_plaintext_owner(principal)
    body = data.read(conn, request_id)
    replacement = body["replacement"]
    if body["state"] != "pending" or replacement is None:
        return body, ()
    metadata = credential_data.read_metadata(conn, replacement["credential_id"])
    if metadata.auth_kind != body["auth_kind"]:
        raise invalid("credential authentication type differs")
    body["replacement"] = {
        "credential_id": metadata.id,
        "name": metadata.reference.name,
        "revision": metadata.revision,
    }
    body["name"] = metadata.reference.name
    return body, (data.update(conn, body, now),)


def retry(
    conn: sqlite3.Connection, request_id: str, *, principal: Principal, now: int
) -> tuple[tuple[CredentialRequestRowBody, str | None], tuple[ConversationEventPayload, ...]]:
    credential_actions.require_plaintext_owner(principal)
    body = data.read(conn, request_id)
    if body["delivery"] is None or body["delivery"]["status"] != "refused":
        return ((body, None), ())
    attempt = _attempt(conn, body, now)
    return ((body, attempt), (data.update(conn, body, now),))
