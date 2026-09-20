"""Every principal, every guarded operation, admitted or refused.

This is the parity evidence for replacing every authority guard with one rule. It is
generated, not asserted by hand: the matrix runs each operation once per principal against
a real app on a fresh copy of one seeded database, and records only whether the caller got
past authority.

A cell is ``refuse`` when the answer carries ``agent_forbidden``, and ``allow`` for every
other outcome, including ``not_found`` and ``validation``. Those mean the caller was
admitted and failed for some other reason, which is not what this file measures.

Each cell gets its own copy of the seeded database, so an admitted mutation in one cell can
never change another cell's answer.

Set ``PANELS_REWRITE_AUTHORITY_MATRIX=1`` to rewrite the snapshot after a deliberate
change. Every moved cell then shows up in the snapshot's diff and has to be explained.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import pytest
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

_SNAPSHOT: Final = Path(__file__).with_name("authority_matrix.snapshot.txt")
_REWRITE_ENV: Final = "PANELS_REWRITE_AUTHORITY_MATRIX"
_OWNER_HOLDER: Final = {"kind": "owner", "id": "owner"}
_DAY: Final = "2026-09-20"


@dataclass(frozen=True, slots=True)
class Seed:
    """The ids the seeded database holds, named by their relation to the target."""

    item_a: str
    item_b: str
    ticket_a: str
    ticket_b: str
    ticket_planning: str
    ticket_user_owned: str
    sprint_id: str
    gating_field: str


@dataclass(frozen=True, slots=True)
class Call:
    method: str
    path: str
    body: dict[str, Any] | None = None


Operation = tuple[str, Callable[[Seed], Call]]


def _app_on(db_path: Path) -> Any:
    env = {"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path), "PLAN_FAKE_NOW": f"{_DAY}T12:00:00"}
    config = load_config(path=None, env=env)
    return create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )


def _principal_headers(seed: Seed) -> dict[str, dict[str, str]]:
    return {
        "owner": {},
        "chief": {"X-Plan-Actor": "chief"},
        "outcome_parent": {
            "X-Plan-Actor": "sprint_item_supervisor",
            "X-Plan-Sprint-Item-ID": seed.item_a,
        },
        "outcome_stranger": {
            "X-Plan-Actor": "sprint_item_supervisor",
            "X-Plan-Sprint-Item-ID": seed.item_b,
        },
        "ticket_self": {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": seed.ticket_a},
        "ticket_stranger": {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": seed.ticket_b},
        "ticket_planning_day": {
            "X-Plan-Actor": "worker",
            "X-Plan-Ticket-ID": seed.ticket_planning,
        },
    }


PRINCIPALS: Final = (
    "owner",
    "chief",
    "outcome_parent",
    "outcome_stranger",
    "ticket_self",
    "ticket_stranger",
    "ticket_planning_day",
)


def _seed(db_path: Path) -> Seed:
    """Build the one state every cell runs against: two Outcomes, a Ticket under each,
    a parked proposal on the target Ticket so accept and reject reach their real gate."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    with TestClient(_app_on(db_path)) as client:
        item_a = _ok(client.post("/api/items", json={"title": "A", "project_id": "project_vylo"}))
        item_b = _ok(client.post("/api/items", json={"title": "B", "project_id": "project_vylo"}))
        ticket_a = _ok(
            client.post(
                "/api/tickets",
                json={
                    "worker_type": "coding",
                    "title": "Target ticket",
                    "kickoff_note": "Start here.",
                    "sprint_item_id": item_a["id"],
                },
            )
        )
        ticket_b = _ok(
            client.post(
                "/api/tickets",
                json={
                    "worker_type": "coding",
                    "title": "Stranger ticket",
                    "kickoff_note": "Start here.",
                    "sprint_item_id": item_b["id"],
                },
            )
        )
        _ok(
            client.post(
                f"/api/tickets/{ticket_a['id']}/propose",
                json={"body": "Done and verified.", "recap": "Ready for review"},
                headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(ticket_a["id"])},
            )
        )
        ticket_planning = _ok(
            client.post(
                "/api/tickets",
                json={
                    "worker_type": "planning-day",
                    "title": "Plan the day",
                    "kickoff_note": "Start here.",
                },
            )
        )
        ticket_user_owned = _ok(
            client.post(
                "/api/tickets",
                json={
                    "worker_type": "personal",
                    "title": "A user-owned gate",
                    "kickoff_note": "Start here.",
                    "sprint_item_id": item_a["id"],
                },
            )
        )
        sprint = _ok(
            client.post(
                "/api/sprints",
                json={
                    "name": "Matrix sprint",
                    "date_start": "2026-09-21",
                    "date_end": "2026-10-04",
                },
            )
        )
    return Seed(
        gating_field=str(ticket_a["stage"]).removeprefix("needs_"),
        item_a=str(item_a["id"]),
        item_b=str(item_b["id"]),
        ticket_a=str(ticket_a["id"]),
        ticket_b=str(ticket_b["id"]),
        ticket_planning=str(ticket_planning["id"]),
        ticket_user_owned=str(ticket_user_owned["id"]),
        sprint_id=str(sprint["id"]),
    )


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, f"{response.request.url}: {response.text}"
    return cast(dict[str, Any], response.json())


