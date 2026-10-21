"""The skill rows a deploy corrects, and the report of what it left alone."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import click
import pytest

from planner.core.db import connect, create_schema
from planner.core.migrations.skill_row_correction_data import SPAN_CORRECTIONS
from planner.core.migrations.versions.skill_rows_name_what_exists import (
    corrected_sources,
)
from planner.skill_staleness import stale_references_in


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(str(tmp_path / "planner.db"))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _panels_correction() -> tuple[str, str, str, str]:
    return next(item for item in SPAN_CORRECTIONS if item[0] == "panels")


def test_a_row_keeps_the_owners_words_while_losing_the_build_text_around_them() -> None:
    _skill, replaced_text, _replacement, _why = _panels_correction()
    owner_sentence = "I rewrote this paragraph myself and it must survive.\n"
    stored = {"panels": owner_sentence + replaced_text + "\nmy closing line\n"}

    corrected = corrected_sources(stored)

    assert owner_sentence in corrected["panels"]
    assert "my closing line" in corrected["panels"]
    assert replaced_text not in corrected["panels"]


def _create_example(payload: str, *, quote: str = "'", fence: str = "```sh") -> str:
    return (
        f"{fence}\npanels ticket create --input-json - <<{quote}JSON{quote}\n"
        f"{payload}\nJSON\n{fence[:3]}\n"
    )


def _create_findings(text: str) -> list[dict[str, str]]:
    return [
        item.as_dict()
        for item in stale_references_in(
            "some-skill", text, command_root=click.Group("panels"), stage_ids=frozenset()
        )
        if item.kind == "ticket_create_field"
    ]


def test_nested_manager_examples_use_the_actual_principal_parser() -> None:
    import json

    from planner.managed_skills import PACKAGED_SKILLS_ROOT
    from planner.skill_staleness import ticket_create_examples
    from planner.tickets.api import _parse_manager, _parse_principal

    examples = []
    for source in PACKAGED_SKILLS_ROOT.glob("*/SKILL.md"):
        for payload in ticket_create_examples(source.read_text()):
            examples.append(payload)
            if "manager" in payload:
                parsed = _parse_manager(payload["manager"])
                assert parsed.kind.value in {"manager", "chief"}
            if "ceiling_holder" in payload:
                assert _parse_principal(payload["ceiling_holder"], "ceiling_holder") is not None
    assert examples
    findings = stale_references_in(
        "example",
        _create_example(json.dumps({"manager": {"kind": "worker", "id": "t_one"}})),
        command_root=click.Group("panels"),
        stage_ids=frozenset(),
    )
    assert any(
        item.kind == "ticket_create_assignment" and item.reference == "manager" for item in findings
    )
