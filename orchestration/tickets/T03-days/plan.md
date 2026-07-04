# T03 — Days domain: implementation plan (stage 3)

Blueprint only. Owns exactly: `src/planner/days/logic/` (new pure package),
`src/planner/days/data.py`, `src/planner/days/boundary.py`,
`tests/unit/test_days.py`. Touches nothing else. `days/__init__.py`,
`days/api.py`, `days/contracts.py`, and the shared conftest are read-only.

Contracts implemented against (never modified): `days/contracts.py`
(`NodeStatus`, `PlanRoot`, `PlanNode`, `PlanTree`, `Day`, `DayTicket`,
`PlanningDateFn`), `core/contracts.py` (`EventKind`, `JsonDict`),
`core/errors.py`. Infra reused: `core/db.py` (tables `days`, `day_tickets`,
`boundary_runs`, `events`), `core/events.py` (`append_event`,
`read_events_since`), `core/ids.py` (`day_id`), `core/clock.py` (`Clock`
protocol), `core/config.py` (`Config.boundary_hour`,
`Config.boundary_timeout_seconds`), `core/adapters/base.py` (`BoundaryAdapter`,
`BoundaryInputs`, `BoundaryJudgment`).

Cross-domain contract imports used by the pure logic (all are stdlib-only
contract modules, so this stays "pure"): `tickets/contracts.py`
(`TicketState`, `GATING_FIELD`), `sprints/contracts.py` (`ItemStatus`).

---

## 0. Two constraints that override the ticket text — decide now, log in decisions.md

**0a. One test function per acceptance item (not "multiple allowed").**
`scripts/verify_lib.py::score()` marks an item PASS iff **exactly one** collected
test whose bare name starts with `<token>_` passed; "multiple matches ... mean
FAIL". So the four items are **exactly four functions**: `test_a01_planning_date`,
`test_a12_day_ticket_removal`, `test_a17_plan_tree`, `test_a18_boundary_job`.
Each packs all of its item's assertions into one body. `./verify` is the source
of truth (CLAUDE.md), so this beats the ticket's "multiple functions per item
are allowed" line. Log the contradiction and this resolution in decisions.md.

**0b. a18 needs a recording adapter, not the bare `FakeBoundaryAdapter`.**
`FakeBoundaryAdapter.calls` is `list[str]` of method names only — it cannot
expose the `BoundaryInputs` it received, but SPEC item 18 requires asserting the
computed carryover **and** overdue exactly. a18 therefore defines a local
`RecordingBoundaryAdapter` in `test_days.py` (a fake — no OS/network, honoring
§14/§18.3) that records both `calls: list[str]` and `received: list[BoundaryInputs]`
and builds children from `inputs.carryover` exactly like `FakeBoundaryAdapter`.
This one adapter covers the calls-count assertions too. Log this in decisions.md.

---

## 1. File-by-file blueprint

Layering: `logic/` is pure (stdlib + contract modules only — never `sqlite3`,
`fastapi`, `pydantic`, `config`, `events`, `adapters/real`). `data.py` and
`boundary.py` are the data layer (import `sqlite3`, `core.events`, `core.ids`,
`core.db` types, the logic package, adapter Protocols) — **no FastAPI/pydantic**.

### 1.1 `src/planner/days/logic/__init__.py`
Empty package marker. Docstring: "Pure day-domain logic: planning-date math,
plan-tree transforms + serialization, the effect vocabulary, and the boundary
deterministic-pass computations. Stdlib + planner contract modules only."

### 1.2 `src/planner/days/logic/dates.py`
Docstring intent: "Planning-date math (§6.1). The calendar date of
(now − boundary_hour hours). Matches the `PlanningDateFn` contract named in
`days/contracts.py`."

Imports: `from __future__ import annotations`; `from datetime import date,
datetime, timedelta`.

```python
def planning_date(now: datetime, boundary_hour: int) -> date:
    """§6.1: planning date = calendar date of (now − boundary_hour hours).
    05:00 belongs to the NEW date (04:59 → previous, 05:00 → current)."""
    return (now - timedelta(hours=boundary_hour)).date()
```
Behavior → SPEC: §6.1 line 117. Tz-agnostic (works on aware local `now` from
`Clock.now()` or on naive test datetimes). The off-by-one at exactly 05:00 falls
out of `>= boundary_hour ⇒ same date` because `(05:00 − 5h).date()` rolls to the
new date while `(04:59 − 5h).date()` stays on the old one. Signature is exactly
`PlanningDateFn = Callable[[datetime, int], date]`.

### 1.3 `src/planner/days/logic/effects.py`
Docstring intent: "The serializable effect vocabulary returned by plan-tree
transforms. Effects are DATA describing side effects; the data layer applies
them. Frozen dataclasses so the stage-4 runtime can store/serialize them
(replan requests especially — SPEC R5 latest-wins is applied later, not here)."

Imports: `from dataclasses import dataclass`; `from planner.core.contracts
import EventKind, JsonDict`.

