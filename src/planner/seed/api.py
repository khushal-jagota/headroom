"""Seed route (§9): POST /api/seed runs a migration and returns a MigrationReport.
Stub for now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.post("/seed")
async def seed(body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError
