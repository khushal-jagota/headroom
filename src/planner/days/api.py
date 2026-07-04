"""Day routes (§9). The {date} path segment accepts the literal `today` (the
server resolves it via the planning date). Stubs until stage 4."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/day/{date}")
async def get_day(date: str) -> dict[str, Any]:
    raise NotImplementedError


@router.patch("/day/{date}")
async def patch_day(date: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/day/{date}/tickets")
async def add_day_ticket(date: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.delete("/day/{date}/tickets/{ticket_id}")
async def remove_day_ticket(date: str, ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/day/{date}/plan/accept")
async def plan_accept(date: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/day/{date}/plan/accept-all")
async def plan_accept_all(date: str) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/day/{date}/plan/invalidate")
async def plan_invalidate(date: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/day/{date}/plan/reject-all")
async def plan_reject_all(date: str) -> dict[str, Any]:
    raise NotImplementedError