```python
@dataclass(frozen=True)
class AddTicketToDay:
    """Append ticket_id to the day list if absent (§6.3). Application is
    idempotent — a ticket already on the list is not added twice (a17)."""
    ticket_id: str

@dataclass(frozen=True)
class EmitEvent:
    """Append one events row. kind is an EventKind; payload is JSON-ready."""
    kind: EventKind
    payload: JsonDict

@dataclass(frozen=True)
class ReplanRoot:
    """Request a full-tree replan (adapter.replan_root). Serialized/executed by
    the stage-4 runtime (R5 latest-wins); exposed here only as data."""

@dataclass(frozen=True)
class ReplanChild:
    """Request a single-node replan of the child at `position`
    (adapter.replan_child). Same stage-4 handling as ReplanRoot."""
    position: int

Effect = AddTicketToDay | EmitEvent | ReplanRoot | ReplanChild
ReplanRequest = ReplanRoot | ReplanChild
```
`ReplanRoot`/`ReplanChild` are `dataclasses.asdict`-serializable (no fields /
one int). `EmitEvent.kind` serializes via `EventKind` → str; `payload` is
already `JsonDict`.

### 1.4 `src/planner/days/logic/tree.py`
Docstring intent: "Plan-tree transforms (§6.3) as pure functions
`PlanTree → (new_tree, effects)`, plus JSON serialization of the tree for the
`days.plan` column and for event payloads that carry `old_tree`. No side
effects; the data layer persists the returned tree and applies the effects."

Imports: `from dataclasses import replace`; `from typing import Any`;
`from planner.core.contracts import EventKind, JsonDict`;
`from planner.days.contracts import NodeStatus, PlanNode, PlanRoot, PlanTree`;
`from planner.days.logic.effects import (AddTicketToDay, Effect, EmitEvent,
ReplanChild, ReplanRoot)`.

Node addressing: `NodeRef = str | int` — the literal `"root"` or a child
`position` (int). This mirrors the `plan_node_accepted`/`plan_node_invalidated`
payload contract: `{node}` is `"root" | <child position>`.

Serialization:
```python
def tree_to_dict(tree: PlanTree) -> JsonDict:
    """{root:{focus,status}, children:[{ticket_id,note,status,position}, ...]}.
    NodeStatus serialized as its str value; child order preserved."""

def tree_from_dict(data: JsonDict) -> PlanTree:
    """Inverse of tree_to_dict. status parsed via NodeStatus(...)."""

def as_proposed(tree: PlanTree) -> PlanTree:
    """Return a copy with root and every child status=proposed. The boundary
    stores the judgment tree as a PROPOSED plan (§6.2)."""
```

Transforms (each returns `tuple[PlanTree, list[Effect]]` except reject-all):
```python
def accept_node(tree: PlanTree, ref: NodeRef) -> tuple[PlanTree, list[Effect]]:
    """§6.3: accepting a node sets it accepted; accepting root does NOT cascade.
    A child with a ticket_id also yields an AddTicketToDay effect (append at end
    if absent). effects order: EmitEvent(plan_node_accepted) then
    (for a child with ticket_id) AddTicketToDay."""
    # ref == "root": new root status=accepted, children copied unchanged,
    #   effects = [EmitEvent(plan_node_accepted, {"node": "root"})]
    # ref is int p: child at position p -> accepted, other children unchanged,
    #   effects = [EmitEvent(plan_node_accepted, {"node": p})]
    #             + ([AddTicketToDay(child.ticket_id)] if child.ticket_id else [])

def accept_all(tree: PlanTree) -> tuple[PlanTree, list[Effect]]:
    """§6.3 accept-all: root + every child accepted. effects =
    [EmitEvent(plan_accepted_all, {})] then AddTicketToDay(child.ticket_id) for
    each child with a ticket_id, in child position order. Idempotent add is the
    data layer's job (a17)."""

def invalidate_root(tree: PlanTree) -> tuple[PlanTree, list[Effect]]:
    """§6.3: root + EVERY child -> invalidated; request exactly one root replan.
    effects = [EmitEvent(plan_node_invalidated, {"node": "root"}), ReplanRoot()]."""

def invalidate_child(tree: PlanTree, position: int) -> tuple[PlanTree, list[Effect]]:
    """§6.3: ONLY the child at `position` -> invalidated; root and every other
    child keep their prior status. effects =
    [EmitEvent(plan_node_invalidated, {"node": position}), ReplanChild(position)]."""

def reject_all(tree: PlanTree) -> tuple[None, list[Effect]]:
    """§6.3 reject-all: plan cleared. new tree is None (data layer writes
    days.plan = NULL). effects = [EmitEvent(plan_rejected, {"old_tree":
    tree_to_dict(tree)})]."""
```
Purity: transforms never mutate the input — they build new `PlanRoot`/`PlanNode`
via `dataclasses.replace` and a fresh `PlanTree`. Behavior → SPEC §6.3 lines
135–139.

