"""Private secret entry and public names-only credential routes."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from planner.core.contracts import JsonDict
from planner.credentials import actions, data
from planner.credentials import configuration as limits
from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialReference,
    CredentialScheme,
    CredentialSecret,
    CredentialWrite,
    HeaderSecret,
)
from planner.credentials.logic.validation import invalid, text
from planner.tickets.api import Cfg, Clk, Ctx, DbConn

router = APIRouter()


async def _private_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None
    if not isinstance(body, dict):
        raise invalid()
    return body


def _secret(value: object, kind: CredentialAuthKind) -> CredentialSecret:
    if not isinstance(value, dict):
        raise invalid()
    if kind is CredentialAuthKind.bearer and set(value) == {"token"}:
        return BearerSecret(text(value["token"]))
    if kind is CredentialAuthKind.header and set(value) == {"header_name", "value"}:
        return HeaderSecret(
            text(value["header_name"]),
            text(value["value"]),
        )
    if kind is CredentialAuthKind.basic and set(value) == {"username", "password"}:
        return BasicSecret(
            text(value["username"], allow_empty=True),
            text(value["password"], allow_empty=True),
        )
    raise invalid()


def _revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise invalid("invalid credential revision")
    return value


@router.get("/credentials")
async def list_credentials(
    conn: DbConn,
    limit: int = limits.DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> JsonDict:
    page = data.list_metadata(conn, limit=limit, offset=offset)
    return {
        "credentials": [data.metadata_json(row) for row in page.rows],
        "page": page.facts_json(),
    }


@router.post("/credentials")
async def create_credential(
    request: Request, conn: DbConn, ctx: Ctx, clk: Clk, cfg: Cfg
) -> JsonDict:
    actions.require_plaintext_owner(ctx.principal)
    body = await _private_body(request)
    if set(body) != {
        "scheme",
        "name",
        "auth_kind",
        "secret",
    }:
        raise invalid()
    try:
        scheme, kind = CredentialScheme(body["scheme"]), CredentialAuthKind(body["auth_kind"])
    except (TypeError, ValueError):
        raise invalid("unsupported credential type") from None
    write = CredentialWrite(
        CredentialReference(scheme, text(body["name"], maximum=limits.MAX_NAME_LENGTH)),
        kind,
        _secret(body["secret"], kind),
    )
    result = actions.create_credential(
        conn,
        write,
        principal=ctx.principal,
        now=clk.now_unix(),
        key_path=Path(cfg.credential_key_path),
    )
    return data.metadata_json(result)


@router.put("/credentials/{credential_id}/secret")
async def replace_credential(
    credential_id: str, request: Request, conn: DbConn, ctx: Ctx, clk: Clk, cfg: Cfg
) -> JsonDict:
    actions.require_plaintext_owner(ctx.principal)
    body = await _private_body(request)
    if set(body) != {"expected_revision", "secret"}:
        raise invalid()
    metadata = data.read_metadata(conn, credential_id)
    result = actions.replace_credential(
        conn,
        credential_id,
        _secret(body["secret"], metadata.auth_kind),
        expected_revision=_revision(body["expected_revision"]),
        principal=ctx.principal,
        now=clk.now_unix(),
        key_path=Path(cfg.credential_key_path),
    )
    return data.metadata_json(result)


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    actions.delete_credential(conn, credential_id, principal=ctx.principal)
    return {"deleted": True}


@router.patch("/credentials/{credential_id}")
async def rename_credential(
    credential_id: str, request: Request, conn: DbConn, ctx: Ctx, clk: Clk, cfg: Cfg
) -> JsonDict:
    actions.require_plaintext_owner(ctx.principal)
    body = await _private_body(request)
    if set(body) != {"name", "expected_revision"}:
        raise invalid()
    result = actions.rename_credential(
        conn,
        credential_id,
        text(body["name"], maximum=limits.MAX_NAME_LENGTH),
        expected_revision=_revision(body["expected_revision"]),
        principal=ctx.principal,
        now=clk.now_unix(),
        key_path=Path(cfg.credential_key_path),
    )
    return data.metadata_json(result)
