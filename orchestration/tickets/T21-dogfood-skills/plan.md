# T21 plan — Dogfood Level A script (item 35) + the four skills documents

Deliverables (only these files are created; nothing else is touched):

1. `scripts/dogfood_cli.py`
2. `tests/e2e/test_dogfood.py` (exactly one test: `test_e35_dogfood_level_a`)
3. `skills/planning-worker.md`, `skills/planning-boundary.md`, `skills/planner-main.md`,
   `skills/planning-executor.md` (each ≤ 150 lines by `wc -l`)

No conftest changes (T19/T20 work sibling e2e files concurrently). No `src/` changes.
Every recon constraint (A–H) was verified against the code; none contradicts it. Two
refinements are noted in §4 (R1, R2).

Verified code anchors the whole plan hangs on:

| Fact | Anchor |
|---|---|
| CLI always sends `X-Plan-Actor` (default `agent`); run/claim headers only when env set | `src/planner/cli/http.py:33-42` |
| Header-less HTTP = human; human-only routes call `reject_agents` | `src/planner/core/authctx.py:46-73,184-193`; `tickets/api.py:307,327,359,390` |
| `claim_lock` never echoed by any view; `claim_active` derived instead | `src/planner/tickets/views.py:38-62` |
| New-ticket grant defaults `ceiling=needs_success, at_cap=propose` | `src/planner/tickets/data.py:139-158`; SPEC §4.3 (R2) |
| `proposal_superseded` payload `{field, replaced_body}` | `src/planner/tickets/logic/resolution.py:125-131` |
| `proposal_accepted` payload `{field, body, resolved_by, edited}` | `resolution.py:63-73,176-186` |
| Grant pair required on gating accept; `"none"` → ceiling = newly entered state; later ceiling must be ≥ new state | `src/planner/tickets/logic/machine.py:75-100`; SPEC §4.4.7 |
| Auto-accept changes neither ceiling nor at_cap | `resolution.py:108-117` (grant=None); SPEC §4.4.7, item 36 |
| Recap rejected at `needs_success`, allowed from `needs_approach` | `src/planner/tickets/logic/admission.py:63-69`; SPEC §3.3 |
| Eligibility: not done/dropped/needs_review, not blocked/auto_blocked/claimed, gating not pending, target ≤ ceiling OR at-ceiling+propose | `src/planner/dispatch/logic/eligibility.py:36-59`; SPEC §7.2 |
| Ordering: P0 first, deadline asc NULLs last, created_at asc | `src/planner/dispatch/logic/ordering.py`; SPEC §7.2 |
| Tick report shape `{skipped, reclaimed, timed_out, spawned:[{ticket_id, run_id, pid}], spawn_failed}` | `src/planner/dispatch/runtime.py:126-218` |
| Budget = `max_runs(2) − active running` | `runtime.py:164-176`; SPEC §7.3 (R7) |
| Test mode: background loops never run; ticks only via `/api/test/tick-dispatcher`; fake spawn adapter (`auto`→fake) | `src/planner/core/server.py:91-99`; `core/adapters/registry.py:35-47`; `core/testmode.py:80-86` |
| `close_run` clears the lease + breaker only — never ticket state | `src/planner/dispatch/data.py:113-217` |
| Blocks direction: `from` = blocker, `to` = blocked; blocked while source not done/dropped | `src/planner/core/links.py:134-156`; demo `t8 -blocks-> t5` (`seed/demo.py:111`); SPEC §3.6 |
| Human state jump to any different, non-dropped state | `resolution.py:202-211`; `tickets/api.py:385-392`; SPEC §4.3 |
| Days materialize on read at the server planning date; `today` resolves via boundary hour | `src/planner/days/api.py:52-79`; SPEC §3.4, §6.1 |
| Demo seed uses the REAL clock (`datetime.now()` / `time.time()`), not the injected clock | `src/planner/seed/demo.py:29-30` |
| e2e server clock: `PLAN_FAKE_NOW=2026-07-04T12:00:00`, boundary hour 5 → planning date 2026-07-04 | `tests/e2e/conftest.py:31,87`; SPEC §6.1 |
| Verify invokes `pytest tests/e2e`; skip-scan covers `tests/` only; item 35 anchor `test_e35` scored as exactly-one-match | `scripts/verify.py:114,136-141`; `scripts/verify_lib.py:80,206-224` |
| Real boundary adapter prompt/reply contract (grounds `planning-boundary.md`) | `src/planner/core/adapters/real.py:82-150`; `days/logic/tree.py:45-60` |

Demo eligibility baseline (from `seed/demo.py`, checked against `is_eligible`): exactly 4
eligible tickets — t4 "Implement the demo feature slice." (P0, deadline real-today+1),
t2 (P1, +3), t3 (P1, NULL), t1 (P2, +7). t8 (P0, deadline real-today) is at its ceiling
with `at_cap=stop` → never eligible; t5 is `needs_review` and blocked; t6 done; t7
dropped. Our walkthrough ticket is created P0 with deadline = client-side real today, so
it sorts at or ahead of t4 and always wins one of the two claim slots.

---

## 1. File-by-file blueprint

### 1.1 `scripts/dogfood_cli.py`

Standalone script, stdlib + `httpx` (a pinned project dep; the script always runs under
`.venv` python). Passes `ruff check .` (E/F/W/I/UP/B, line length 100); mypy does not
cover `scripts/` (`pyproject.toml: files=["src"]`).

Module docstring (mandatory content): what the script is (the §18.3 item-35 Level A
walkthrough, SPEC §18.5); the env contract (`PLAN_SERVER_URL` required,
`PLAN_DB_PATH` required); and this exact caveat, per recon B — the claim token lives
only in `tickets.claim_lock` and is deliberately never echoed by any API view
(`tickets/views.py:39`), so the script reads it read-only from SQLite for exactly one
purpose: impersonating the fake-spawned worker after the dispatcher tick. This is
harness scaffolding standing in for the env block the real spawn adapter passes to the
worker process (`core/adapters/real.py:52-56`).

Constants:

```python
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
```

Exit-code vocabulary (mirrors §8): `1` = step assertion mismatch, `2` = environment /
transport / subprocess failure. Any non-zero fails the test either way.

Function signatures:

```python
def fail(step: int, name: str, expected: object, got: object) -> NoReturn
    # prints "FAIL step NN — name", "  expected: ...", "  got: ..." (repr both) to
    # stderr; sys.exit(1). First mismatch wins — nothing catches SystemExit.

def ok(step: int, name: str) -> None
    # prints "PASS step NN — name" to stdout, flush=True.

def expect(step: int, name: str, got: object, expected: object) -> None
    # got != expected -> fail(...)

def expect_true(step: int, name: str, cond: bool, got: object) -> None
    # membership/absence checks; on False -> fail(step, name, "condition true", got)

def scrubbed_env() -> dict[str, str]
    # os.environ minus every PLAN_* key (same rule as conftest._scrubbed_env)

def cli(step: int, args: list[str], *, ticket_id: str | None = None,
        run_id: str | None = None, claim: str | None = None,
        stdin: str | None = None) -> Any
    # subprocess.run([str(PLAN_BIN), *args, "--json"], input=stdin, text=True,
    #   capture_output=True, cwd=str(REPO_ROOT), timeout=30,
    #   env=scrubbed_env() + PLAN_SERVER_URL always + the given pins only).
    # PLAN_ACTOR never set: the CLI defaults the actor header to "agent" (http.py:35).
    # rc != 0 -> fail(step, "cli " + " ".join(args), "exit 0", stderr) via exit 2 path.
    # Returns json.loads(stdout).

def human(step: int, method: str, path: str, body: dict | None = None) -> Any
    # httpx.request(method, SERVER_URL + path, json=body, timeout=10.0) with NO
    # headers argument at all — header-less = human (authctx.py:67-73).
    # status >= 300 -> fail (exit 2 path, body text in "got"). Returns resp.json().

def api_get(step: int, path: str) -> Any            # httpx.get, same failure handling

def read_claim_lock(step: int, ticket_id: str) -> str
    # sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True); SELECT claim_lock FROM
    # tickets WHERE id=?; None/missing row -> fail. Read-only by construction.

def pickup_ids(step: int) -> list[str]
    # cli(step, ["queue", "pickup"]) -> [e["ticket_id"] for e in data["pickup"]]

def main() -> int
    # 1. SERVER_URL = os.environ["PLAN_SERVER_URL"], DB_PATH = os.environ["PLAN_DB_PATH"]
    #    (missing -> print clear message, return 2). Module-level globals set here.
    # 2. Run step_01 .. step_12 in order, threading a small mutable dataclass:
    #    @dataclass class Walk: ticket_id, blocker_id, run_id, claim (all str).
    # 3. print("DOGFOOD LEVEL A: 12/12 PASS"); return 0.
```

