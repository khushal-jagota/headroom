#!/usr/bin/env python3
"""T10 domain-API self-smoke. Straight-line, assert-based, numbered `ok NN` prints.
Not under tests/ — never touched by pytest or the §18.2 scan. Run from the repo root:

    cd /Users/khushaljagota/.hermes/planning-v2 && .venv/bin/python \
        orchestration/tickets/T10-domain-apis/smoke.py

Boots a real test-mode server (subprocess `plan serve`) on a temp DB against the fake
clock (planning date 2026-07-04) and drives the golden path end to end over HTTP:
create -> propose -> accept-with-grant; item lifecycle; sprint + current view; day
add/remove with `today`; links + cycle reject; board / queues / copy-text / events /
ideas; and the (H) agent-forbidden guards. Any failed assertion raises -> traceback +
non-zero exit; the try/finally always kills the server and removes the temp dir. Exits
0 with a final SMOKE PASS line on success."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
FAKE_NOW = "2026-07-04T12:00:00"
PORT = 8788

HUMAN: dict[str, str] = {}
AGENT = {"X-Plan-Actor": "planner-main"}
CLAIMED = {"X-Plan-Run-Id": "run_x", "X-Plan-Claim": "claim_x"}

_CHECK = 0


def ok(msg: str) -> None:
    global _CHECK
    _CHECK += 1
    print(f"ok {_CHECK:02d} — {msg}")


def free_or(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as fallback:
        fallback.bind(("127.0.0.1", 0))
        return int(fallback.getsockname()[1])


def wait_ready(base: str, log_path: Path, budget: float = 15.0, interval: float = 0.25) -> None:
    deadline = time.time() + budget
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/api/meta", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    sys.stderr.write(log_path.read_text() if log_path.exists() else "(no server log)\n")
    raise AssertionError("server did not become ready within budget")


def run_smoke(client: httpx.Client) -> None:
    # 1. Create ticket (human): R2 defaults, no claim, token never echoed.
    r = client.post("/api/tickets", json={"title": "Golden path", "priority": "P1"}, headers=HUMAN)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["state"] == "needs_success" and t["ceiling"] == "needs_success"
    assert t["at_cap"] == "propose" and t["priority"] == "P1"
    assert t["id"].startswith("t_") and t["claim_active"] is False and "claim_lock" not in t
    t1 = t["id"]
    ok("create ticket: R2 defaults, claim_active False, claim_lock omitted")

    # 2. Plain-agent propose at ceiling parks; actor passthrough (§7.6).
    r = client.post(f"/api/tickets/{t1}/propose/success", json={"body": "S-cond"}, headers=AGENT)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["state"] == "needs_success"
    assert t["fields"]["success"]["proposal"]["body"] == "S-cond"
    assert t["fields"]["success"]["proposal"]["proposed_by"] == "planner-main"
    ok("plain-agent propose parks at cap; actor passthrough")

    # 3. Approvals queue sees the pending gating proposal.
    r = client.get("/api/queues")
    assert r.status_code == 200, r.text
    approvals = r.json()["approvals"]
    assert any(
        e["entity_id"] == t1 and e["kind"] == "success" and e["entity_type"] == "ticket"
        for e in approvals
    )
    ok("queues approvals contains the ticket success proposal")

    # 4. Accept without the grant pair -> engine's grant_missing (no route pre-validation).
    r = client.post(f"/api/tickets/{t1}/accept/success", json={}, headers=HUMAN)
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "grant_missing"
    assert err["detail"]["missing"] == ["next_ceiling", "at_cap"]
    ok("accept without pair -> grant_missing lists both halves")

    # 5. Accept with grant pair advances; approvals entry clears.
    r = client.post(
        f"/api/tickets/{t1}/accept/success",
        json={"next_ceiling": "needs_plan", "at_cap": "propose"},
        headers=HUMAN,
    )
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["state"] == "needs_approach" and t["ceiling"] == "needs_plan"
    assert t["at_cap"] == "propose" and t["fields"]["success"]["value"] == "S-cond"
    assert t["fields"]["success"]["proposal"] is None
    r = client.get("/api/queues")
    assert not any(e["entity_id"] == t1 for e in r.json()["approvals"])
    ok("accept with pair advances; approvals entry gone")

    # 6. Auto-accept below ceiling changes neither ceiling nor at_cap.
    r = client.post(f"/api/tickets/{t1}/propose/approach", json={"body": "A"}, headers=AGENT)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["state"] == "needs_plan" and t["fields"]["approach"]["value"] == "A"
    assert t["ceiling"] == "needs_plan" and t["at_cap"] == "propose"
    ok("auto-accept below ceiling; grant unchanged")

    # 7. (H) route with agent headers -> agent_forbidden (both agent classes).
    r = client.post(f"/api/tickets/{t1}/state", json={"to": "in_progress"}, headers=CLAIMED)
    assert r.status_code == 400 and r.json()["error"]["code"] == "agent_forbidden", r.text
    r = client.post(f"/api/tickets/{t1}/drop", headers=AGENT)
    assert r.status_code == 400 and r.json()["error"]["code"] == "agent_forbidden", r.text
    ok("(H) state/drop rejected for claimed and plain agents")

    # 8. Edit-accept with "none" ceiling stores edited text; ceiling = the new state.
    r = client.post(f"/api/tickets/{t1}/propose/plan", json={"body": "P-draft"}, headers=AGENT)
    assert r.status_code == 200 and r.json()["state"] == "needs_plan", r.text
    r = client.post(
        f"/api/tickets/{t1}/accept/plan",
        json={"edited_body": "P-final", "next_ceiling": "none", "at_cap": "stop"},
        headers=HUMAN,
    )
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["state"] == "in_progress" and t["fields"]["plan"]["value"] == "P-final"
    assert t["ceiling"] == "in_progress" and t["at_cap"] == "stop"
    ok("edit-accept with 'none' ceiling stores edited text; ceiling = new state")

    # 9. Recap write past needs_success.
    r = client.put(f"/api/tickets/{t1}/recap", json={"body": "recap v1"}, headers=AGENT)
    assert r.status_code == 200 and r.json()["recap"] == "recap v1", r.text
    ok("recap write")

    # 10. Item lifecycle + A7 deadline marshalling.
    r = client.post("/api/items", json={"title": "Item A", "project": "Vylo"}, headers=HUMAN)
    assert r.status_code == 200 and r.json()["status"] == "todo", r.text
    i1 = r.json()["id"]
    r = client.patch(f"/api/items/{i1}", json={"status": "active"}, headers=AGENT)
    assert r.status_code == 200 and r.json()["status"] == "active", r.text
    r = client.post(f"/api/items/{i1}/propose-status", json={"to": "done"}, headers=AGENT)
    assert r.status_code == 200 and r.json()["status_proposal"]["to_status"] == "done", r.text
    r = client.post(f"/api/items/{i1}/accept-status", json={}, headers=CLAIMED)
    assert r.status_code == 400 and r.json()["error"]["code"] == "agent_forbidden", r.text
    r = client.post(f"/api/items/{i1}/accept-status", json={}, headers=HUMAN)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["status_proposal"] is None
    r = client.patch(f"/api/items/{i1}", json={"deadline": "not-a-date"}, headers=HUMAN)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation", r.text
    r = client.patch(f"/api/items/{i1}", json={"deadline": 20260704}, headers=HUMAN)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation", r.text
    ok("item create/transition/propose/accept; (H) coverage; A7 deadline validation")

    # 11. Sprint + current view (rollups, loose tickets).
    r = client.post(
        "/api/sprints",
        json={"name": "S1", "date_start": "2026-07-01", "date_end": "2026-07-12"},
        headers=HUMAN,
    )
    assert r.status_code == 200, r.text
    sp1 = r.json()["id"]
    r = client.patch(f"/api/items/{i1}", json={"sprint_id": sp1}, headers=HUMAN)
    assert r.status_code == 200, r.text
    r = client.patch(f"/api/tickets/{t1}", json={"sprint_id": sp1}, headers=HUMAN)
    assert r.status_code == 200, r.text
    r = client.get("/api/sprint/current")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["sprint"]["id"] == sp1
    assert view["groups"]["done"][0]["id"] == i1
    assert isinstance(view["groups"]["done"][0]["rollup"], dict)
    assert any(lt["id"] == t1 for lt in view["loose_tickets"])
    ok("sprint current view: done group rollup + loose ticket")

    # 12. Day add/remove via `today`; A6 compact-date resolution.
    r = client.post("/api/day/today/tickets", json={"ticket_id": t1}, headers=HUMAN)
    assert r.status_code == 200, r.text
    day = r.json()
    assert day["id"] == "day_2026-07-04" and [x["id"] for x in day["tickets"]] == [t1]
    r = client.get("/api/day/2026-07-04")
    assert r.status_code == 200 and [x["id"] for x in r.json()["tickets"]] == [t1], r.text
    r = client.get("/api/day/20260704")
    assert r.status_code == 200 and r.json()["id"] == "day_2026-07-04", r.text
    r = client.delete(f"/api/day/today/tickets/{t1}")
    assert r.status_code == 200 and r.json()["tickets"] == [], r.text
    r = client.get("/api/day/2026-07-04")
    assert r.status_code == 200 and r.json()["tickets"] == [], r.text
    ok("day add/remove with `today`; A6 compact ISO resolves canonically")

    # 13. Links add + cycle reject.
    r = client.post("/api/tickets", json={"title": "Blocker"}, headers=HUMAN)
    assert r.status_code == 200, r.text
    t2 = r.json()["id"]
    r = client.post(
        "/api/links", json={"from_id": t2, "to_id": t1, "kind": "blocks"}, headers=HUMAN
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"from_id": t2, "to_id": t1, "kind": "blocks"}
    r = client.post(
        "/api/links", json={"from_id": t1, "to_id": t2, "kind": "blocks"}, headers=HUMAN
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "link_cycle", r.text
    ok("link add echo; reverse edge -> link_cycle")

    # 14. Full ticket read.
    r = client.get(f"/api/tickets/{t1}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["blocked"] is True
    assert {"from_id": t2, "to_id": t1, "kind": "blocks"} in detail["links"]
    assert detail["effective_sprint_id"] == sp1
    assert detail["run_summary"]["total"] == 0 and detail["day_ids"] == []
    ok("full ticket read: blocked, links, effective sprint, empty runs/days")

    # 15. Pickup queue: t2 eligible, t1 (blocked, at-ceiling-stop) not.
    r = client.get("/api/queues")
    pickup_ids = [e["ticket_id"] for e in r.json()["pickup"]]
    assert t2 in pickup_ids and t1 not in pickup_ids
    ok("pickup contains t2, excludes t1")

    # 16. Overdue queue.
    r = client.patch(f"/api/tickets/{t2}", json={"deadline": "2026-07-01"}, headers=HUMAN)
    assert r.status_code == 200, r.text
    r = client.get("/api/queues")
    assert any(
        e["id"] == t2 and e["entity_type"] == "ticket" and e["deadline"] == "2026-07-01"
        for e in r.json()["overdue"]
    )
    ok("overdue contains t2 with entity_type + deadline")

    # 17. Board: six columns in STATE_ORDER, no dropped; exact card key set.
    r = client.get("/api/board")
    board = r.json()
    assert [c["state"] for c in board["columns"]] == [
        "needs_success", "needs_approach", "needs_plan", "in_progress", "needs_review", "done"
    ]
    in_prog = next(c for c in board["columns"] if c["state"] == "in_progress")
    card = next(c for c in in_prog["cards"] if c["id"] == t1)
    assert set(card.keys()) == {
        "id", "title", "priority", "deadline", "project",
        "has_pending_proposal", "has_running_claim",
    }
    assert card["has_pending_proposal"] is False and card["has_running_claim"] is False
    ok("board: 6 columns, exact card keys, both flags False")

    # 18. Copy-text block.
    r = client.get(f"/api/tickets/{t1}/copy-text")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain"), r.text
    body = r.text
    assert "Golden path" in body and "state: in_progress" in body
    assert "P-final" in body and f"{t2} -> {t1}" in body
    ok("copy-text: title/state/field value/link line present")

    # 19. Events feed.
    r = client.get(f"/api/tickets/{t1}/events")
    events = r.json()["events"]
    kinds = {e["kind"] for e in events}
    assert {"ticket_created", "proposal_filed", "proposal_accepted", "state_changed"} <= kinds
    ids = [e["id"] for e in events]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    ok("events feed: required kinds present, ids strictly ascending")

    # 20. Ideas.
    r = client.post("/api/ideas", json={"title": "An idea"}, headers=HUMAN)
    assert r.status_code == 200 and r.json()["id"].startswith("idea_"), r.text
    idea_id = r.json()["id"]
    r = client.get("/api/ideas")
    ideas = r.json()["ideas"]
    assert ideas and ideas[0]["id"] == idea_id
    ok("idea create + list newest first")

    # 21. Day-plan (H) guard fires before the no-plan validation.
    r = client.post("/api/day/today/plan/reject-all", json={}, headers=CLAIMED)
    assert r.status_code == 400 and r.json()["error"]["code"] == "agent_forbidden", r.text
    r = client.post("/api/day/today/plan/reject-all", json={}, headers=HUMAN)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation", r.text
    ok("plan reject-all: agent_forbidden before 'day has no plan' validation")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="t10-smoke-"))
    logf = (tmp / "server.log").open("w")
    procs: list[subprocess.Popen[bytes]] = []
    client: httpx.Client | None = None
    try:
        port = free_or(PORT)
        base = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": FAKE_NOW,
            "PLAN_DB_PATH": str(tmp / "planning.db"),
            "PLAN_PORT": str(port),
            "PLAN_LOGS_DIR": str(tmp / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp / "dispatcher.lock"),
        }
        proc = subprocess.Popen(
            [str(REPO / ".venv/bin/plan"), "serve"],
            cwd=str(REPO),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        procs.append(proc)
        wait_ready(base, tmp / "server.log")
        ok(f"live server booted on {port}; /api/meta 200")

        client = httpx.Client(base_url=base, timeout=10.0)
        run_smoke(client)

        # 22. Clean shutdown.
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=15)
        assert proc.returncode == 0, f"returncode {proc.returncode}"
        ok("SIGINT terminates the live server with returncode 0")

        print(f"SMOKE PASS ({_CHECK} checks)")
        return 0
    finally:
        if client is not None:
            client.close()
        for p in procs:
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
        try:
            logf.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
