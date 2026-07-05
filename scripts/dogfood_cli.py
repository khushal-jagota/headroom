"""Dogfood Level A (§18.3 item 35; the Level A walkthrough is spelled out in §18.5).

A single scripted pass over the whole ticket lifecycle against a demo-seeded test
server. Agent actions go through the real ``plan`` CLI (a subprocess, always with
``--json``); human actions go through the HTTP API with no ``X-Plan-*`` headers, which
is how the server classifies a request as the human. Each of the thirteen steps asserts
the exact response and prints one ``PASS step NN — name`` line; the first mismatch prints
a ``FAIL`` diagnostic to stderr and exits non-zero.

Env contract: ``PLAN_SERVER_URL`` (the running server) and ``PLAN_DB_PATH`` (its SQLite
file) are BOTH required; either missing is a clear message and exit 2.

The one DB read: the claim token lives only in ``tickets.claim_lock`` and is deliberately
never echoed by any API view (``tickets/views.py`` omits it and exposes the derived
``claim_active`` instead), so after the dispatcher tick this script opens the DB
read-only (``file:...?mode=ro``) to recover the token for exactly one purpose:
impersonating the fake-spawned worker. That is harness scaffolding standing in for the
env block the real spawn adapter hands the worker process (``core/adapters/real.py``).
No writes and no other DB reads happen here.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "plan"

TITLE = "Dogfood walkthrough ticket."
BLOCKER_TITLE = "Dogfood blocker ticket."
SUCCESS_1 = "Draft success: the walkthrough completes the demo flow."       # superseded
SUCCESS_2 = (
    "# Success\n\nThe dogfood walkthrough completes every gate.\n\n"
    "- superseded proposal exercised\n- grant pair on every accept\n"
)
RECAP_1 = "Recap v1: success accepted; approach drafting next."
RECAP_2 = "Recap v2: approach proposed; awaiting the human gate."
APPROACH_1 = "Approach: drive the demo slice through every gate via the CLI."
APPROACH_EDITED = (
    "Approach (edited): drive every gate via the CLI, asserting each response. "
)   # trailing space deliberate — stored and asserted byte-for-byte (mirrors E25)
PLAN_BODY = "Plan:\n1. propose result with the claim env\n2. close the run\n3. human approves\n"
RESULT_BODY = "Result: the walkthrough completed; every gate asserted."
RUN_SUMMARY = "Dogfood run complete."

# Set once in main(). The walkthrough date is the SERVER's planning date (read once
# from GET /api/day/today), never the local wall clock, so the script tracks the
# server clock — real or PLAN_FAKE_NOW — and step 1's deadline, step 9's day path,
# and the demo-seeded day all agree byte-for-byte.
SERVER_URL = ""
DB_PATH = ""
SERVER_TODAY = ""


@dataclass
class Walk:
    """The mutable context threaded through the steps."""

    ticket_id: str = ""
    blocker_id: str = ""
    run_id: str = ""
    claim: str = ""


# --- diagnostics and assertions ------------------------------------------------


def fail(step: int, name: str, expected: object, got: object, *, code: int = 1) -> NoReturn:
    """First mismatch wins: print the FAIL diagnostic and exit. code 1 = assertion
    mismatch, code 2 = environment / transport / subprocess failure."""
    print(f"FAIL step {step:02d} — {name}", file=sys.stderr)
    print(f"  expected: {expected!r}", file=sys.stderr)
    print(f"  got:      {got!r}", file=sys.stderr)
    sys.exit(code)


def ok(step: int, name: str) -> None:
    print(f"PASS step {step:02d} — {name}", flush=True)


def expect(step: int, name: str, got: object, expected: object) -> None:
    if got != expected:
        fail(step, name, expected, got)


def expect_true(step: int, name: str, cond: bool, got: object) -> None:
    if not cond:
        fail(step, name, "condition true", got)


# --- transports ----------------------------------------------------------------


def scrubbed_env() -> dict[str, str]:
    """os.environ minus every ambient PLAN_* key (same rule as conftest._scrubbed_env)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}