Twelve step functions `step_01_create(w)` … `step_12_approve(w)`, one per row of the
§2 table, each ending in `ok(n, "<short name>")`. No arguments beyond env; the script
never seeds (the harness/test seeds `--demo` first, per item 35 and §18.5 Level A).

### 1.2 `tests/e2e/test_dogfood.py`

Uses ONLY existing conftest fixtures (`server`, `cli`) — no browser/context fixtures, no
imports from sibling test files, no conftest edits. Exactly one function whose name
matches `test_e35_` (the verify scorer requires exactly one match across the whole e2e
suite, `verify_lib.py:206-224`). Body is real (skip-scan: no skip/xfail/`.only`/empty
body — `verify_lib.py:90-95,108-119`).

```python
"""E2E item 35 — dogfood Level A (SPEC §18.3 item 35, §18.5): seed --demo, then run
scripts/dogfood_cli.py as a subprocess against the live test server."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"      # same pinning style as conftest.PLAN_BIN
SCRIPT = REPO_ROOT / "scripts" / "dogfood_cli.py"


def test_e35_dogfood_level_a(server, cli):
    cli(server, "seed", "--demo")                    # harness seeds; the script never does
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}
    env["PLAN_SERVER_URL"] = server.base
    env["PLAN_DB_PATH"] = str(server.db_path)
    proc = subprocess.run(
        [str(PYTHON), str(SCRIPT)],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=300,
    )
    assert proc.returncode == 0, (
        f"dogfood_cli.py rc={proc.returncode}\n--- stdout ---\n{proc.stdout}"
        f"\n--- stderr ---\n{proc.stderr}"
    )
    assert "DOGFOOD LEVEL A: 12/12 PASS" in proc.stdout, proc.stdout
```

(The local env-scrub duplicates one line of conftest deliberately — importing
`tests/e2e/conftest` helpers from a test file is not a pattern this suite uses, and the
fixture module may be edited concurrently by T19/T20.)

### 1.3 The four skills files — see §3.

---

## 2. The pinned step table

Actors: **CLI** = `.venv/bin/plan … --json` subprocess, env pinned per step (always
`PLAN_SERVER_URL`; `PLAN_TICKET_ID`/`PLAN_RUN_ID`/`PLAN_CLAIM` only where shown).
**HTTP-human** = request with no `X-Plan-*` headers. All assertions are on OUR entities
only — never global counts or queue equality (demo data coexists, recon C). `w.` = the
Walk context. Response of every ticket mutation/read is `ticket_json`
(`tickets/views.py:38-62`) unless noted.

**Step 1 — create (SPEC §4.3 R2, §8).** CLI, no ticket env:
`ticket create --title "Dogfood walkthrough ticket." --priority P0 --deadline <real-today>`
where `<real-today> = datetime.now().astimezone().date().isoformat()` (client-side, the
same convention `seed/demo.py:30` uses — recon C/D: the demo seed runs on the real
clock while the server clock is faked).
Assert: `state == "needs_success"`, `ceiling == "needs_success"`, `at_cap == "propose"`,
`priority == "P0"`, `deadline == <real-today>`. Save `w.ticket_id = data["id"]`.

**Step 2 — propose success #1 parks (SPEC §4.3, §4.4.1/3).** CLI, env
`PLAN_TICKET_ID=w.ticket_id`: `propose success --body-file -`, stdin `SUCCESS_1`.
At ceiling `needs_success` with `propose` → files pending, nothing advances
(`resolution.py:101-139`, target `needs_approach` > ceiling).
Assert: `state == "needs_success"`, `fields.success.proposal.body == SUCCESS_1`,
`fields.success.value is None`.

**Step 3 — propose success #2 supersedes (SPEC §4.4.1, item 5).** CLI, same env:
`propose success --body-file -`, stdin `SUCCESS_2`.
Assert: `state == "needs_success"`, `fields.success.proposal.body == SUCCESS_2`.
Then `GET /api/tickets/{w.ticket_id}/events` (HTTP, headers irrelevant on GET) →
`events` list contains an event with `kind == "proposal_superseded"` and
`payload == {"field": "success", "replaced_body": SUCCESS_1}` — membership over the
list, exact payload match (`resolution.py:125-131`).

