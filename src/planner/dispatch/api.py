"""Run routes (§9): heartbeat and close. Both require the claim headers, checked
server-side in stage 4. Stubs for now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.post("/runs/{run_id}/heartbeat")
async def heartbeat(run_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/runs/{run_id}/close")
async def close_run(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError
