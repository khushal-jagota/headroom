"""Ticket routes (§9), plus the ticket-anchored links and the ticket-centric
derived views (board, queues). Handlers are stubs until the engine lands in
stage 4; the method + path is the contract now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.post("/tickets")
async def create_ticket(body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/tickets")
async def list_tickets(
    state: str | None = None,
    project: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.patch("/tickets/{ticket_id}")
async def patch_ticket(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/tickets/{ticket_id}/propose/{field}")
async def propose_field(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/tickets/{ticket_id}/accept/{field}")
async def accept_field(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/tickets/{ticket_id}/approve")
async def approve_ticket(ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.put("/tickets/{ticket_id}/notes/{field}")
async def put_notes(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.put("/tickets/{ticket_id}/recap")
async def put_recap(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/tickets/{ticket_id}/grant")
async def grant_ticket(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/tickets/{ticket_id}/state")
async def set_state(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/tickets/{ticket_id}/drop")
async def drop_ticket(ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/tickets/{ticket_id}/unblock")
async def unblock_ticket(ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/tickets/{ticket_id}/events")
async def ticket_events(ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/tickets/{ticket_id}/runs")
async def ticket_runs(ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/tickets/{ticket_id}/copy-text")
async def ticket_copy_text(ticket_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/links")
async def add_link(body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.delete("/links")
async def remove_link(from_id: str, to_id: str, kind: str) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/board")
async def board() -> dict[str, Any]:
    raise NotImplementedError


@router.get("/queues")
async def queues() -> dict[str, Any]:
    raise NotImplementedError
