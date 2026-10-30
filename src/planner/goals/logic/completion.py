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
    creating: bool,
    selection_explicit: bool,
    chart_explicit: bool,
    completed: bool | None,
) -> Goal:
    """Require intentional display changes and preserve unrelated legacy values."""
    guidance = f"inspect panels goal schema {'create' if creating else 'edit'} --json"
    kind_changed = current.kind != resulting.kind
    selector = resulting.primary_checklist_block_id
    if selector is not None and not isinstance(selector, str):
        raise ValueError(f"primary_checklist_block_id must be text or null; {guidance}")
    checklist = selected_checklist(resulting.page, selector)
    if selection_explicit and selector is not None:
        if resulting.kind != "binary":
            raise ValueError(f"only binary goals can select a primary checklist; {guidance}")
        if checklist is None:
            raise ValueError(
                "primary_checklist_block_id must identify a saved checklist; "
                f"use null to select binary completion; {guidance}"
            )

    if resulting.kind == "binary":
        if (creating or kind_changed) and not selection_explicit:
            raise ValueError(
                "primary_checklist_block_id is required: use null for binary completion "
                f"or a saved checklist ID; {guidance}"
            )
        if (
            not selection_explicit
            and checklist is None
            and selected_checklist(current.page, current.primary_checklist_block_id) is not None
        ):
            raise ValueError(
                "the selected checklist was removed: explicitly set primary_checklist_block_id "
                f"to a replacement saved checklist ID or null for binary completion; {guidance}"
            )
    else:
        if (creating or kind_changed) and not chart_explicit:
            raise ValueError(
                "primary_chart is required for a quantitative display: supply a chart object "
                f"with an explicit kind; {guidance}"
            )
        if chart_explicit and resulting.primary_chart is None:
            raise ValueError(
                "primary_chart cannot be null for a quantitative display: supply a chart object "
                "or explicitly switch kind to binary and set primary_checklist_block_id "
                f"to a saved checklist ID or null; {guidance}"
            )
        if creating or kind_changed:
            # An explicitly supplied destination chart replaces the checklist display.
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
