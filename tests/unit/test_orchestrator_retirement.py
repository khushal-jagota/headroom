"""The retirement preserves history and ordinary supply without live calendar state."""

from __future__ import annotations

def test_managed_skill_retirement_preserves_custom_prose_and_detects_unknown_commands() -> None:
    from planner.core.migrations.retirement_skills import (
        corrected_sources,
        stale_calendar_instructions,
    )

    custom = (
        "---\nname: panels-sprint-item-supervisor\ndescription: Custom manager\n---\n\n"
        "Owner-specific judgment stays exactly as written.\n\n"
        "add-to-day and remove-from-day change Day membership.\n"
    )
    corrected = corrected_sources({"panels-sprint-item-supervisor": custom})
    text = corrected["panels-sprint-item-supervisor"]
    assert "Owner-specific judgment stays exactly as written." in text
    assert "Day membership operations are retired." in text
    assert stale_calendar_instructions(corrected) == []
    assert corrected_sources(corrected) == corrected
    assert stale_calendar_instructions(
        {"custom-skill": "Run panels day add-ticket for this Worker."}
    )