### 1.5 `src/planner/days/logic/carryover.py`
Docstring intent: "The boundary deterministic pass (§6.2) as pure functions over
plain projected row data. Produce the `BoundaryInputs` digest lists: carryover,
overdue, approvals — plus the yesterday done/not-done split for `day_closed`.
Zero DB access; the boundary layer does the SQL and hands rows in."

Imports: `from typing import Any`; `from planner.tickets.contracts import
FieldName, GATING_FIELD, TicketState`; `from planner.sprints.contracts import
ItemStatus`.

Row inputs are `list[dict[str, Any]]` (already-projected sqlite rows) and
outputs are `list[dict[str, Any]]` — matching the `BoundaryInputs` field types
exactly.

```python
_CARRYOVER_EXCLUDE = {TicketState.done, TicketState.dropped}          # §6.2.2
_ITEM_CLOSED = {ItemStatus.done, ItemStatus.deferred_next_sprint}     # overdue

def carryover_candidates(yesterday_tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§6.2 step 2: yesterday's day-tickets whose ticket state is NOT done/dropped.
    Input rows carry {id, title, state, priority} (ticket joined to its day-ticket,
    in day-ticket position order). Output digest: {id, title, state, priority}."""
    # keep rows where row["state"] not in _CARRYOVER_EXCLUDE (StrEnum == str),
    # projecting the four digest keys.

def day_ticket_counts(yesterday_tickets: list[dict[str, Any]]) -> tuple[int, int]:
    """§6.2 step 4: (done_count, not_done_count) over yesterday's day-tickets.
    done_count = rows with state == TicketState.done; not_done_count = the rest
    (dropped and every unfinished state). The two sum to len(rows)."""

def overdue_list(
    tickets: list[dict[str, Any]], items: list[dict[str, Any]], today_iso: str
) -> list[dict[str, Any]]:
    """§6.2 step 3 (overdue). A ticket is overdue iff deadline is not None and
    deadline < today_iso (strict; due-today is NOT overdue) and state not in
    {done, dropped}. An item is overdue iff deadline < today_iso and status not
    in {done, deferred_next_sprint}. Uniform digest {id, title, state, priority}
    (for items, state = the item status). ISO date strings compare lexically.
    Tickets first, then items."""

def approvals_digest(
    tickets: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """§6.2 step 3 (approval-queue digest). A ticket is awaiting approval iff its
    current gating field (GATING_FIELD[state], absent for needs_review/done/
    dropped) has a non-null proposal in its parsed `fields` dict. An item is
    awaiting approval iff it has a non-null `status_proposal`. Digest per
    base.py: {entity_id, kind, waiting_since}. For a ticket kind = gating field
    name (e.g. "plan"); for an item kind = "status". waiting_since =
    proposal["created_at"]. ticket rows carry {id, state, fields(dict)}; item
    rows carry {id, status_proposal(dict|None)}."""
```
Behavior → SPEC §6.2 lines 123–129; `BoundaryInputs` shapes → `core/adapters/base.py`
lines 37–43. a18 asserts only carryover + overdue (§6.2 items 2–3 that item 18
names); approvals is exercised (runs without error) but its contents are not an
item-18 fence, so a18 does not over-assert it.

### 1.6 `src/planner/days/data.py`
Docstring intent: "The day data layer. Materializes a day on first read/write
(no 'missing day' state), owns the ordered day-ticket list (contiguous positions
from 0), plan JSON storage, brief/notes writes, and applies the logic layer's
plan-tree effects. Every state transition here appends exactly one canonical
event. No FastAPI/pydantic; times come in as unix-second ints from the caller's
clock."

Imports: `from __future__ import annotations`; `import json`; `import sqlite3`;
`from planner.core.contracts import EventKind, JsonDict`;
`from planner.core.events import append_event`;
`from planner.days.contracts import Day, DayTicket, PlanTree`;
`from planner.days.logic.effects import (AddTicketToDay, EmitEvent, Effect,
ReplanChild, ReplanRoot, ReplanRequest)`;
`from planner.days.logic.tree import tree_from_dict, tree_to_dict`.

