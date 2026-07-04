"""Sprint-item, sprint, current-sprint view, and idea routes (§9). Ideas are
homed here because the Idea shape lives in this domain's contracts. Stubs until
stage 4."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.post("/items")
async def create_item(body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/items")
async def list_items(
    status: str | None = None,
    project: str | None = None,
    sprint_id: str | None = None,
) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/items/{item_id}")
async def get_item(item_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.patch("/items/{item_id}")
async def patch_item(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/items/{item_id}/propose-status")
async def propose_item_status(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/items/{item_id}/accept-status")
async def accept_item_status(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/sprints")
async def create_sprint(body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/sprints")
async def list_sprints() -> dict[str, Any]:
    raise NotImplementedError


@router.get("/sprints/{sprint_id}")
async def get_sprint(sprint_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.patch("/sprints/{sprint_id}")
async def patch_sprint(sprint_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/sprints/{sprint_id}/freeze-kickoff")
async def freeze_kickoff(sprint_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/sprints/{sprint_id}/freeze-review")
async def freeze_review(sprint_id: str) -> dict[str, Any]:
    raise NotImplementedError


@router.post("/sprints/{sprint_id}/addenda")
async def add_addendum(sprint_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/sprint/current")
async def current_sprint() -> dict[str, Any]:
    raise NotImplementedError


@router.post("/ideas")
async def create_idea(body: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError


@router.get("/ideas")
async def list_ideas() -> dict[str, Any]:
    raise NotImplementedError