**Step 4 — human accept success with grant pair (SPEC §4.4.4/7).** HTTP-human:
`POST /api/tickets/{w.ticket_id}/accept/success` body
`{"next_ceiling": "none", "at_cap": "propose"}` (no `edited_body`).
`"none"` resolves the ceiling to the newly entered state (`machine.py:87-89`), so the
ticket lands AT its ceiling with `at_cap=propose` — the step-6 approach proposal will
park, as item 35's per-gate human accepts require (recon E.4).
Assert: `state == "needs_approach"`, `fields.success.value == SUCCESS_2` (exact),
`fields.success.proposal is None`, `ceiling == "needs_approach"`, `at_cap == "propose"`.

**Step 5 — recap write then overwrite (SPEC §3.3, item 8).** CLI, env
`PLAN_TICKET_ID`: `recap --body-file -`, stdin `RECAP_1` → assert
`recap == RECAP_1`, `state == "needs_approach"`. Repeat with `RECAP_2` → assert
`recap == RECAP_2`, `state == "needs_approach"`. (Writable from `needs_approach`,
`admission.py:63-69`; overwrite is a plain replace, `tickets/data.py:267-277`.)

**Step 6 — propose approach; human EDIT-accepts (SPEC §4.4.4, item 6).** CLI:
`propose approach --body-file -`, stdin `APPROACH_1` → assert
`state == "needs_approach"`, `fields.approach.proposal.body == APPROACH_1` (parks: at
ceiling per step 4). HTTP-human: `POST /api/tickets/{w.ticket_id}/accept/approach` body
`{"edited_body": APPROACH_EDITED, "next_ceiling": "none", "at_cap": "propose"}`.
Assert: `state == "needs_plan"`, `fields.approach.value == APPROACH_EDITED`
byte-for-byte (trailing space included — compare with `repr` in the diagnostic),
`fields.approach.proposal is None`, `ceiling == "needs_plan"`, `at_cap == "propose"`.
Then `GET /api/tickets/{id}/events` → contains an event with
`kind == "proposal_accepted"` and payload `{"field": "approach",
"body": APPROACH_EDITED, "resolved_by": "human", "edited": True}` (exact payload,
`resolution.py:63-73,154-173`).

**Step 7 — propose plan; human accepts into in_progress (SPEC §4.4.7).** CLI:
`propose plan --body-file -`, stdin `PLAN_BODY` → assert `state == "needs_plan"`,
`fields.plan.proposal.body == PLAN_BODY`. HTTP-human:
`POST /api/tickets/{id}/accept/plan` body
`{"next_ceiling": "needs_review", "at_cap": "propose"}`.
Ceiling `needs_review` ≥ new state `in_progress` (`machine.py:94-100`); it makes the
ticket dispatcher-eligible (advance target `needs_review` ≤ ceiling,
`eligibility.py:55-57`) and routes the step-11 result auto-accept into `needs_review`
(`machine.py:40-48`: only `ceiling=done` short-circuits to done).
Assert: `state == "in_progress"`, `fields.plan.value == PLAN_BODY`,
`fields.plan.proposal is None`, `ceiling == "needs_review"`, `at_cap == "propose"`.

**Step 8 — day add / remove (SPEC §3.4, §8, item 12).** CLI (dates defaulted — `today`
resolves server-side to the fake planning date 2026-07-04; days materialize on read,
`days/api.py:52-79`, recon D):
`day add-ticket {w.ticket_id}` → response is the day view; then `day show` → assert
`w.ticket_id in [t["id"] for t in data["tickets"]]`.
`day remove-ticket {w.ticket_id}` → then `day show` → assert `w.ticket_id not in
[t["id"] ...]`; then `ticket show` (env `PLAN_TICKET_ID`) → assert
`state == "in_progress"` (removal is deferral only, SPEC §3.4).
Membership-only assertions: under a real clock (standalone run after 05:00) the demo
day already holds 3 tickets, so counts/positions are never asserted.

**Step 9 — blocking round-trip (SPEC §3.6, §7.2, item 9).**
(a) Baseline: `pickup_ids()` via CLI `queue pickup` → assert `w.ticket_id` present.
(b) CLI: `ticket create --title "Dogfood blocker ticket."` (defaults: P3, no deadline)
→ `w.blocker_id`. (c) CLI: `link add {w.blocker_id} {w.ticket_id} --kind blocks` —
from = blocker, to = blocked, confirmed against demo's `t8 -blocks-> t5`
(`seed/demo.py:111`) and `core/links.py:134-156`. Assert response
`{"from_id": w.blocker_id, "to_id": w.ticket_id, "kind": "blocks"}`.
(d) Assert `w.ticket_id` ABSENT from `pickup_ids()`; secondary exact check on our
entity: `GET /api/tickets/{w.ticket_id}` (detail view) → `blocked is True`
(`tickets/views.py:144-147`). (e) Complete the blocker — HTTP-human:
`POST /api/tickets/{w.blocker_id}/state` body `{"to": "done"}` (human state jump,
`resolution.py:202-211`) → assert response `state == "done"`. (f) Assert `w.ticket_id`
PRESENT in `pickup_ids()` again.