```python
def materialize_day(conn, day_id: str, now_unix: int) -> None:
    """§3.4: create the day row if absent (brief='', notes='', plan=NULL,
    chat_session_key=NULL, created_at=updated_at=now_unix) and append a
    day_created event. Idempotent: present day -> no write, no event."""

def read_day(conn, day_id: str, now_unix: int) -> Day:
    """§3.4 'reading a nonexistent day materializes it empty'. Calls
    materialize_day, then SELECTs the row and builds a Day (plan parsed via
    tree_from_dict when non-null)."""

def list_day_tickets(conn, day_id: str) -> list[DayTicket]:
    """day_tickets for the day, ordered by position (contiguous from 0)."""

def load_plan(conn, day_id: str) -> PlanTree | None:
    """Parse days.plan JSON -> PlanTree, or None."""

def store_plan(conn, day_id: str, tree: PlanTree | None, now_unix: int) -> None:
    """Materialize, then write days.plan = tree_to_dict(tree) JSON (or NULL) and
    bump updated_at. No event here (callers emit the specific plan_* event)."""

def store_judgment(conn, day_id: str, brief: str, tree: PlanTree, now_unix: int) -> None:
    """Boundary success path: write days.brief + days.plan (serialized) +
    updated_at in one UPDATE. No day_updated event (that kind is reserved for
    manual edits); the boundary emits plan_proposed separately."""

def add_day_ticket(
    conn, day_id: str, ticket_id: str, now_unix: int, cause: str = "manual"
) -> bool:
    """§3.4: append at end with the next contiguous position (= current count).
    Idempotent: if (day_id, ticket_id) already present -> return False, no event.
    On add -> INSERT, append day_ticket_added {ticket_id, position, cause}, bump
    updated_at, return True. Materializes the day first (write path)."""

def remove_day_ticket(conn, day_id: str, ticket_id: str, now_unix: int) -> None:
    """§3.4: delete the association only (ticket state untouched). If nothing was
    deleted (rowcount 0) -> no-op, no event, no re-pack. Else re-pack remaining
    positions to 0..n-1 in existing position order, append day_ticket_removed
    {ticket_id}, bump updated_at."""

def set_brief(conn, day_id: str, brief: str, now_unix: int) -> None:
    """Manual brief edit: materialize, UPDATE brief + updated_at, append
    day_updated {field: "brief"}."""

def set_notes(conn, day_id: str, notes: str, now_unix: int) -> None:
    """Manual notes edit: materialize, UPDATE notes + updated_at, append
    day_updated {field: "notes"}."""

def apply_plan_effects(
    conn, day_id: str, new_tree: PlanTree | None, effects: list[Effect], now_unix: int
) -> list[ReplanRequest]:
    """Persist the transform result and apply its effects. (1) store_plan(new_tree)
    — writes days.plan (or NULL for reject-all). (2) For each effect in order:
    AddTicketToDay -> add_day_ticket(..., cause="plan_accept") (idempotent);
    EmitEvent -> append_event(day_id, kind, payload); ReplanRoot/ReplanChild ->
    collected. (3) Return the collected replan requests for the stage-4 runtime
    to serialize + execute (R5). At stage 3 no adapter is called here."""
```
`conn` typed `sqlite3.Connection`. Re-pack uses a plain UPDATE loop; `position`
has only a `CHECK (>=0)` (no UNIQUE), so transient duplicate positions mid-loop
are legal. Behavior → SPEC §3.4 lines 48–49, §6.3 line 137.

### 1.7 `src/planner/days/boundary.py`
Docstring intent: "The boundary job (§6.2). Runs once per planning date, guarded
by the boundary_runs table. Deterministic pass (materialize the day, carryover,
overdue, approvals, close yesterday) then one adapter judgment call wrapped in a
caller-owned timeout. On failure/timeout the day survives with empty brief and
no plan. Judgment is skipped entirely when the human already planned the day."

Imports: `from __future__ import annotations`; `import json`; `import sqlite3`;
`from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout`;
`from datetime import timedelta`; `from typing import Any`;
`from planner.core.adapters.base import BoundaryAdapter, BoundaryInputs`;
`from planner.core.clock import Clock`; `from planner.core.config import Config`;
`from planner.core.contracts import EventKind`;
`from planner.core.events import append_event`; `from planner.core.ids import day_id`;
`from planner.days.contracts import NodeStatus`;
`from planner.days.data import load_plan, materialize_day, store_judgment`;
`from planner.days.logic.carryover import (approvals_digest, carryover_candidates,
day_ticket_counts, overdue_list)`;
`from planner.days.logic.dates import planning_date`;
`from planner.days.logic.tree import as_proposed, tree_to_dict`. (`_human_planned`
uses `data.load_plan`; the success path uses `as_proposed` + `tree_to_dict`.)

Public entry:
```python
def run_boundary(conn, clock: Clock, config: Config, adapter: BoundaryAdapter) -> None:
    """One boundary tick. Idempotent per planning date via the boundary_runs
    guard, so the stage-4 scheduler may call it every tick — only the first tick
    that advances the planning date does work (planning_date only becomes the new
    date at/after boundary_hour, satisfying §6.2 'first tick at/after the boundary
    hour')."""
```

Private helpers (all typed, `conn: sqlite3.Connection`):
- `_boundary_ran(conn, pd_iso) -> bool` — `SELECT 1 FROM boundary_runs WHERE planning_date=?`.
- `_record_boundary(conn, pd_iso, now_unix, judgment: str) -> None` — one
  `INSERT INTO boundary_runs(planning_date, ran_at, judgment)`. judgment ∈
  {"ok","skipped","failed"}.
- `_read_yesterday_tickets(conn, yesterday_id) -> list[dict[str, Any]]` —
  `SELECT t.id, t.title, t.state, t.priority FROM day_tickets dt JOIN tickets t
  ON t.id = dt.ticket_id WHERE dt.day_id=? ORDER BY dt.position`, projected to dicts.
