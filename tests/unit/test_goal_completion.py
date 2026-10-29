"""Pure completion risks: nested selection, empty lists, and input precedence."""

import pytest

from planner.goals.contracts import ChecklistBlock, ChecklistItem, PageDocument
from planner.goals.logic.completion import (
    checklist_completed,
    effective_completion,
    selected_checklist,
)


def page(checklist: ChecklistBlock) -> PageDocument:
    return {
        "kind": "headroom.page",
        "version": 1,
        "title": "Approach",
        "summary": "",
        "blocks": [
            {
                "type": "section",
                "id": "section",
                "title": "Delivery",
                "blocks": [
                    {
                        "type": "segmented",
                        "id": "segments",
                        "views": [
                            {
                                "id": "view",
                                "label": "Checklist",
                                "blocks": [checklist],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_selected_checklist_resolves_nested_identity_only() -> None:
    checklist: ChecklistBlock = {"type": "checklist", "id": "delivery", "items": []}
    document = page(checklist)

    assert selected_checklist(document, "delivery") is checklist
    assert selected_checklist(document, "section") is None
    assert selected_checklist(document, "missing") is None
    assert selected_checklist(document, None) is None


@pytest.mark.parametrize(
    ("items", "completed"),
    [
        ([], False),
        (
            [
                {"id": "draft", "label": "Draft", "state": "done"},
                {"id": "publish", "label": "Publish", "state": "done"},
            ],
            True,
        ),
        (
            [
                {"id": "draft", "label": "Draft", "state": "done"},
                {"id": "publish", "label": "Publish", "state": "open"},
            ],
            False,
        ),
        ([{"id": "draft", "label": "Draft", "state": "blocked"}], False),
    ],
)
def test_checklist_completion_requires_nonempty_all_done(
    items: list[ChecklistItem],
    completed: bool,
) -> None:
    checklist: ChecklistBlock = {
        "type": "checklist",
        "id": "delivery",
        "items": items,
    }

    assert checklist_completed(checklist) is completed


def test_selected_component_overrides_binary_input() -> None:
    checklist: ChecklistBlock = {
        "type": "checklist",
        "id": "delivery",
        "items": [{"id": "draft", "label": "Draft", "state": "done"}],
    }
    document = page(checklist)

    assert effective_completion(False, document, "delivery")
    checklist["items"][0]["state"] = "open"
    assert not effective_completion(True, document, "delivery")
    assert not effective_completion(True, document, "missing")
    assert effective_completion(True, document, None)
    assert not effective_completion(False, document, None)