**Step 10 — dispatcher claim (SPEC §7.1-7.3, §9 test mode).** HTTP:
`POST /api/test/tick-dispatcher` (no body; test-endpoint, no auth distinction).
Report per `runtime.py:126-218`. Budget 2 − 0 active = 2; eligible order puts our P0
deadline-today ticket at/next-to the top (see baseline analysis above), so it is
always claimed; the other slot goes to a demo ticket — ignored.
Assert: `spawned` contains an entry `e` with `e["ticket_id"] == w.ticket_id` —
membership, never `len(spawned)` — and save `w.run_id = e["run_id"]` (the
HTTP-visible source, recon B). Then `GET /api/tickets/{w.ticket_id}` → assert
`claim_active is True` (`views.py:59`). Then `w.claim =
read_claim_lock(w.ticket_id)` from SQLite (read-only; the one sanctioned use,
recon B) → assert non-empty.

**Step 11 — claim-carrying worker writes (SPEC §7.3, §7.6, §8; recon E.11).** All CLI
with env `PLAN_TICKET_ID=w.ticket_id`, `PLAN_RUN_ID=w.run_id`, `PLAN_CLAIM=w.claim`
(the CLI turns these into `X-Plan-Run-Id`/`X-Plan-Claim` headers, `http.py:36-42`;
`require_claim` validates token + running-run match, `authctx.py:114-181`).
**Decision: heartbeat AND close are both exercised** (rationale in §4, D2). Order:
1. `run heartbeat` → assert `data["run"]["id"] == w.run_id` and
   `isinstance(data["claim_expires"], int)` (`dispatch/api.py:30-39`).
2. `propose result --body-file -`, stdin `RESULT_BODY` → auto-accepts
   (`in_progress` target `needs_review` ≤ ceiling `needs_review`). Assert:
   `state == "needs_review"`, `fields.result.value == RESULT_BODY`,
   `fields.result.proposal is None`, and — auto-accepts change neither grant half
   (SPEC §4.4.7, item 36) — `ceiling == "needs_review"`, `at_cap == "propose"`.
3. `run close --outcome done --summary -`, stdin `RUN_SUMMARY` → assert
   `data["run"]["status"] == "done"`, `data["run"]["summary"] == RUN_SUMMARY`
   (`dispatch/api.py:42-57`). Then `GET /api/tickets/{w.ticket_id}` → assert
   `claim_active is False` (lease cleared by `_finalize_run`, `dispatch/data.py:149-153`).

**Step 12 — human approve (SPEC §4.4.5).** HTTP-human:
`POST /api/tickets/{w.ticket_id}/approve` (empty JSON body `{}`; route takes none).
Assert response `state == "done"`. Print the final summary line, exit 0.

Every step ends with one `PASS step NN — <name>` line; the first mismatch prints
`FAIL step NN — <name>` + expected/got (reprs) to stderr and exits 1.

---

## 3. The four skills documents (SPEC §17; each ≤ 150 lines)

Binding grounding rule for the implementer: **every CLI example is first run against a
live demo-seeded test server and transcribed from real output** — boot
`PLAN_TEST_MODE=1 PLAN_DB_PATH=<tmp> PLAN_PORT=<free> .venv/bin/plan serve`, run
`PLAN_SERVER_URL=… .venv/bin/plan seed --demo`, then execute each documented command
and copy the real flags and JSON field names. No invented flags, no invented fields.
The verb surface is exactly `src/planner/cli/main.py`; anything not there (accept,
approve, grant, unblock, day-plan decisions) does not exist in the CLI and must not
appear as a command in any skill. Tone: imperative, written FOR the agent loading it,
concrete commands, no meta-commentary about the spec or the build.

### 3.1 `skills/planning-worker.md` — the dispatched worker loop (~100 lines)

1. **You and your env** — you were spawned by the dispatcher to work one ticket.
   `PLAN_SERVER_URL` (the server), `PLAN_TICKET_ID` (your ticket — every verb defaults
   to it), `PLAN_RUN_ID` + `PLAN_CLAIM` (your lease: the CLI attaches them to every
   write; a write without them, or after the lease lapses, is rejected with
   `stale_claim`). Never unset or share them.