- `_read_overdue_candidates(conn) -> tuple[list, list]` — tickets
  `SELECT id,title,state,priority,deadline FROM tickets`; items
  `SELECT id,title,status,priority,deadline FROM sprint_items`, projected.
- `_read_approval_candidates(conn) -> tuple[list, list]` — tickets
  `SELECT id,state,fields FROM tickets` (fields json.loads'd into a dict); items
  `SELECT id,status_proposal FROM sprint_items` (status_proposal json.loads'd or None).
- `_human_planned(conn, new_day_id) -> bool` — True iff a day_ticket exists on
  the new day OR days.plan has root accepted or any child accepted (via load_plan).
- `_judgment_with_timeout(adapter, inputs, timeout_s) -> BoundaryJudgment` — runs
  `adapter.judgment(inputs)` in a `ThreadPoolExecutor(max_workers=1)` and returns
  `future.result(timeout=timeout_s)`. The CALLER owns the timeout (base.py line 52).
- `_error_text(exc, timeout_s) -> str` — `FuturesTimeout` -> f"boundary judgment
  timed out after {timeout_s}s"; else `str(exc) or exc.__class__.__name__`.

---

## 2. Effects vocabulary (exact serializable shapes)

Returned by the tree transforms; applied by `data.apply_plan_effects`. All are
`@dataclass(frozen=True)` in `logic/effects.py`:

| Effect | Fields | Applied as | Serialization |
|---|---|---|---|
| `AddTicketToDay` | `ticket_id: str` | `add_day_ticket(cause="plan_accept")`, idempotent | `{"ticket_id": ...}` |
| `EmitEvent` | `kind: EventKind`, `payload: JsonDict` | `append_event(day_id, kind, payload)` | `{"kind": kind.value, "payload": {...}}` |
| `ReplanRoot` | (none) | collected → returned to caller (stage-4 runs it) | `{}` |
| `ReplanChild` | `position: int` | collected → returned to caller | `{"position": n}` |

`Effect = AddTicketToDay | EmitEvent | ReplanRoot | ReplanChild`.
`ReplanRequest = ReplanRoot | ReplanChild` (the subset `apply_plan_effects`
returns). Replan effects are pure data at stage 3 — R5 latest-wins serialization
is stage-4 runtime behavior, so nothing here calls a replan adapter.

---

## 3. Event flow (EventKind → where → entity_id → payload)

| EventKind | Emitted in | entity_id | payload keys |
|---|---|---|---|
| `day_created` | `data.materialize_day` (on create only) | new day id | `{}` |
| `day_ticket_added` | `data.add_day_ticket` (on real add only) | day id | `{ticket_id, position, cause}` |
| `day_ticket_removed` | `data.remove_day_ticket` (on real delete) | day id | `{ticket_id}` |
| `day_updated` | `data.set_brief` / `data.set_notes` (manual) | day id | `{field}` — `"brief"`\|`"notes"` |
| `plan_node_accepted` | `accept_node` effect → `apply_plan_effects` | day id | `{node}` — `"root"`\|position(int) |
| `plan_accepted_all` | `accept_all` effect | day id | `{}` |
| `plan_node_invalidated` | `invalidate_root`/`invalidate_child` effect | day id | `{node}` — `"root"`\|position(int) |
| `plan_rejected` | `reject_all` effect | day id | `{old_tree}` (serialized tree) |
| `plan_proposed` | `boundary.run_boundary` success | new day id | `{tree}` (serialized proposed tree) |
| `day_closed` | `boundary.run_boundary` deterministic pass | **yesterday** day id | `{done_count, not_done_count}` |
| `boundary_failed` | `boundary.run_boundary` on judgment fail/timeout | new day id | `{error}` |

`plan_replanned` (EventKind exists, `{old_tree, scope, node}`) is **not** emitted
at stage 3 — it fires in stage 4 when a replan result is applied. The brief on
the success path is stored on the day row (via `store_judgment`) with no separate
event; `day_updated` stays reserved for manual edits (its contract comment).

---

## 4. Boundary-job control flow (`run_boundary`, ordered)

```
pd   = planning_date(clock.now(), config.boundary_hour)
piso = pd.isoformat()

1. GUARD.        if _boundary_ran(conn, piso): return          # second tick same
                                                                # date: zero writes,
                                                                # no adapter call.
   now  = clock.now_unix()
   ndid = day_id(pd)
   yid  = day_id(pd - timedelta(days=1))

2. DETERMINISTIC PASS (always runs on a first tick):
   materialize_day(conn, ndid, now)                            # day_created if new
   yts       = _read_yesterday_tickets(conn, yid)
   carry     = carryover_candidates(yts)
   otk, oit  = _read_overdue_candidates(conn)
   overdue   = overdue_list(otk, oit, piso)
   atk, ait  = _read_approval_candidates(conn)
   approvals = approvals_digest(atk, ait)
   done, not_done = day_ticket_counts(yts)
   append_event(conn, yid, day_closed, {"done_count": done,
                "not_done_count": not_done}, now)

3. SKIP CHECK (§6.2 last paragraph):
   if _human_planned(conn, ndid):
       _record_boundary(conn, piso, now, "skipped")            # judgment NOT called
       return

4. JUDGMENT PASS (one adapter call):
   inputs = BoundaryInputs(planning_date=piso, carryover=carry,
                           overdue=overdue, approvals_digest=approvals)
   try:
       j = _judgment_with_timeout(adapter, inputs, config.boundary_timeout_seconds)
   except Exception as exc:                                    # timeout OR raise
       append_event(conn, ndid, boundary_failed,
                    {"error": _error_text(exc, timeout_s)}, now)
       _record_boundary(conn, piso, now, "failed")             # day survives:
       return                                                  # empty brief, no plan
   proposed = as_proposed(j.plan_tree)                         # §6.2 "proposed" plan
   store_judgment(conn, ndid, j.brief_markdown, proposed, now) # brief + plan
   append_event(conn, ndid, plan_proposed, {"tree": tree_to_dict(proposed)}, now)
   _record_boundary(conn, piso, now, "ok")
```

`boundary_runs.judgment` per path: **ok** (judgment succeeded, brief+plan
stored), **skipped** (human pre-planned; adapter untouched), **failed**
(adapter raised or timed out; `boundary_failed` logged, day left empty). Exactly
one `boundary_runs` row is INSERTed per first-tick, at the terminal step of
whichever path — the guard makes any later tick for that date a no-op. The
judgment call touches no DB (it receives plain `BoundaryInputs` and returns a
`BoundaryJudgment`), so running it in a worker thread is connection-safe; all DB
writes happen on the main thread after `future.result()`.

Timeout mechanism: `concurrent.futures` — `future.result(timeout=...)` raises
`FuturesTimeout` on timeout and re-raises any adapter exception; both are caught
by the single `except` → `boundary_failed`. Deterministic under a fake: the fake
returns/raises instantly, so no real 60s wait occurs.

---

## 5. Test list — `tests/unit/test_days.py`

Header: module docstring naming items 1/12/17/18; `from __future__ import
annotations`. Fixtures used read-only from conftest: `cfg`, `tmp_db`,
`fake_clock`. Local helpers defined in-file: a `_mk_ticket(conn, id, *, state,
title, priority="P3", deadline=None)` inserter (writes the minimal NOT-NULL
columns of `tickets`), and `RecordingBoundaryAdapter` (see 0b). Events are read
with `read_events_since(conn, 0, 1000)` then filtered by `kind`/`entity_id`; DB
state is checked with direct `conn.execute(...).fetchall()`.

Ruff-clean, and one function per item (0a). No skips/xfail/empty bodies.

### `test_a01_planning_date(cfg)` — item 1 (§6.1)
- `planning_date(datetime(2026,7,5,4,59), cfg.boundary_hour) == date(2026,7,4)`
  (04:59 → previous date; `cfg.boundary_hour == 5`).
- `planning_date(datetime(2026,7,5,5,0), cfg.boundary_hour) == date(2026,7,5)`
  (05:00 → NEW date — the off-by-one watch).
- Boundary hour honored from config, and a non-default hour changes the result:
  `planning_date(datetime(2026,7,5,5,0), 6) == date(2026,7,4)` (05:00 − 6h stays
  on Jul 4), and `planning_date(datetime(2026,7,5,5,0), 0) == date(2026,7,5)`
  (no shift). Same instant, three hours → three different results ⇒ the hour is
  load-bearing.

### `test_a12_day_ticket_removal(tmp_db)` — item 12 (§3.4)
- Setup: insert tickets `t0,t1,t2,t3` (t1 state `in_progress`). `add_day_ticket`
  all four to `day_2026-07-04` → positions `[0,1,2,3]`.
- `remove_day_ticket(conn, "day_2026-07-04", "t1", now)` (t1 is a MIDDLE element).
- Assert `list_day_tickets` == `[(t0,0),(t2,1),(t3,2)]` — association gone,
  positions re-packed contiguously from 0 in original relative order.
- Assert `SELECT state FROM tickets WHERE id='t1'` still `in_progress` (state
  untouched — "deferring").
- Assert exactly one `day_ticket_removed` event on `day_2026-07-04` with payload
  `{"ticket_id": "t1"}`.

### `test_a17_plan_tree(tmp_db)` — item 17 (§6.3)
Build `tree = PlanTree(root=PlanRoot("focus", proposed), children=[
PlanNode("t_a","",proposed,0), PlanNode("t_b","",proposed,1),
PlanNode(None,"note",proposed,2)])`.
- **Root invalidation:** `nt, eff = invalidate_root(tree)`. Assert `nt.root.status
  == invalidated` and every child status `invalidated`. Assert `eff` contains
  `EmitEvent(plan_node_invalidated, {"node":"root"})` and **exactly one** replan
  request, a `ReplanRoot()` (`len([e for e in eff if isinstance(e,(ReplanRoot,
  ReplanChild))]) == 1`).
- **Child invalidation replaces only that child:** `nt2, eff2 =
  invalidate_child(tree, 1)`. Assert `nt2.children[1].status == invalidated`
  while `nt2.root.status == proposed`, `nt2.children[0].status == proposed`,
  `nt2.children[2].status == proposed` (other nodes keep status). Assert `eff2`
  has exactly one replan request `ReplanChild(1)` and
  `EmitEvent(plan_node_invalidated, {"node":1})`.
- **Accept-all + exactly-once day-list add (idempotent):** insert tickets
  `t_a,t_b`; `materialize_day("day_2026-07-05")`; pre-`add_day_ticket("t_b")`
  so the day list is `[t_b@0]`. `ntA, effA = accept_all(tree)`. Assert
  `ntA.root.status == accepted` and every child `accepted`. Assert `effA`
  contains `EmitEvent(plan_accepted_all, {})`, `AddTicketToDay("t_a")`,
  `AddTicketToDay("t_b")`, and NO AddTicketToDay for the `ticket_id=None` child.
  `apply_plan_effects(conn, "day_2026-07-05", ntA, effA, now)`. Assert
  `list_day_tickets == [(t_b,0),(t_a,1)]` — t_b appears **exactly once** (not
  re-added), t_a appended at end, positions contiguous. Assert `load_plan`
  reflects all-accepted statuses.

### `test_a18_boundary_job(tmp_db, fake_clock, cfg)` — item 18 (§6.2)
Uses `RecordingBoundaryAdapter` (0b). All DB setup via the day data layer /
`_mk_ticket`.

- **Part 1 — first tick ≥ 05:00 creates the day + carryover + overdue + close:**
  Insert `t_done` (state `done`, deadline None, title "Done one") and `t_prog`
  (state `in_progress`, deadline `"2026-07-01"`, title "Prog one", priority
  "P1"). `add_day_ticket("day_2026-07-04", "t_done")` then `"t_prog"`
  (positions 0,1). `fake_clock.set(datetime(2026,7,5,5,1).astimezone())`.
  `adapter1 = RecordingBoundaryAdapter()`. `run_boundary(conn, fake_clock, cfg,
  adapter1)`. Assert:
  - `day_2026-07-05` row exists; a `day_created` event for it is present.
  - `adapter1.received[0].carryover == [{"id":"t_prog","title":"Prog one",
    "state":"in_progress","priority":"P1"}]` (t_done excluded — done).
  - `adapter1.received[0].overdue == [{"id":"t_prog","title":"Prog one",
    "state":"in_progress","priority":"P1"}]` (deadline 2026-07-01 < 2026-07-05,
    not terminal; t_done excluded by state; no items → no item overdue).
  - `day_closed` on **`day_2026-07-04`** with payload
    `{"done_count":1, "not_done_count":1}`.
  - `plan_proposed` on `day_2026-07-05`; `load_plan` has exactly one child with
    `ticket_id == "t_prog"` (the recorder built children from carryover).
  - `boundary_runs` has one row for `2026-07-05` with `judgment == "ok"`.
  - `adapter1.calls == ["judgment"]`.
- **Part 2 — second tick same date does nothing:** snapshot
  `n_events = COUNT(*) events`, `n_runs = COUNT(*) boundary_runs`. Call
  `run_boundary(conn, fake_clock, cfg, adapter1)` again (same clock ⇒ same
  planning date). Assert `COUNT(*) events == n_events` (no new day events),
  `COUNT(*) boundary_runs == n_runs`, and `adapter1.calls == ["judgment"]`
  (no second judgment call).
- **Part 3 — explicit prior planning skips the judgment pass:**
  `fake_clock.set(datetime(2026,7,6,5,1).astimezone())` (fresh planning date
  `2026-07-06`, guard passes). Pre-plan it: `add_day_ticket("day_2026-07-06",
  "t_prog")` (any day-ticket ⇒ human-planned). `adapter3 =
  RecordingBoundaryAdapter()`. `run_boundary(conn, fake_clock, cfg, adapter3)`.
  Assert `adapter3.calls == []` (adapter not called) and `boundary_runs` row for
  `2026-07-06` has `judgment == "skipped"`. (Deterministic pass still ran: a
  `day_closed` for `day_2026-07-05` exists.)

---

## 6. Edge cases decided

- **Exactly 05:00 (off-by-one).** 05:00 belongs to the NEW date. Falls out of
  `(now − boundary_hour h).date()`: `(05:00 − 5h)` → 00:00 new date; `(04:59 −
  5h)` → 23:59 old date. Asserted in a01. No `>`/`>=` branch to get wrong.
- **Midnight crossing.** `(00:30 − 5h)` → previous day 19:30 → previous date;
  handled by the same subtraction, no special case.
- **Empty children list.** `accept_all` / `invalidate_root` over a childless tree
  → only the root changes; no `AddTicketToDay` (accept-all) and still exactly one
  `ReplanRoot` (invalidate-root). `invalidate_child` with an out-of-range
  position → no child matches; the tree is returned unchanged except nothing is
  invalidated, and one `ReplanChild(position)` is still requested (the position
  is the caller's addressing; stage-4 handles a missing target). List
  comprehensions over `[]` are safe.
- **Removal of a non-existent day-ticket.** `DELETE` rowcount 0 → no-op: no
  re-pack, no `day_ticket_removed` event, no error (idempotent remove).
- **Re-pack after removing a middle position.** Read survivors `ORDER BY
  position`, reassign `0..n-1` — preserves relative order (a12 removes the middle
  t1 and asserts `[t0@0, t2@1, t3@2]`). `position` has no UNIQUE constraint, so
  the sequential UPDATE loop is safe even with transient duplicates.
- **Idempotent day-list add.** `add_day_ticket` on an already-present
  `(day_id, ticket_id)` returns False with no event; `apply_plan_effects` relies
  on this so accept-all adds each child ticket exactly once (a17).
- **Judgment stored as PROPOSED.** `as_proposed` normalizes root + all children
  to `proposed` before storage, so a real adapter that returns other statuses
  can't smuggle an "accepted" node past the boundary (§6.2 "proposed plan").
- **`boundary_runs` single INSERT, guard-first.** The guard SELECT is the first
  statement; the INSERT is the last statement of each terminal path. The stage-4
  boundary scheduler runs inside the single server process one tick at a time
  (§7.1 dispatcher lock context), so no concurrent double-run; the tiny
  crash-between-writes window is out of scope for stage 3 and not reachable by
  the synchronous unit driver.
- **Approvals digest scope.** Reading `tickets.fields` / `sprint_items.
  status_proposal` for the digest is a read projection, not a write — it does not
  touch the resolution engine (T04) or violate "one canonical writer". Its rule
  is self-contained via `GATING_FIELD`; a18 does not assert its contents (item 18
  names only carryover + overdue), so no coupling to T04/T06 derived views.

---

## 7. Quality gates

- `.venv/bin/ruff check .` — line length 100, rules E,F,W,I,UP,B. Watch import
  order (I), unused imports (F401), `X | None` unions (UP), and no bare `except`
  in `boundary.py` (catch `Exception`, B).
- `.venv/bin/mypy src/` strict — every function fully annotated; `conn:
  sqlite3.Connection`; `Any` only inside the `list[dict[str, Any]]` digests that
  `BoundaryInputs` itself types that way; `append_event` return int ignored.
- `.venv/bin/pytest tests/unit/test_days.py -q` — the four functions green.
- Full `./verify` scoreboard: items 1, 12, 17, 18 flip to PASS (exactly one test
  per token — see 0a).

---

## 8. BINDING AMENDMENTS (orchestrator dispositions of the codex plan review)

These override the sections above where they conflict. See plan-review.md for
the findings and reasoning.

**A1 — `timeout_s` binding (§4 control flow).** In `run_boundary`, bind
`timeout_s = config.boundary_timeout_seconds` before the judgment `try` block;
pass it to both `_judgment_with_timeout(adapter, inputs, timeout_s)` and
`_error_text(exc, timeout_s)`. (Fixes the undefined name in the §4 pseudocode.)

**A2 — import `BoundaryJudgment` (§1.7 imports).** boundary.py imports
`from planner.core.adapters.base import BoundaryAdapter, BoundaryInputs,
BoundaryJudgment` — the helper `_judgment_with_timeout` returns `BoundaryJudgment`.

**A3 — full generics (§1.7 helpers).** `_read_overdue_candidates` and
`_read_approval_candidates` are typed
`-> tuple[list[dict[str, Any]], list[dict[str, Any]]]` (mypy strict forbids bare
`list`).

**A4 — approvals digest includes `needs_review` tickets (§1.5).** Per SPEC §4.5
the approval queue is: tickets with a pending proposal on their current gating
field, plus sprint items with pending status proposals, **plus `needs_review`
tickets**. `approvals_digest` therefore also emits a row for every ticket with
`state == TicketState.needs_review`: `{entity_id: id, kind: "review",
waiting_since: row["updated_at"]}` (DB-internal proxy for review entry time).
The ticket projection in `_read_approval_candidates` gains `updated_at`. The
combined digest is ordered oldest-pending first (ascending `waiting_since`),
mirroring §4.5. a18 still does not assert digest contents (not an item-18 fence).

**A5 — a18 Part 4: accepted-plan skip branch (§5).** Inside the same single
`test_a18_boundary_job` function, after Part 3: advance the clock to
`datetime(2026,7,7,5,1)` (fresh planning date `2026-07-07`, guard passes).
Pre-plan `day_2026-07-07` via the ACCEPTED-PLAN branch only — no day-tickets:
build a `PlanTree` with root `proposed` and one child `accepted` and
`store_plan(conn, "day_2026-07-07", tree, now)`. Run `run_boundary` with a
fresh `RecordingBoundaryAdapter`; assert its `calls == []` and the
`boundary_runs` row for `2026-07-07` has `judgment == "skipped"`. This covers
the "OR an accepted plan node exists" half of the §6.2 skip condition (Part 3
covers the day-ticket half).
