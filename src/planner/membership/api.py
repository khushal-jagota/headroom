"""The one way to put a thing in a collection, and the one way to take it out.

The collection is a parameter, not an address. Both routes look the collection up, ask
its one authority rule, and hand the work to the code that owns that collection.
"""

from __future__ import annotations

from functools import partial

from fastapi import APIRouter

from planner.membership.collections import ENTRIES, CollectionEntry, MembershipWrite
from planner.membership.contracts import Collection, MembershipAnswer
from planner.tickets.api import Cfg, Clk, Ctx, DbConn, parse_enum

router = APIRouter()

_PATH = "/collections/{collection}/{container_id}/{member_id}"


def _prepare(
    collection: str,
    container_id: str,
    member_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> tuple[CollectionEntry, MembershipWrite]:
    entry = ENTRIES[parse_enum(Collection, collection, "collection")]
    resolved_container = entry.resolve_container(container_id, clk, cfg)
    write = MembershipWrite(
        conn=conn,
        principal=ctx.principal,
        container_id=resolved_container,
        member_id=member_id,
        now=clk.now_unix(),
        admit=partial(entry.rule, conn, ctx, resolved_container, member_id),
    )
    return entry, write


def _answer(collection: str, write: MembershipWrite) -> MembershipAnswer:
    return MembershipAnswer(
        collection=collection,
        container_id=write.container_id,
        member_id=write.member_id,
        ok=True,
    )


@router.put(_PATH)
async def add_member(
    collection: str,
    container_id: str,
    member_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> MembershipAnswer:
    entry, write = _prepare(collection, container_id, member_id, conn, ctx, cfg, clk)
    entry.add(write)
    return _answer(collection, write)


@router.delete(_PATH)
async def remove_member(
    collection: str,
    container_id: str,
    member_id: str,
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
) -> MembershipAnswer:
    entry, write = _prepare(collection, container_id, member_id, conn, ctx, cfg, clk)
    entry.remove(write)
    return _answer(collection, write)