2. **Orient first** — `plan ticket show --json`; read `state`, `ceiling`, `at_cap`,
   the four `fields` slots (`value` = decided, `proposal` = pending, `notes` =
   guidance to honor), `recap`. The gating field per state (success → approach → plan
   → result table).
3. **The write model: proposals only** — you never write values or states. File the
   current gating field; below the ceiling it auto-accepts and the ticket advances; at
   the ceiling with `at_cap=propose` it parks for the human; a new proposal on the
   same field replaces the pending one. Example (transcribed):
   `plan propose approach --body-file -` with a heredoc body; long text always via
   stdin or `--body-file`, never inline.
4. **Recap discipline** — after every meaningful unit of work overwrite the recap:
   `plan recap --body-file -`. It is the ticket's running memory; rejected only while
   the ticket still sits at `needs_success`.
5. **Notes** — `plan note <field> --body-file -` for guidance you want kept next to a
   field (e.g. review notes on `result`).
6. **Heartbeat cadence** — `plan run heartbeat` at least every 10 minutes (lease TTL
   15 minutes); a lapsed lease means every further write fails and the run is
   reclaimed.
7. **Finishing** — work to the ceiling, then `plan run close --outcome done --summary -`
   (stdin summary). Stuck on something you cannot resolve: write what you know into
   the recap and notes, then `plan run close --outcome blocked --summary -`.
8. **Never ask questions** — no human is in this loop. Decide, propose, record in the
   recap, close. Exit codes: 0 ok, 1 rejected write (read the JSON error on stderr),
   2 server unreachable.

### 3.2 `skills/planning-boundary.md` — the judgment-pass contract (~80 lines)

Grounded in `core/adapters/real.py:109-150` and `core/adapters/base.py:37-56` (this
agent is invoked non-interactively; it does not use the CLI).

1. **When you run** — once per planning date at the morning boundary; you receive one
   prompt and must reply with exactly one JSON object, nothing else around it.
2. **Your inputs** (deterministic, DB-internal; given as JSON in the prompt):
   `planning_date` (ISO); `carryover` — yesterday's unfinished day tickets as
   `{id, title, state, priority}` digests; `overdue` — same digest shape, tickets and
   items past deadline; `approvals_digest` — `{entity_id, kind, waiting_since}`
   entries awaiting the human.
3. **Judgment reply shape** (verbatim JSON skeleton):
   `{"brief_markdown": "...", "plan_tree": {"root": {"focus": "...", "status":
   "proposed"}, "children": [{"ticket_id": "t_…"|null, "note": "...", "status":
   "proposed", "position": 0}]}}` — statuses always `"proposed"` (the system stores
   your tree as a proposal for the human); `ticket_id` only from ids present in the
   inputs, `null` for a note-only child; positions contiguous from 0; few children —
   a focused day, not a backlog dump.
4. **Brief guidance** — short markdown: the day's single focus first, then carryover
   worth naming, overdue risks, and how much is waiting in approvals. No filler.
5. **Replan-root call** — reply `{"plan_tree": {...}}` (same tree shape), a complete
   replacement for the day.
6. **Replan-child call** — you get the invalidated child + the same inputs; reply
   `{"child": {"ticket_id": "t_…"|null, "note": "..."}}` — one replacement node only.
7. **Budget** — 60 seconds; reply directly, no exploration, no questions, no text
   outside the JSON object.

### 3.3 `skills/planner-main.md` — the day/system chat agent (~90 lines)

1. **You** — the planner in the day chat. You read and file through the CLI
   (`PLAN_SERVER_URL` set; you carry no claim); all decisions (accepts, grants,
   approvals, day-plan calls) belong to the human in the UI — you surface, summarize,
   and capture, you never resolve.
2. **Quick capture — the prime directive** — anything the human tosses into chat that
   is work gets filed IMMEDIATELY as a ticket, anything that is an idea as an idea;
   default P3; capture first, keep talking after:
   `plan ticket create --title "…"` (add `--project`/`--deadline` only when stated);
   `plan idea create --title "…" --body-file -` for bodies. Never ask "should I file
   this?" — file it and say so.
3. **Operating the queues** — `plan queue approvals --json` (what waits on the human,
   oldest first), `plan queue pickup --json` (what the dispatcher can take next, in
   order), `plan queue overdue --json` (past-deadline tickets/items). Transcribed
   one-line output examples for each.
