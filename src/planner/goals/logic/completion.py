"""Derive completion from the selected saved component without changing its inputs."""

from dataclasses import replace
from typing import cast

from planner.goals.contracts import ChecklistBlock, Goal, PageDocument
from planner.goals.logic.documents import blocks


def selected_checklist(page: PageDocument, block_id: str | None) -> ChecklistBlock | None:
    if block_id is None:
        return None
    for block in blocks(page):
        if block["id"] == block_id and block["type"] == "checklist":
            return cast(ChecklistBlock, block)
    return None


def checklist_completed(checklist: ChecklistBlock) -> bool:
    return bool(checklist["items"]) and all(item["state"] == "done" for item in checklist["items"])


def effective_completion(
    binary_checked: bool,
    page: PageDocument,
    primary_checklist_block_id: str | None,
) -> bool:
    if primary_checklist_block_id is None:
        return binary_checked
    checklist = selected_checklist(page, primary_checklist_block_id)
    return checklist is not None and checklist_completed(checklist)


def prepare_completion(
    current: Goal,
    resulting: Goal,
    *,
    selection_explicit: bool,
    completed: bool | None,
) -> Goal:
    """Validate the resulting selection and preserve completion when selection ends."""
    selector = resulting.primary_checklist_block_id
    if selector is not None and not isinstance(selector, str):
        raise ValueError("primary_checklist_block_id must be text or null")
    checklist = selected_checklist(resulting.page, selector)
    if selector is not None:
        if selection_explicit:
            if resulting.kind != "binary":
                raise ValueError("only binary goals can select a primary checklist")
            if checklist is None:
                raise ValueError("primary_checklist_block_id must identify a saved checklist")
        elif resulting.kind != "binary" or checklist is None:
            selector = None

    binary_checked = resulting.binary_checked
    if current.primary_checklist_block_id is not None and selector is None:
        binary_checked = effective_completion(
            current.binary_checked, current.page, current.primary_checklist_block_id
        )
    if completed is not None:
        if selector is None:
            binary_checked = completed
        elif completed != effective_completion(binary_checked, resulting.page, selector):
            raise ValueError("completion follows the selected checklist's items")
    return replace(resulting, binary_checked=binary_checked, primary_checklist_block_id=selector)