# --- the operations -------------------------------------------------------------
#
# One row per guarded operation. Each body is valid enough to reach the guard: a body that
# fails marshalling first would make the row measure nothing.

OPERATIONS: Final[tuple[Operation, ...]] = (
    # --- a Ticket, through its ordinary address
    (
        "GET    /tickets (detail=full)",
        lambda s: Call("GET", f"/api/tickets?detail=full&id={s.ticket_a}"),
    ),
    (
        "PATCH  /tickets/{t} priority",
        lambda s: Call("PATCH", f"/api/tickets/{s.ticket_a}", {"priority": "P2"}),
    ),
    (
        "PATCH  /tickets/{t} title",
        lambda s: Call("PATCH", f"/api/tickets/{s.ticket_a}", {"title": "Renamed"}),
    ),
    (
        "PATCH  /tickets/{t} sprint_item_id",
        lambda s: Call("PATCH", f"/api/tickets/{s.ticket_a}", {"sprint_item_id": s.item_b}),
    ),
    (
        "PATCH  /tickets/{t} project_id",
        lambda s: Call("PATCH", f"/api/tickets/{s.ticket_a}", {"project_id": "project_other"}),
    ),
    (
        "PATCH  /tickets/{t} ceiling",
        lambda s: Call("PATCH", f"/api/tickets/{s.ticket_a}", {"ceiling": "needs_consequences"}),
    ),
    (
        "PATCH  /tickets/{t} ceiling_holder",
        lambda s: Call("PATCH", f"/api/tickets/{s.ticket_a}", {"ceiling_holder": _OWNER_HOLDER}),
    ),
    (
        "PATCH  /tickets/{t} recap",
        lambda s: Call("PATCH", f"/api/tickets/{s.ticket_a}", {"recap": "A recap"}),
    ),
    ("DELETE /tickets/{t}", lambda s: Call("DELETE", f"/api/tickets/{s.ticket_a}")),
    (
        "POST   /tickets/{t}/propose",
        lambda s: Call(
            "POST", f"/api/tickets/{s.ticket_a}/propose", {"body": "Again.", "recap": "Again"}
        ),
    ),
    (
        "POST   /tickets/{t}/request-help",
        lambda s: Call("POST", f"/api/tickets/{s.ticket_a}/request-help", {"message": "Stuck."}),
    ),
    (
        "PUT    /tickets/{t}/employee-configuration",
        lambda s: Call(
            "PUT",
            f"/api/tickets/{s.ticket_a}/employee-configuration",
            {"employee_backend": "claude", "employee_launch_model": "opus[1m]"},
        ),
    ),
    (
        "POST   /tickets/{t}/conversation/send",
        lambda s: Call(
            "POST",
            f"/api/tickets/{s.ticket_a}/conversation/send",
            {"content": [{"piece": "text", "text": "Hello."}], "sender_label": "owner"},
        ),
    ),
    (
        "POST   /tickets/{t}/conversation/reset",
        lambda s: Call("POST", f"/api/tickets/{s.ticket_a}/conversation/reset"),
    ),
    (
        "GET    /tickets/{t}/worker-self",
        lambda s: Call("GET", f"/api/tickets/{s.ticket_a}/worker-self"),
    ),
    (
        "PATCH  /tickets/{t} field_values",
        lambda s: Call(
            "PATCH", f"/api/tickets/{s.ticket_a}", {"field_values": {s.gating_field: "Edited."}}
        ),
    ),
    (
        "POST   /tickets/{t}/complete/{f}",
        lambda s: Call(
            "POST", f"/api/tickets/{s.ticket_user_owned}/complete/brief", {"body": "Mine."}
        ),
    ),
    # --- the approval gate, at its ordinary address
    (
        "POST   /tickets/{t}/accept/{f}",
        lambda s: Call(
            "POST",
            f"/api/tickets/{s.ticket_a}/accept/{s.gating_field}",
            {"next_holder": _OWNER_HOLDER},
        ),
    ),
    (
        "POST   /tickets/{t}/reject",
        lambda s: Call("POST", f"/api/tickets/{s.ticket_a}/reject", {"message": "Revise this."}),
    ),
    # --- the same Ticket, through the supervisor address
    ("GET    /items/{i}/supervisor", lambda s: Call("GET", f"/api/items/{s.item_a}/supervisor")),
    (
        "GET    /items/{i}/supervisor/context",
        lambda s: Call("GET", f"/api/items/{s.item_a}/supervisor/context"),
    ),
    (
        "GET    /items/{i}/supervisor/tickets/{t}/context",
        lambda s: Call("GET", f"/api/items/{s.item_a}/supervisor/tickets/{s.ticket_a}/context"),
    ),
    (
        "GET    /items/{i}/supervisor/tickets/{t}/history",
        lambda s: Call("GET", f"/api/items/{s.item_a}/supervisor/tickets/{s.ticket_a}/history"),
    ),
    (
        "POST   /items/{i}/supervisor/tickets/{t}/restart-worker",
        lambda s: Call(
            "POST", f"/api/items/{s.item_a}/supervisor/tickets/{s.ticket_a}/restart-worker", {}
        ),
    ),
    (
        "GET    /items/{i}/supervisor/artifacts",
        lambda s: Call("GET", f"/api/items/{s.item_a}/supervisor/artifacts"),
    ),
    (
        "PUT    /items/{i}/supervisor/artifacts/{p}",
        lambda s: Call(
            "PUT", f"/api/items/{s.item_a}/supervisor/artifacts/note.md", {"content": "# Note"}
        ),
    ),
    (
        "DELETE /items/{i}/supervisor/artifacts/{p}",
        lambda s: Call("DELETE", f"/api/items/{s.item_a}/supervisor/artifacts/note.md"),
    ),
    (
        "GET    /items/{i}/supervisor/conversation/start-values",
        lambda s: Call("GET", f"/api/items/{s.item_a}/supervisor/conversation/start-values"),
    ),
    (
        "POST   /items/{i}/supervisor/conversation/reset",
        lambda s: Call("POST", f"/api/items/{s.item_a}/supervisor/conversation/reset"),
    ),
    ("GET    /items/{i}/workspace", lambda s: Call("GET", f"/api/items/{s.item_a}/workspace")),
    # --- an Outcome, through its ordinary address
    (
        "PATCH  /items/{i}",
        lambda s: Call("PATCH", f"/api/items/{s.item_a}", {"title": "A renamed"}),
    ),
    ("DELETE /items/{i}", lambda s: Call("DELETE", f"/api/items/{s.item_b}")),
    # --- collections
    (
        "PUT    /collections/outcome_tickets",
        lambda s: Call("PUT", f"/api/collections/outcome_tickets/{s.item_a}/{s.ticket_a}"),
    ),
    (
        "PUT    /collections/day_tickets",
        lambda s: Call("PUT", f"/api/collections/day_tickets/{_DAY}/{s.ticket_a}"),
    ),
    (
        "PUT    /collections/blockers",
        lambda s: Call("PUT", f"/api/collections/blockers/{s.ticket_a}/{s.ticket_b}"),
    ),
    (
        "PUT    /collections/sprint_outcomes",
        lambda s: Call("PUT", f"/api/collections/sprint_outcomes/{s.sprint_id}/{s.item_a}"),
    ),
    # --- the plan
    ("PATCH  /day/{d} focus", lambda s: Call("PATCH", f"/api/day/{_DAY}", {"focus": "One thing."})),
    (
        "PATCH  /day/{d} midday_reconciliation",
        lambda s: Call("PATCH", f"/api/day/{_DAY}", {"midday_reconciliation": "Checked."}),
    ),
    ("PATCH  /day/{d} notes", lambda s: Call("PATCH", f"/api/day/{_DAY}", {"notes": "Mine."})),
    (
        "POST   /sprints",
        lambda s: Call(
            "POST",
            "/api/sprints",
            {"name": "Later sprint", "date_start": "2026-10-05", "date_end": "2026-10-18"},
        ),
    ),
    (
        "PATCH  /sprints/{s}",
        lambda s: Call("PATCH", f"/api/sprints/{s.sprint_id}", {"name": "Renamed sprint"}),
    ),
    # --- what Khushal owns outright
    ("POST   /projects", lambda s: Call("POST", "/api/projects", {"name": "New project"})),
    (
        "PATCH  /projects/{p}",
        lambda s: Call("PATCH", "/api/projects/project_other", {"priority": "P2"}),
    ),
    (
        "PUT    /workers/{w}/launch-defaults",
        lambda s: Call(
            "PUT",
            "/api/workers/coding/launch-defaults",
            {
                "default_backend": "claude",
                "default_model": "opus[1m]",
                "default_reasoning_effort": "medium",
                "toolset_profile": "default",
            },
        ),
    ),
    (
        "PATCH  /skills/{n}",
        lambda s: Call("PATCH", "/api/skills/panels", {"description": "x"}),
    ),
    ("POST   /feedback", lambda s: Call("POST", "/api/feedback", {"body": "Some feedback."})),
    (
        "POST   /messages/send",
        lambda s: Call(
            "POST",
            "/api/messages/send",
            {"target": {"kind": "ticket", "id": s.ticket_a}, "message": "Hello."},
        ),
    ),
)