4. **The day** — `plan day show --json` (brief, plan tree, ordered ticket list);
   `plan day add-ticket <t_id>` / `plan day remove-ticket <t_id>` (today by default;
   removing defers — ticket state untouched); `plan ticket set <t_id> --day today`.
5. **Looking things up** — `plan ticket show <t_id> --json`, `plan ticket list
   --state …`, `plan item list`, `plan sprint show --json`, `plan idea list`.
6. **What you must route to the human** — accepting proposals, grants
   (ceiling/at_cap), needs_review approval, unblocking, dropping, day-plan
   accept/invalidate: name the thing, point to the Review screen or Ticket page.

### 3.4 `skills/planning-executor.md` — owned in_progress work + the needs_review discipline (~90 lines)

1. **Scope** — you own one `in_progress` ticket (dispatched with claim env, or handed
   `PLAN_TICKET_ID`). Deliver the result; then drive the review discipline.
2. **Work loop** — orient (`plan ticket show --json`): execute against
   `fields.plan.value` and honor every `notes` slot; keep the recap current
   (`plan recap --body-file -`) as you go; heartbeat if you carry a claim.
3. **Landing the result** — `plan propose result --body-file -`: a result that states
   what was produced, where it lives, and how it was verified. Below/at ceiling
   `needs_review` it auto-accepts and the ticket moves to `needs_review`; re-proposing
   replaces a pending result.
4. **The review notes are the checklist** — `fields.result.notes` holds what needs
   careful checking (written during planning, updated by you). Read them before
   review; they are the review's contract (SPEC §4.2).
5. **needs_review: orchestrate the Codex CLI** — run it non-interactively against the
   result and the notes, e.g.
   `codex exec "Review the following result against these review notes; list concrete
   violations only: <result value + notes + the artifacts they point to>"`.
   Feed it the real content (from `plan ticket show --json`), not a summary.
6. **Fix–update loop** — fix everything codex surfaces; update the notes via
   `plan note result --body-file -` recording what was checked, what was fixed, and
   what a human should still eyeball; rerun codex until it reports no violations.
7. **Hand-off** — you never approve. When codex is clean and the notes say so, the
   recap states the ticket is ready; the human approves from Review. If review
   surfaces something you cannot fix, write it into the notes and recap and (if
   claimed) `plan run close --outcome blocked --summary -`.

---

## 4. Risks and open points — each resolved

**R1 — demo data coexistence / real-vs-fake clock skew.** The demo seed writes with the
real clock (`demo.py:29-30`) while the e2e server clock is fake (2026-07-04). Resolved
by construction: every assertion is membership/absence of OUR entities; day verbs use
default dates (server-resolved planning date); the walkthrough deadline uses the
client-side real date so ordering beats/ties t4 under both clocks. In a tie
(standalone run where demo real-today+1 equals script real-today — clock-midnight
edge), t4 sorts first on `created_at` but both fit the 2-slot budget, so the step-10
membership assertion is unaffected. No global counts anywhere.

**R2 — recon C wording "the second slot will claim the demo P0".** True under the e2e
fake clock; under a standalone real-clock run our ticket may take the second slot
instead of the first. Not a contradiction — the plan never asserts which slot, only
membership of `w.ticket_id` in `spawned`.

