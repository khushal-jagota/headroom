"""The one projection contract used by CLI record reads."""

from __future__ import annotations

import pytest

from planner.cli.record_projection import (
    parse_part_names,
    part,
    project_record,
    render_text,
)


def test_manifest_preserves_declared_order_and_counts_unicode_characters() -> None:
    projected = project_record(
        {"id": "t_one", "stage": "needs_success"},
        {
            "empty": part(""),
            "brief": part("A🌱é", user_note="Keep this."),
        },
        None,
    )

    assert list(projected["manifest"]) == ["empty", "brief"]
    assert projected["manifest"] == {
        "empty": {"character_count": 0, "has_user_note": False},
        "brief": {"character_count": 3, "has_user_note": True},
    }
    text = render_text(projected)
    assert "character_count: 3" in text
    assert "has_user_note: true" in text


def test_selection_returns_only_requested_parts_in_caller_order() -> None:
    parts = {
        "kickoff": part("why"),
        "success": part(
            "done", user_note="Do less.", proposal={"body": "proposed"}
        ),
        "approach": part(None),
    }

    projected = project_record(
        {"id": "t_one"}, parts, parse_part_names("success,kickoff")
    )

    assert list(projected["parts"]) == ["success", "kickoff"]
    assert projected["parts"]["success"] == {
        "value": "done",
        "user_note": "Do less.",
        "proposal": {"body": "proposed"},
    }
    assert "manifest" not in projected
    text = render_text(projected)
    assert "value: done" in text
    assert "user_note: Do less." in text
    assert 'proposal: {"body": "proposed"}' in text


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("body,", "part names must be a comma-separated list"),
    ],
)
def test_part_list_validation(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_part_names(raw)


def test_duplicate_parts_report_the_valid_ordered_names() -> None:
    with pytest.raises(
        ValueError,
        match="duplicate part names: body; valid part names: body, notes",
    ):
        project_record(
            {"id": "record"},
            {"body": part("one"), "notes": part("two")},
            parse_part_names("body,body"),
        )


def test_unknown_parts_report_the_valid_ordered_names() -> None:
    with pytest.raises(
        ValueError,
        match="unknown part names: missing; valid part names: first, second",
    ):
        project_record(
            {"id": "record"},
            {"first": part("one"), "second": part("two")},
            ("missing",),
        )