# Operations that really do admit every principal, each one checked by hand against the
# route. A row that comes out uniform and is not named here is measuring nothing — usually a
# body that fails marshalling before the guard runs — and the matrix says so rather than
# recording six identical answers as if they were evidence.
UNGUARDED_OPERATIONS: Final = frozenset(
    {
        # No guard: the ordinary Ticket read is open to every principal.
        "GET    /tickets (detail=full)",
        # No guard, despite the name: the Ticket is named in the path, not by the caller.
        "GET    /tickets/{t}/worker-self",
    }
)


def _classify(response: Any) -> str:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return "allow"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("code") == "agent_forbidden":
            return "refuse"
    return "allow"


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Seed]]:
    root = tmp_path_factory.mktemp("authority-matrix")
    template = root / "template" / "planning.db"
    seed = _seed(template)
    yield template, seed


def _answer(template: Path, work: Path, headers: dict[str, str], call: Call) -> str:
    db_path = work / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(template) + suffix)
        if source.exists():
            shutil.copy2(source, str(db_path) + suffix)
    with TestClient(_app_on(db_path)) as client:
        response = client.request(call.method, call.path, json=call.body, headers=headers)
    return _classify(response)


def _render(rows: list[tuple[str, dict[str, str]]]) -> str:
    width = max(len(name) for name, _ in rows)
    columns = [max(len(p), 6) for p in PRINCIPALS]
    header = (
        "operation".ljust(width)
        + "  "
        + "  ".join(p.ljust(w) for p, w in zip(PRINCIPALS, columns, strict=True))
    )
    lines = [header, "-" * len(header)]
    for name, answers in rows:
        lines.append(
            name.ljust(width)
            + "  "
            + "  ".join(answers[p].ljust(w) for p, w in zip(PRINCIPALS, columns, strict=True))
        )
    return "\n".join(lines) + "\n"