**D1 — grant pairs per gate** (recon E.4/6/7): success accept `("none", "propose")`;
approach edit-accept `("none", "propose")`; plan accept `("needs_review", "propose")`.
The two `"none"` grants pin the ticket at each new gate with `propose`, forcing the
next proposal to park for a human accept at every gate (item 35's requirement); the
final grant makes the ticket eligible and routes the result into `needs_review`.

**D2 — heartbeat and close: INCLUDED** (recon E.11 decision point). `close_run` only
clears the lease and updates the breaker/run row (`dispatch/data.py:113-217`); it never
touches ticket state, so `needs_review` → approve is undisturbed. Order matters only in
that both claim verbs must run while the lease is live: heartbeat → propose result →
close → (human) approve. This grounds `plan run heartbeat` and `plan run close` in the
worker/executor skills with transcribed output.

**D3 — claim token acquisition.** `run_id` from the tick report's `spawned` entry
(HTTP-visible, preferred per recon B); the token from a read-only SQLite open of
`PLAN_DB_PATH` (`file:…?mode=ro`), documented in the module docstring as harness
scaffolding for the env block the real spawn passes (`real.py:52-56`). No API change,
no other DB reads or any writes.

**D4 — blocker completion path.** Human state jump `POST /api/tickets/{id}/state
{"to": "done"}` (recon E.9): valid from `needs_success` (`resolution.py:202-211` allows
any different non-dropped target; skipped gating values stay null, SPEC §4.3).

**D5 — script HTTP client.** `httpx` (already a pinned dependency; the script always
runs under `.venv` python — both in the test and the standalone gate). Human calls pass
no custom headers at all, matching conftest's `api.human_post` proof that header-less
POSTs pass `reject_agents`.

**D6 — test subprocess interpreter.** Pinned `.venv/bin/python` by absolute path
(conftest's `PLAN_BIN` pattern) rather than `sys.executable` — identical under
`./verify`, unambiguous if a system pytest ever collects the suite.

**D7 — one-anchor discipline.** Only `test_e35_dogfood_level_a` exists in the new test
file; helpers (none needed) would carry non-`test_` names. T19/T20 own e28–e34 in
sibling files; this file imports nothing from them.

**Gates before hand-back (recon H):**
1. `.venv/bin/pytest tests/e2e/test_dogfood.py -q` green.
2. Standalone: `PLAN_TEST_MODE=1 PLAN_DB_PATH=$T/dog.db PLAN_PORT=<free>
   PLAN_LOGS_DIR=$T/logs PLAN_DISPATCHER_LOCK_PATH=$T/dispatcher.lock
   .venv/bin/plan serve` &; `PLAN_SERVER_URL=http://127.0.0.1:<port> .venv/bin/plan
   seed --demo`; `PLAN_SERVER_URL=… PLAN_DB_PATH=$T/dog.db .venv/bin/python
   scripts/dogfood_cli.py` → exit 0, full 12-line PASS trace. (`PLAN_FAKE_NOW`
   optional — the script is clock-agnostic by design, R1.)
3. `.venv/bin/ruff check .` clean (covers `scripts/` and `tests/`).
4. `wc -l skills/*.md` — each of the four ≤ 150.
5. Zero `src/` diffs (`mypy src/` untouched by construction).
6. Full `./verify` at integration: item 35 flips to PASS; no other item regresses.

---

## 5. Binding amendments (post-review — these override anything above)

Codex plan review returned 5 findings; all accepted (see plan-review.md). The
walkthrough is now **13 steps** and the trace/summary lines change accordingly.

**A1 — new step 9: the day-plan decision (codex finding 1).** Inserted between the
day add/remove step and the blocking step; later steps renumber (blocking → 10,
claim → 11, worker writes → 12, approve → 13). HTTP-human:
`POST /api/day/{real_today}/plan/accept` body `{"node": 1}` where
`real_today = datetime.now().astimezone().date().isoformat()` computed client-side —
the demo seed writes its day at the server's REAL today in every context
(`seed/demo.py:30,224`), same host and tz, so the dates agree; no fallback logic.
The demo day's plan tree ships with child 1 (`t8`, "Stabilise the login test…")
`proposed` (`demo.py:225-237`). Assert on the returned day view:
`plan.children[1].status == "accepted"` and `plan.root.status == "accepted"` (root
was seeded accepted and must be untouched). No day-list assertions (t8 is already a
day ticket; accept adds only if absent). Live-verified against a scratch server.
Consequences: `main()` runs step_01..step_13; the final line printed is
`DOGFOOD LEVEL A: 13/13 PASS`; the test asserts that exact string (the gate-2 recipe
above reads "full 13-line PASS trace" accordingly).

**A2 — response-shape precision (finding 2).** Mutation endpoints return
`ticket_json`; `GET /api/tickets/{id}` and `plan ticket show --json` return
`ticket_detail` = `ticket_json` + `blocked`, `links`, `day_ids`, `run_summary`
(`tickets/views.py:121`). All pinned assertions stand — every asserted key exists in
the returned shape; the blocking step's `blocked is True` check already relied on the
detail view.

**A3 — DOCS.md (finding 3).** Out of T21 scope: the implementer touches ONLY the six
owned files. The SPEC §17 requirement that DOCS.md summarize the four skills' roles
is handed to the stage-7 integrator via the T21 report.

**A4 — worker-skill exit codes (finding 4).** planning-worker.md §8 states: exit 0
success; 1 validation/domain error (a rejected write is one kind — read the JSON
error body); 2 connection error (SPEC §8, `cli/http.py:19,83,104`).

**A5 — planner-main routing (finding 5).** In planner-main.md §6: day-plan
accept/invalidate/accept-all/reject-all belong to the **Day screen**; proposal
accepts, grants, and needs_review approval belong to Review/Ticket (SPEC §10.1-10.4).
