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
