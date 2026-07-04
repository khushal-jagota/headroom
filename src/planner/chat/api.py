"""Chat routes (§9): send a message, read gateway availability. Stubs for now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.post("/chat/{entity_id}/send")
async def send_message(entity_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/chat/{entity_id}/status")
async def gateway_status(entity_id: str) -> dict[str, Any]:
    raise NotImplementedError