def cli(
    step: int,
    args: list[str],
    *,
    ticket_id: str | None = None,
    run_id: str | None = None,
    claim: str | None = None,
    stdin: str | None = None,
) -> Any:
    """Run the real CLI with a scrubbed env plus only the pins this step needs. The CLI
    always sends X-Plan-Actor=agent; run/claim headers only when their env is set."""
    env = scrubbed_env()
    env["PLAN_SERVER_URL"] = SERVER_URL
    if ticket_id is not None:
        env["PLAN_TICKET_ID"] = ticket_id
    if run_id is not None:
        env["PLAN_RUN_ID"] = run_id
    if claim is not None:
        env["PLAN_CLAIM"] = claim
    proc = subprocess.run(
        [str(PLAN_BIN), *args, "--json"],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=30,
    )
    if proc.returncode != 0:
        fail(step, "cli " + " ".join(args), "exit 0", proc.stderr.strip(), code=2)
    return json.loads(proc.stdout)


def human(step: int, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Header-less HTTP request = the human (authctx classifies no X-Plan-* as human)."""
    try:
        resp = httpx.request(method, SERVER_URL + path, json=body, timeout=10.0)
    except httpx.HTTPError as exc:
        fail(step, f"{method} {path}", "transport ok", str(exc), code=2)
    if resp.status_code >= 300:
        fail(step, f"{method} {path}", "status < 300", f"{resp.status_code}: {resp.text}", code=2)
    return resp.json()


def api_get(step: int, path: str) -> Any:
    try:
        resp = httpx.get(SERVER_URL + path, timeout=10.0)
    except httpx.HTTPError as exc:
        fail(step, f"GET {path}", "transport ok", str(exc), code=2)
    if resp.status_code >= 300:
        fail(step, f"GET {path}", "status < 300", f"{resp.status_code}: {resp.text}", code=2)
    return resp.json()


def read_claim_lock(step: int, ticket_id: str) -> str:
    """The one sanctioned DB read: recover the claim token the API never echoes."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT claim_lock FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or row[0] is None:
        fail(step, "read_claim_lock", "non-null claim_lock", None, code=2)
    return str(row[0])


def pickup_ids(step: int) -> list[str]:
    return [e["ticket_id"] for e in cli(step, ["queue", "pickup"])["pickup"]]


# --- the thirteen steps --------------------------------------------------------


def step_01_create(w: Walk) -> None:
    d = cli(1, ["ticket", "create", "--title", TITLE, "--priority", "P0",
                "--deadline", SERVER_TODAY])
    expect(1, "create.state", d["state"], "needs_success")
    expect(1, "create.ceiling", d["ceiling"], "needs_success")
    expect(1, "create.at_cap", d["at_cap"], "propose")
    expect(1, "create.priority", d["priority"], "P0")
    expect(1, "create.deadline", d["deadline"], SERVER_TODAY)
    w.ticket_id = d["id"]
    ok(1, "create")


def step_02_success_parks(w: Walk) -> None:
    d = cli(2, ["propose", "success", "--body-file", "-"], ticket_id=w.ticket_id, stdin=SUCCESS_1)
    expect(2, "success1.state", d["state"], "needs_success")
    expect(2, "success1.proposal", d["fields"]["success"]["proposal"]["body"], SUCCESS_1)
    expect(2, "success1.value", d["fields"]["success"]["value"], None)
    ok(2, "propose success #1 parks")


def step_03_supersede(w: Walk) -> None:
    d = cli(3, ["propose", "success", "--body-file", "-"], ticket_id=w.ticket_id, stdin=SUCCESS_2)
    expect(3, "success2.state", d["state"], "needs_success")
    expect(3, "success2.proposal", d["fields"]["success"]["proposal"]["body"], SUCCESS_2)
    events = api_get(3, f"/api/tickets/{w.ticket_id}/events")["events"]
    want = {"field": "success", "replaced_body": SUCCESS_1}
    hits = [e for e in events if e["kind"] == "proposal_superseded" and e["payload"] == want]
    expect_true(
        3, "proposal_superseded event",
        len(hits) >= 1, [e for e in events if e["kind"] == "proposal_superseded"],
    )
    ok(3, "propose success #2 supersedes")


def step_04_accept_success(w: Walk) -> None:
    d = human(
        4, "POST", f"/api/tickets/{w.ticket_id}/accept/success",
        {"next_ceiling": "none", "at_cap": "propose"},
    )
    expect(4, "accept.state", d["state"], "needs_approach")
    expect(4, "accept.success.value", d["fields"]["success"]["value"], SUCCESS_2)
    expect(4, "accept.success.proposal", d["fields"]["success"]["proposal"], None)
    expect(4, "accept.ceiling", d["ceiling"], "needs_approach")
    expect(4, "accept.at_cap", d["at_cap"], "propose")
    ok(4, "human accept success with grant pair")


def step_05_recap(w: Walk) -> None:
    d = cli(5, ["recap", "--body-file", "-"], ticket_id=w.ticket_id, stdin=RECAP_1)
    expect(5, "recap1", d["recap"], RECAP_1)
    expect(5, "recap1.state", d["state"], "needs_approach")
    d = cli(5, ["recap", "--body-file", "-"], ticket_id=w.ticket_id, stdin=RECAP_2)
    expect(5, "recap2", d["recap"], RECAP_2)
    expect(5, "recap2.state", d["state"], "needs_approach")
    ok(5, "recap write then overwrite")


def step_06_approach(w: Walk) -> None:
    d = cli(6, ["propose", "approach", "--body-file", "-"], ticket_id=w.ticket_id, stdin=APPROACH_1)
    expect(6, "approach.parks.state", d["state"], "needs_approach")
    expect(6, "approach.proposal", d["fields"]["approach"]["proposal"]["body"], APPROACH_1)
    d = human(
        6, "POST", f"/api/tickets/{w.ticket_id}/accept/approach",
        {"edited_body": APPROACH_EDITED, "next_ceiling": "none", "at_cap": "propose"},
    )
    expect(6, "approach.accept.state", d["state"], "needs_plan")
    expect(6, "approach.value", d["fields"]["approach"]["value"], APPROACH_EDITED)
    expect(6, "approach.accept.proposal", d["fields"]["approach"]["proposal"], None)
    expect(6, "approach.ceiling", d["ceiling"], "needs_plan")
    expect(6, "approach.at_cap", d["at_cap"], "propose")
    events = api_get(6, f"/api/tickets/{w.ticket_id}/events")["events"]
    want = {"field": "approach", "body": APPROACH_EDITED, "resolved_by": "human", "edited": True}
    hits = [e for e in events if e["kind"] == "proposal_accepted" and e["payload"] == want]
    expect_true(
        6, "proposal_accepted event",
        len(hits) >= 1, [e for e in events if e["kind"] == "proposal_accepted"],
    )
    ok(6, "propose approach; human edit-accepts")


def step_07_plan(w: Walk) -> None:
    d = cli(7, ["propose", "plan", "--body-file", "-"], ticket_id=w.ticket_id, stdin=PLAN_BODY)
    expect(7, "plan.parks.state", d["state"], "needs_plan")
    expect(7, "plan.proposal", d["fields"]["plan"]["proposal"]["body"], PLAN_BODY)
    d = human(
        7, "POST", f"/api/tickets/{w.ticket_id}/accept/plan",
        {"next_ceiling": "needs_review", "at_cap": "propose"},
    )
    expect(7, "plan.accept.state", d["state"], "in_progress")
    expect(7, "plan.value", d["fields"]["plan"]["value"], PLAN_BODY)
    expect(7, "plan.accept.proposal", d["fields"]["plan"]["proposal"], None)
    expect(7, "plan.ceiling", d["ceiling"], "needs_review")
    expect(7, "plan.at_cap", d["at_cap"], "propose")
    ok(7, "propose plan; human accepts into in_progress")


def step_08_day(w: Walk) -> None:
    cli(8, ["day", "add-ticket", w.ticket_id])
    d = cli(8, ["day", "show"])
    expect_true(
        8, "day contains ticket",
        w.ticket_id in [t["id"] for t in d["tickets"]], [t["id"] for t in d["tickets"]],
    )
    cli(8, ["day", "remove-ticket", w.ticket_id])
    d = cli(8, ["day", "show"])
    expect_true(
        8, "day omits ticket after remove",
        w.ticket_id not in [t["id"] for t in d["tickets"]], [t["id"] for t in d["tickets"]],
    )
    d = cli(8, ["ticket", "show"], ticket_id=w.ticket_id)
    expect(8, "day.remove.state.untouched", d["state"], "in_progress")
    ok(8, "day add / remove")


def step_09_day_plan(w: Walk) -> None:
    d = human(9, "POST", f"/api/day/{SERVER_TODAY}/plan/accept", {"node": 1})
    expect(9, "plan.child1.status", d["plan"]["children"][1]["status"], "accepted")
    expect(9, "plan.root.status", d["plan"]["root"]["status"], "accepted")
    ok(9, "day-plan accept")


def step_10_blocking(w: Walk) -> None:
    ids = pickup_ids(10)
    expect_true(10, "pickup baseline holds ticket", w.ticket_id in ids, ids)
    w.blocker_id = cli(10, ["ticket", "create", "--title", BLOCKER_TITLE])["id"]
    d = cli(10, ["link", "add", w.blocker_id, w.ticket_id, "--kind", "blocks"])
    expect(10, "link.from_id", d["from_id"], w.blocker_id)
    expect(10, "link.to_id", d["to_id"], w.ticket_id)
    expect(10, "link.kind", d["kind"], "blocks")
    ids = pickup_ids(10)
    expect_true(10, "blocked ticket absent from pickup", w.ticket_id not in ids, ids)
    detail = api_get(10, f"/api/tickets/{w.ticket_id}")
    expect(10, "blocked flag", detail["blocked"], True)
    d = human(10, "POST", f"/api/tickets/{w.blocker_id}/state", {"to": "done"})
    expect(10, "blocker.state", d["state"], "done")
    ids = pickup_ids(10)
    expect_true(10, "pickup restored after unblock", w.ticket_id in ids, ids)
    ok(10, "blocking round-trip")


def step_11_claim(w: Walk) -> None:
    report = human(11, "POST", "/api/test/tick-dispatcher")
    hits = [e for e in report["spawned"] if e["ticket_id"] == w.ticket_id]
    expect_true(11, "our ticket in spawned", len(hits) == 1, report["spawned"])
    w.run_id = hits[0]["run_id"]
    detail = api_get(11, f"/api/tickets/{w.ticket_id}")
    expect(11, "claim_active", detail["claim_active"], True)
    w.claim = read_claim_lock(11, w.ticket_id)
    expect_true(11, "claim token non-empty", bool(w.claim), w.claim)
    ok(11, "dispatcher claim")


def step_12_worker(w: Walk) -> None:
    pins = {"ticket_id": w.ticket_id, "run_id": w.run_id, "claim": w.claim}
    d = cli(12, ["run", "heartbeat"], **pins)
    expect(12, "heartbeat.run.id", d["run"]["id"], w.run_id)
    expires = d["claim_expires"]
    expect_true(12, "heartbeat.claim_expires int", isinstance(expires, int), expires)
    d = cli(12, ["propose", "result", "--body-file", "-"], **pins, stdin=RESULT_BODY)
    expect(12, "result.state", d["state"], "needs_review")
    expect(12, "result.value", d["fields"]["result"]["value"], RESULT_BODY)
    expect(12, "result.proposal", d["fields"]["result"]["proposal"], None)
    expect(12, "result.ceiling.unchanged", d["ceiling"], "needs_review")
    expect(12, "result.at_cap.unchanged", d["at_cap"], "propose")
    d = cli(12, ["run", "close", "--outcome", "done", "--summary", "-"], **pins, stdin=RUN_SUMMARY)
    expect(12, "close.run.status", d["run"]["status"], "done")
    expect(12, "close.run.summary", d["run"]["summary"], RUN_SUMMARY)
    detail = api_get(12, f"/api/tickets/{w.ticket_id}")
    expect(12, "claim cleared after close", detail["claim_active"], False)
    ok(12, "claim-carrying worker writes")


def step_13_approve(w: Walk) -> None:
    d = human(13, "POST", f"/api/tickets/{w.ticket_id}/approve", {})
    expect(13, "approve.state", d["state"], "done")
    ok(13, "human approve")


def main() -> int:
    global SERVER_URL, DB_PATH, SERVER_TODAY
    server_url = os.environ.get("PLAN_SERVER_URL", "").strip()
    db_path = os.environ.get("PLAN_DB_PATH", "").strip()
    if not server_url or not db_path:
        print(
            "dogfood_cli: PLAN_SERVER_URL and PLAN_DB_PATH are both required", file=sys.stderr
        )
        return 2
    SERVER_URL = server_url.rstrip("/")
    DB_PATH = db_path
    SERVER_TODAY = str(api_get(0, "/api/day/today")["id"]).removeprefix("day_")

    w = Walk()
    step_01_create(w)
    step_02_success_parks(w)
    step_03_supersede(w)
    step_04_accept_success(w)
    step_05_recap(w)
    step_06_approach(w)
    step_07_plan(w)
    step_08_day(w)
    step_09_day_plan(w)
    step_10_blocking(w)
    step_11_claim(w)
    step_12_worker(w)
    step_13_approve(w)

    print("DOGFOOD LEVEL A: 13/13 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
