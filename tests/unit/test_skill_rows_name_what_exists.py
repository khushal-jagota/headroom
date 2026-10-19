"""The skill rows a deploy corrects, and the report of what it left alone."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import click
import pytest

from planner.core.db import connect, create_schema
from planner.core.migrations.skill_row_correction_data import CLAIMS, SPAN_CORRECTIONS
from planner.core.migrations.versions.skill_rows_name_what_exists import (
    corrected_sources,
    unmet_claims,
)
from planner.skill_sources import panels_skill_root
from planner.skill_staleness import stale_references, stale_references_in
from planner.tickets.contracts import CreateTicketBody


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


def test_a_command_that_moved_moves_inside_a_sentence_the_owner_wrote() -> None:
    stored = {
        "panels-worker-planning-sprint": (
            "Send that bounded opening in Ticket Chat and call "
            "`panels worker request-user-help <ticket-id>`, my own wording around it."
        )
    }

    corrected = corrected_sources(stored)

    assert "`panels worker request-help <ticket-id>`" in corrected["panels-worker-planning-sprint"]
    assert "my own wording around it" in corrected["panels-worker-planning-sprint"]


def test_a_correction_that_does_not_land_is_reported_rather_than_passing() -> None:
    skill_name, must_be_absent = CLAIMS[0]
    stored = {skill_name: f"an edited row that still says {must_be_absent} somewhere"}

    unmet = unmet_claims(stored, corrected_sources(stored))

    assert unmet == [f"{skill_name} still names {must_be_absent!r}"]


def test_a_row_that_never_held_the_text_is_never_claimed() -> None:
    stored: dict[str, str] = {}

    assert unmet_claims(stored, corrected_sources(stored)) == []


def test_the_check_reads_the_build_rather_than_a_list_of_old_names() -> None:
    root = click.Group(
        "panels",
        commands={"worker": click.Group("worker", commands={"propose": click.Command("propose")})},
    )

    found = stale_references_in(
        "some-skill",
        "Run `panels worker propose <id>` but never `panels worker trouble`.",
        command_root=root,
        stage_ids=frozenset(),
    )

    assert [(item.kind, item.reference) for item in found] == [("command", "panels worker trouble")]


def test_a_dead_command_is_not_excused_by_its_parent_group_existing() -> None:
    root = click.Group("panels", commands={"chief": click.Group("chief", commands={})})

    found = stale_references_in(
        "some-skill",
        "Use `panels chief reconcile-ticket-from-external-work <id>` for an existing Ticket.",
        command_root=root,
        stage_ids=frozenset(),
    )

    assert found[0].reference == "panels chief reconcile-ticket-from-external-work"


def test_a_database_this_build_made_names_only_things_this_build_has(
    database: sqlite3.Connection,
) -> None:
    assert stale_references(database) == ()


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


def test_create_fields_follow_the_contract_and_report_only_unknown_top_level_keys() -> None:
    payload: dict[str, object] = {key: None for key in CreateTicketBody.__annotations__}
    payload.update({"z_unknown": {}, "a_unknown": 1})
    payload["orchestrator"] = {"kickoff_note": "a nested field is not a create field"}
    example = _create_example(json.dumps(payload))

    assert _create_findings(example + example) == [
        {"skill_name": "some-skill", "kind": "ticket_create_field", "reference": key}
        for key in ("a_unknown", "z_unknown")
    ]


@pytest.mark.parametrize("quote,fence", [("'", "```sh"), ('"', "~~~bash"), ("'", "```shell")])
def test_create_example_reports_the_old_intake_field_and_accepts_brief(
    quote: str, fence: str
) -> None:
    old = _create_example('{"kickoff_note": "Intake"}', quote=quote, fence=fence)

    assert [item["reference"] for item in _create_findings(old)] == ["kickoff_note"]
    assert _create_findings(old.replace("kickoff_note", "brief")) == []


@pytest.mark.parametrize(
    "text",
    [
        'The old field is `kickoff_note`. {"kickoff_note": "prose"}',
        _create_example('{"kickoff_note": "non-shell"}', fence="```json"),
        _create_example('{"kickoff_note": "unfenced"}').replace("```sh\n", "").replace("```\n", ""),
        _create_example('{"kickoff_note": "another command"}').replace(
            "ticket create", "ticket edit"
        ),
        _create_example('{"kickoff_note": "file input"}').replace(
            "--input-json -", "--input-json file.json"
        ),
        _create_example('{"kickoff_note": "$variable"}', quote=""),
        _create_example('{"kickoff_note": $variable}'),
        _create_example('{"kickoff_note": "truncated"'),
        _create_example('{"kickoff_note": NaN}'),
        _create_example('[{"kickoff_note": "array"}]'),
        _create_example('{"kickoff_note": "missing fence"}').removesuffix("```\n"),
        _create_example('{"kickoff_note": "wrong delimiter"}').replace("\nJSON\n", "\nOTHER\n"),
        _create_example('{"kickoff_note": "spaced delimiter"}').replace("\nJSON\n", "\nJSON \n"),
        _create_example('{"kickoff_note":\ntrue\n}').replace("JSON", "true"),
        _create_example('{"kickoff_note": "continued command"}').replace(" - <<", " - \\\n<<"),
        _create_example('{"kickoff_note": "shell suffix"}').replace(
            "<<'JSON'", "<<'JSON' && echo done"
        ),
        _create_example('{"kickoff_note": "extra command"}').replace(
            "```sh\n", "```sh\necho start\n"
        ),
        _create_example('{"kickoff_note": "extra command"}').replace(
            "\nJSON\n", "\nJSON\necho done\n"
        ),
        "````text\n" + _create_example('{"kickoff_note": "nested fence"}') + "````\n",
        '~~~markdown title="~"\n' + _create_example('{"kickoff_note": "nested"}') + "~~~\n",
        '~~~markdown title="`"\n' + _create_example('{"kickoff_note": "nested"}') + "~~~\n",
        '````markdown title="~"\n' + _create_example('{"kickoff_note": "nested"}') + "````\n",
        _create_example(
            '{"brief": "kickoff_note is only text", "orchestrator": {"kickoff_note": 1}}'
        ),
        _create_example('{"kickoff_note": "nested heredoc"}')
        .replace("```sh\n", "```sh\ncat <<'OUTER'\n")
        .replace("\nJSON\n", "\nJSON\nOUTER\n"),
    ],
)
def test_ambiguous_or_unrelated_create_text_is_not_reported(text: str) -> None:
    assert _create_findings(text) == []


def test_packaged_create_examples_have_only_accepted_fields_before_migrations() -> None:
    root = panels_skill_root()

    assert {
        path.parent.name: findings
        for path in root.glob("*/SKILL.md")
        if (findings := _create_findings(path.read_text()))
    } == {}


def test_create_field_findings_use_the_existing_managed_row_report(
    database: sqlite3.Connection,
) -> None:
    database.execute(
        "UPDATE managed_skills SET source_text = ? WHERE skill_name = ?",
        (_create_example('{"kickoff_note": "Intake"}'), "panels-ticket-creation"),
    )

    assert [item.as_dict() for item in stale_references(database)] == [
        {
            "skill_name": "panels-ticket-creation",
            "kind": "ticket_create_field",
            "reference": "kickoff_note",
        }
    ]


def test_seed_only_create_defects_remain_visible_when_the_stored_skill_has_no_example(
    database: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database.execute(
        "UPDATE managed_skills SET source_text = ? WHERE skill_name = ?",
        ("The stored skill has no create example.", "panels-ticket-creation"),
    )
    root = tmp_path / "packaged-skills"
    seed = root / "panels-ticket-creation" / "SKILL.md"
    seed.parent.mkdir(parents=True)
    seed.write_text(
        "`panels vanished-command` needs_vanished_stage\n"
        + _create_example('{"kickoff_note": "Intake"}'),
        encoding="utf-8",
    )
    monkeypatch.setattr("planner.skill_staleness.panels_skill_root", lambda: root)

    assert [item.as_dict() for item in stale_references(database)] == [
        {
            "skill_name": "panels-ticket-creation",
            "kind": "packaged_ticket_create_field",
            "reference": "kickoff_note",
        }
    ]

    seed.write_text(seed.read_text(encoding="utf-8").replace("kickoff_note", "brief"))
    assert stale_references(database) == ()


def test_the_report_is_served_rather_than_read_as_a_skill_named_stale(tmp_path: Path) -> None:
    """``/skills/{skill_name}`` matches first unless the literal path is declared above it."""
    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.server import create_app

    db_path = tmp_path / "reported.db"
    with closing(connect(str(db_path))) as boot:
        create_schema(boot)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": "2026-09-21T09:00:00+01:00",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )

    with TestClient(app) as client:
        response = client.get("/api/skills/stale")

    assert response.status_code == 200
    assert response.json() == {"stale_references": [], "skill_names": []}