def test_the_authority_matrix_has_not_moved(seeded: tuple[Path, Seed], tmp_path: Path) -> None:
    template, seed = seeded
    headers_by_principal = _principal_headers(seed)
    rows: list[tuple[str, dict[str, str]]] = []
    for index, (name, build) in enumerate(OPERATIONS):
        call = build(seed)
        answers = {
            principal: _answer(
                template, tmp_path / f"{index}-{principal}", headers_by_principal[principal], call
            )
            for principal in PRINCIPALS
        }
        rows.append((name, answers))

    uniformly_admitted = sorted(
        name
        for name, answers in rows
        if set(answers.values()) == {"allow"} and name not in UNGUARDED_OPERATIONS
    )
    assert not uniformly_admitted, (
        "these rows admitted every principal and are not known to be unguarded, so they "
        f"measure nothing — check the request reaches the guard: {uniformly_admitted}"
    )

    rendered = _render(rows)
    if os.environ.get(_REWRITE_ENV) == "1":
        _SNAPSHOT.write_text(rendered, encoding="utf-8")
        pytest.skip(f"{_REWRITE_ENV}=1: snapshot rewritten, re-run to check it")

    assert _SNAPSHOT.exists(), f"run with {_REWRITE_ENV}=1 to record the first snapshot"
    expected = _SNAPSHOT.read_text(encoding="utf-8")
    if rendered != expected:
        pytest.fail(_diff(expected, rendered))


def _cells(text: str) -> dict[tuple[str, str], str]:
    """Read a rendered matrix back, using the header to find where the columns start."""
    lines = text.splitlines()
    header = lines[0]
    name_width = header.index(PRINCIPALS[0])
    out: dict[tuple[str, str], str] = {}
    for line in lines[2:]:
        if not line.strip():
            continue
        name = line[:name_width].strip()
        answers = line[name_width:].split()
        assert len(answers) == len(PRINCIPALS), f"unreadable row: {line!r}"
        for principal, answer in zip(PRINCIPALS, answers, strict=True):
            out[(name, principal)] = answer
    return out


def _diff(expected: str, actual: str) -> str:
    """Name every cell that moved, and in which direction."""
    before, after = _cells(expected), _cells(actual)
    moved = [
        f"  {name} [{principal}]: {before.get(key, '-')} -> {after.get(key, '-')}"
        for key in sorted(set(before) | set(after))
        for name, principal in [key]
        if before.get(key) != after.get(key)
    ]
    return f"the authority matrix moved in {len(moved)} cells:\n" + "\n".join(
        moved or ["  (the row set changed)"]
    )
