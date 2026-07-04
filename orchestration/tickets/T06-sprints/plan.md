# T06 — Sprints: implementation plan

Deliverable of the implementer: three owned paths only —
`src/planner/sprints/logic/` (new pure package), `src/planner/sprints/data.py`
(new), `tests/unit/test_sprints.py` (new). Nothing else is created or modified.
Contracts, `conftest.py`, `api.py`, `core/*` are LAW and read-only.

Fences: SPEC §18.3 item 10 (`test_a10_*`) and item 20 (`test_a20_*`).

---

## 0. Architecture at a glance

Three layers, strict dependency direction (per PRINCIPLES §Pure logic):

```
tests/unit/test_sprints.py
        │ calls
        ▼
src/planner/sprints/data.py         ← sqlite3 + events + clock live here only
        │ calls (decisions)          calls append_event, new_id
        ▼
src/planner/sprints/logic/*         ← pure: stdlib + contracts, zero side effects
        │ reads sets/consts
        ▼
src/planner/sprints/contracts.py  +  src/planner/core/contracts.py  (LAW)
```

- **Pure logic decides; data enforces.** Logic functions return booleans / small
  verdict enums / ids. They never raise `PlannerError`, never touch sqlite3, never
  import errors/events/db. `data.py` maps each verdict to a `PlannerError` with the
  exact `ErrorCode` and to the SQL mutation + event.
- **One canonical writer per state transition** (CLAUDE.md, SPEC §4.4.6). The item
  `status` field has exactly three writer doors: `transition_item_status` (direct
  agent/human edges), `propose_item_status` (files the gating proposal),
  `accept_item_status` (applies an accepted proposal). No other function writes
  `sprint_items.status`. `update_item_field` is forbidden from touching `status`.
- **Clock threading.** Every writer takes `clock: Clock` (keyword-only), calls
  `now = clock.now_unix()` **once**, and reuses that single integer for
  `created_at` / `updated_at` and every event's `created_at`, so an entity and the
  events it emits share one timestamp. Pure logic never sees the clock. Tests pass
  the `fake_clock` fixture (`TestClock` pinned to 2026-07-04 12:00 local).
- **Atomicity.** Read-only authorization checks (freeze, overlap, transition
  permission, existence) run first and may raise before any write. The mutation +
  its event(s) then run inside one `BEGIN IMMEDIATE … COMMIT` transaction (rollback
  on exception), so an event never exists without its state change and vice versa
  (`connect()` opens the DB in autocommit / `isolation_level=None`, so the writer
  must open the transaction explicitly).

---

## 1. `src/planner/sprints/logic/` — pure package

Every module starts with `from __future__ import annotations`. Imports allowed:
stdlib + `planner.sprints.contracts` + `planner.core.contracts`. No errors, no db,
no events, no clock, no sqlite3.

### 1.1 `logic/__init__.py`

Re-exports the public surface so `data.py` and tests import from
`planner.sprints.logic`:

```python
from planner.sprints.logic.blockers import blockers_cleared
from planner.sprints.logic.freeze import field_write_admissible, frozen_group
from planner.sprints.logic.ranges import (
    DateRange, current_sprint_id, find_overlap, ranges_overlap,
)
from planner.sprints.logic.transitions import (
    TransitionVerdict, classify_agent_transition, classify_human_transition,
)

__all__ = [
    "DateRange", "TransitionVerdict", "blockers_cleared",
    "classify_agent_transition", "classify_human_transition",
    "current_sprint_id", "field_write_admissible", "find_overlap",
    "frozen_group", "ranges_overlap",
]
```

### 1.2 `logic/transitions.py` — item status permissions (§3.2, §4.4.7)

Imports `ItemStatus, AGENT_ITEM_TRANSITIONS, PROPOSAL_ONLY_STATUSES` from
`planner.sprints.contracts` (uses the frozensets directly — never restates them),
`StrEnum` from `enum`, `Sequence` from `collections.abc`.

```python
class TransitionVerdict(StrEnum):
    allowed = "allowed"
    forbidden = "forbidden"            # not an agent/human edge, or into a proposal-only status
    missing_blockers = "missing_blockers"   # -> blocked with an empty blocked_by list
```

```python
def classify_agent_transition(
    from_status: ItemStatus,
    to_status: ItemStatus,
    blocked_by: Sequence[str],
) -> TransitionVerdict:
```
- `to_status in PROPOSAL_ONLY_STATUSES` → `forbidden` (done/deferred need a
  proposal; §3.2 last bullet). Uses the contract set as instructed.
- `(from_status, to_status) not in AGENT_ITEM_TRANSITIONS` → `forbidden` (only
  `todo↔active` and `*→blocked` are agent edges; §3.2). This also covers same-status
  writes (`from == to` has no self-edge in the set) and `blocked→todo/active`
  (agents may not leave blocked).
- `to_status is ItemStatus.blocked and not blocked_by` → `missing_blockers`
  (setting `blocked` requires a non-empty `blocked_by`; §3.2).
- else → `allowed`.

```python
def classify_human_transition(
    from_status: ItemStatus,
    to_status: ItemStatus,
    blocked_by: Sequence[str],
) -> TransitionVerdict:
```
Human latitude among the non-proposal statuses (see edge decision §5.1):
- `to_status in PROPOSAL_ONLY_STATUSES` → `forbidden` (even the human reaches
  done/deferred only through accept of a proposal, per §3.2).
- `from_status == to_status` → `forbidden` (no-op write).
- `to_status is ItemStatus.blocked and not blocked_by` → `missing_blockers`.
- else → `allowed` — permits any move within `{todo, active, blocked}`, which is how
  a human **leaves** `blocked` (`blocked→todo` / `blocked→active`) without inventing
  a new agent power.

### 1.3 `logic/blockers.py` — `blockers_cleared` derivation (§3.2)

Imports `Mapping, Sequence` from `collections.abc`, `Final` from `typing`.

```python
_TICKET_STATE_DONE: Final = "done"

def blockers_cleared(
    blocked_by: Sequence[str],
    ticket_states: Mapping[str, str],
) -> bool:
    if not blocked_by:
        return False
    return all(ticket_states.get(tid) == _TICKET_STATE_DONE for tid in blocked_by)
```
- True **iff** `blocked_by` is non-empty and every referenced ticket maps to
  ticket-state `"done"` (§3.2: "When every blocker reaches ticket-state `done`").
- Empty list → `False` (a non-blocked item is never "cleared"; the flag means
  "there were blockers, all now done").
- An id absent from `ticket_states` (unknown/not-found ticket) → `.get` yields
  `None ≠ "done"` → not cleared (conservative: still blocked).
- Compares against the literal `"done"` string, **not** the tickets domain's state
  enum — see edge decision §5.4 (no cross-domain coupling to T04's in-flight
  contract; the DB column is plain TEXT). Never stored (no column exists); computed
  on read.

### 1.4 `logic/freeze.py` — freeze admission (§3.1, §5)

Imports `KICKOFF_FIELDS, REVIEW_FIELDS` from `planner.sprints.contracts`.

```python
def frozen_group(field: str) -> str | None:
    if field in KICKOFF_FIELDS:
        return "kickoff"
    if field in REVIEW_FIELDS:
        return "review"
    return None

def field_write_admissible(
    field: str,
    kickoff_frozen_at: int | None,
    review_frozen_at: int | None,
) -> bool:
    group = frozen_group(field)
    if group == "kickoff":
        return kickoff_frozen_at is None
    if group == "review":
        return review_frozen_at is None
    return True
```
- Kickoff fields inadmissible once `kickoff_frozen_at` is set; review fields
  inadmissible once `review_frozen_at` is set; the two flags are **independent**
  (§3.1, §5).
- `weekly_addenda` is **not** in `KICKOFF_FIELDS`, so it lands in the `None` group →
  always admissible — encoding the "EXCEPT weekly_addenda append" carve-out. In
  practice the addenda writer never calls this (it has its own always-allowed door),
  but the function is correct if asked.
- Takes the two timestamps rather than a whole `Sprint` so it is unit-testable with
  scalar inputs (PRINCIPLES §Pure logic acid test).

### 1.5 `logic/ranges.py` — overlap + current-sprint selection (§3.1, §6.1)

Imports `Iterable` from `collections.abc`, `NamedTuple` from `typing`.

```python
class DateRange(NamedTuple):
    id: str
    date_start: str            # ISO date, inclusive
    date_end: str              # ISO date, inclusive
```
A minimal projection so overlap/selection depend only on the three columns they need
(not full `Sprint` hydration).

```python
def ranges_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start <= b_end and b_start <= a_end
```
Inclusive interval intersection; ISO date strings compare lexicographically (§3.1).
Boundary `a_end == b_start` ⇒ overlap (the `<=` on both sides).

```python
def find_overlap(
    date_start: str, date_end: str, existing: Iterable[DateRange],
) -> str | None:
    for r in existing:
        if ranges_overlap(date_start, date_end, r.date_start, r.date_end):
            return r.id
    return None
```
Returns the id of the first existing range that overlaps the candidate, else `None`
(used by sprint-create to reject overlap and name the conflict).

```python
def current_sprint_id(
    planning_date: str, sprints: Iterable[DateRange],
) -> str | None:
    for r in sprints:
        if r.date_start <= planning_date <= r.date_end:
            return r.id
    return None
```
The sprint whose inclusive `[date_start, date_end]` contains the planning date, else
`None` (§3.1 "current" sprint, §6.1 planning date supplied by the caller as an ISO
string — the boundary-hour math lives in the days domain, not here).

---

## 2. `src/planner/sprints/data.py` — canonical writers

`from __future__ import annotations`. Imports: `json`, `sqlite3`;
`contextlib.contextmanager`; `collections.abc.Iterator`, `typing.NamedTuple`;
`planner.core.clock.Clock`; `planner.core.contracts.EventKind`;
`planner.core.errors.ErrorCode, PlannerError`; `planner.core.events.append_event`;
`planner.core.ids.new_id, ID_PREFIXES`; from `planner.sprints.contracts`:
`Addendum, ItemStatus, ItemStatusProposal, KICKOFF_FIELDS, REVIEW_FIELDS,
PROPOSAL_ONLY_STATUSES, Sprint, SprintItem`; from `planner.core.contracts`:
`Priority, Project`; from `planner.sprints.logic`: the eight pure helpers.

Column names in every statement equal the contract dataclass field names one-for-one
(SPEC §3 / `db.py`).

### 2.0 Shared internals

```python
class ItemRead(NamedTuple):
    item: SprintItem
    blockers_cleared: bool

_ITEM_PLAIN_FIELDS: frozenset[str] = frozenset(
    {"title", "body", "priority", "deadline", "project", "current_state_note"}
)
_SPRINT_TEXT_FIELDS: frozenset[str] = frozenset(
    KICKOFF_FIELDS + REVIEW_FIELDS + ("name",)
)

@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
```

Row hydration helpers (pure de-serialization, no events):
- `_row_to_sprint(row: sqlite3.Row) -> Sprint` — `weekly_addenda` via
  `[Addendum(**a) for a in json.loads(row["weekly_addenda"])]`; frozen timestamps
  `int | None`; all text fields straight through.
- `_row_to_item(row: sqlite3.Row) -> SprintItem` — `status=ItemStatus(row["status"])`,
  `priority=Priority(row["priority"])`, `project=Project(row["project"])`,
  `blocked_by=json.loads(row["blocked_by"])`, `status_proposal` via
  `_proposal_from_json(row["status_proposal"])`.
- `_proposal_to_json(p: ItemStatusProposal) -> str` /
  `_proposal_from_json(raw: str | None) -> ItemStatusProposal | None` —
  `{to_status, note, proposed_by, created_at}` (store `to_status` as its `.value`;
  rebuild via `ItemStatus(...)`).
- `_load_sprint(conn, sprint_id) -> Sprint` / `_load_item(conn, item_id) -> SprintItem`
  — `SELECT * WHERE id=?`; raise `PlannerError(ErrorCode.not_found, …)` if the row is
  absent.

Enum values in event payloads are written as their `.value` (e.g.
`from_status.value`) for unambiguous JSON.

### 2.1 Sprint writers

**`create_sprint(conn, *, name, date_start, date_end, limiting_factor="",
primary_bet="", supports="", premortem="", clock) -> Sprint`**
- Validate `name` non-empty and `date_start <= date_end` → else
  `PlannerError(ErrorCode.validation, …)` (a clean error ahead of the DDL CHECK).
- `now = clock.now_unix()`. Open `_tx`. Inside: `SELECT id, date_start, date_end FROM
  sprints` → build `DateRange`s; `find_overlap(date_start, date_end, existing)`; if not
  `None` → `PlannerError(ErrorCode.sprint_overlap, "sprint dates overlap",
  {"conflict_id": …, "date_start": …, "date_end": …})` (raised inside `_tx`, so the
  `BEGIN IMMEDIATE` write-lock serializes concurrent creates; nothing is written).
- `sprint_id = new_id(ID_PREFIXES["sprint"])`. `INSERT` all columns
  (`weekly_addenda='[]'`, `kickoff_frozen_at`/`review_frozen_at` NULL, review-field
  columns `''`, `created_at=updated_at=now`).
- `append_event(conn, sprint_id, EventKind.sprint_created,
  {"name": name, "date_start": date_start, "date_end": date_end}, now)`.
- Return `_load_sprint(conn, sprint_id)`.
- Satisfies §3.1 ("creation rejects overlap"), §5.

**`update_sprint_field(conn, sprint_id, field, value, *, clock) -> Sprint`**
- `field not in _SPRINT_TEXT_FIELDS` → `PlannerError(ErrorCode.validation, …)`
  (blocks writing `weekly_addenda`/frozen timestamps/dates through this door — those
  have dedicated writers).
- `sprint = _load_sprint(...)`. `field_write_admissible(field, sprint.kickoff_frozen_at,
  sprint.review_frozen_at)` False → `PlannerError(ErrorCode.frozen_write,
  "field is frozen", {"field": field, "group": frozen_group(field)})` (§5 "rejected
  with a structured error").
- `now`; `prev = getattr(sprint, field)`. `_tx`: `UPDATE sprints SET {field}=?,
  updated_at=? WHERE id=?` (`{field}` is a validated whitelist member — safe
  interpolation; ruff has no S-rules selected but the whitelist keeps it injection-free).
- `append_event(conn, sprint_id, EventKind.sprint_updated,
  {"field": field, "from": prev, "to": value}, now)`. Return fresh `Sprint`.
- `name` writes are always admissible (`frozen_group` → `None`).

**`freeze_kickoff(conn, sprint_id, *, clock) -> Sprint`** /
**`freeze_review(conn, sprint_id, *, clock) -> Sprint`**
- `sprint = _load_sprint(...)`. **Idempotent latch** (edge decision §5.2): if the
  corresponding timestamp is already set, return `sprint` unchanged, no event, no
  clobber of the original freeze time. Otherwise `now`; `_tx`: `UPDATE sprints SET
  kickoff_frozen_at=?, updated_at=? WHERE id=?`; `append_event(conn, sprint_id,
  EventKind.kickoff_frozen, {"frozen_at": now}, now)`. `freeze_review` is symmetric
  with `review_frozen_at` and `EventKind.review_frozen`.
- Satisfies §5 (explicit freeze action sets the timestamp; independent flags).

**`add_addendum(conn, sprint_id, *, date, text, clock) -> Sprint`**
- Validate `text` non-empty and `date` present → else `ErrorCode.validation`.
- `sprint = _load_sprint(...)`. **No freeze check** — always allowed, including after
  `kickoff_frozen_at` is set (§3.1 carve-out). Append-only: new list =
  `sprint.weekly_addenda + [Addendum(date=date, text=text)]`.
- `now`; `_tx`: `UPDATE sprints SET weekly_addenda=?, updated_at=? WHERE id=?`
  (JSON of the full list); `append_event(conn, sprint_id, EventKind.addendum_added,
  {"date": date, "text": text}, now)`. Return fresh `Sprint`.

### 2.2 Sprint-item writers

**`create_item(conn, *, title, project, body="", priority=Priority.P3,
deadline=None, current_state_note="", sprint_id=None, clock) -> SprintItem`**
- Validate `title` non-empty → else `ErrorCode.validation`.
- `item_id = new_id(ID_PREFIXES["sprint_item"])`; status defaults `ItemStatus.todo`,
  `blocked_by=[]`, `status_proposal=NULL`. `now`. `_tx`: `INSERT`;
  `append_event(conn, item_id, EventKind.sprint_item_created,
  {"title": title, "project": project.value, "sprint_id": sprint_id}, now)`.
- Return fresh `SprintItem`.

**`update_item_field(conn, item_id, field, value, *, clock) -> SprintItem`**
- `field not in _ITEM_PLAIN_FIELDS` → `PlannerError(ErrorCode.validation, …)`.
  This is the hard fence that keeps `status`, `blocked_by`, `status_proposal`,
  `sprint_id` **out** of the plain-field door (edge decision §5.1 / §5.3).
- `item = _load_item(...)`; `prev = getattr(item, field)`. For `priority`/`project`
  validate `value` is a legal enum member (`Priority(value)` / `Project(value)` —
  `ValueError` → `ErrorCode.validation`). `now`; `_tx`: `UPDATE sprint_items SET
  {field}=?, updated_at=? WHERE id=?`; `append_event(conn, item_id,
  EventKind.item_updated, {"field": field, "from": prev, "to": value}, now)`. Return
  fresh `SprintItem`.

**`transition_item_status(conn, item_id, to_status, *, clock, by_agent=True,
blocked_by=None) -> SprintItem`** — canonical writer for the direct status edges
(`todo↔active`, `*→blocked`, and human `blocked→todo/active`).
- `item = _load_item(...)`; `from_status = item.status`.
- `intended = blocked_by if to_status is ItemStatus.blocked else []` (list, never
  `None`). `verdict = classify_agent_transition(from_status, to_status, intended)`
  when `by_agent` else `classify_human_transition(from_status, to_status, intended)`.
- `TransitionVerdict.forbidden` →
  `PlannerError(ErrorCode.item_transition_forbidden, "transition not permitted",
  {"from": from_status.value, "to": to_status.value})`.
  `TransitionVerdict.missing_blockers` →
  `PlannerError(ErrorCode.validation, "blocked requires a non-empty blocked_by",
  {"to": "blocked"})`.
- New column value for `blocked_by`: `intended` when `to_status is blocked` else `[]`
  (leaving/entering any non-blocked status clears blockers).
- `cause = "agent" if by_agent else "human"`. `now`; `_tx`: `UPDATE sprint_items SET
  status=?, blocked_by=?, updated_at=? WHERE id=?` (`status=to_status.value`,
  `blocked_by=json.dumps(new_blocked_by)`); `append_event(conn, item_id,
  EventKind.item_status_changed, {"from": from_status.value, "to": to_status.value,
  "cause": cause}, now)`. Return fresh `SprintItem`.
- Satisfies §3.2 (agent `todo↔active`, `→blocked` with blockers) and §4.4.6
  (one `{from,to,cause}` event per edge). Never writes done/deferred (those are
  `forbidden` here).

**`propose_item_status(conn, item_id, to_status, *, note, proposed_by, clock)
-> SprintItem`** — files the item's single gating-field proposal (§3.2, §4.4.1).
- `to_status not in PROPOSAL_ONLY_STATUSES` →
  `PlannerError(ErrorCode.validation, "only done/deferred_next_sprint are proposable",
  {"to": to_status.value})` (todo/active/blocked are direct transitions, never
  proposals).
- `item = _load_item(...)`. `now`. `_tx`:
  - If `item.status_proposal is not None` (supersede, §4.4.1): `append_event(conn,
    item_id, EventKind.proposal_superseded, {"field": "status",
    "replaced_body": _proposal_body(item.status_proposal)}, now)` **before** filing
    the new one, where `_proposal_body` → `{"to_status", "note", "proposed_by",
    "created_at"}`.
  - Build `ItemStatusProposal(to_status, note, proposed_by, created_at=now)`;
    `UPDATE sprint_items SET status_proposal=?, updated_at=? WHERE id=?` (status
    itself unchanged — the proposal parks; §4.4.3).
  - `append_event(conn, item_id, EventKind.proposal_filed, {"field": "status",
    "body": {"to_status": to_status.value, "note": note}, "proposed_by": proposed_by},
    now)`.
- Return fresh `SprintItem` (status unchanged, `status_proposal` set). Puts the item
  in the approval queue (§4.5).

**`accept_item_status(conn, item_id, *, clock, resolved_by="human") -> SprintItem`**
— applies an accepted proposal. **NO grant arguments** (§4.4.7 last sentence).
- Signature contains **no** `next_ceiling` and **no** `at_cap`; item accepts are
  terminal for agent involvement, so no onward-grant pair exists here. `resolved_by`
  is attribution only, not a grant. (Contrast: ticket gating-field accepts require the
  grant pair or raise `grant_missing`; this writer has nothing to raise it for.)
- `item = _load_item(...)`. `item.status_proposal is None` →
  `PlannerError(ErrorCode.validation, "no pending status proposal", {})`.
- `to_status = item.status_proposal.to_status`; `from_status = item.status`. `now`.
  `_tx`: `UPDATE sprint_items SET status=?, status_proposal=NULL, blocked_by='[]',
  updated_at=? WHERE id=?` (accepting done/deferred clears any blockers and the
  proposal); then two events, in order:
  1. `append_event(conn, item_id, EventKind.proposal_accepted, {"field": "status",
     "body": {"to_status": to_status.value}, "resolved_by": resolved_by,
     "edited": False}, now)` (payload shape per §4.4.2/4 `{field, body, resolved_by,
     edited}`; items have no free-text to edit, so `edited` is always `False` — see
     §5.5).
  2. `append_event(conn, item_id, EventKind.item_status_changed, {"from":
     from_status.value, "to": to_status.value, "cause": "accept"}, now)`.
- Return fresh `SprintItem` (status = accepted target, `status_proposal` = `None`).

**`assign_item_sprint(conn, item_id, sprint_id, *, clock) -> SprintItem`** —
plain event-logged field update of `sprint_id`, never a copy (§3.2).
- `item = _load_item(...)`; `prev = item.sprint_id`. If `sprint_id is not None`,
  pre-check the sprint exists (`_load_sprint` for a clean `not_found` ahead of the FK
  `IntegrityError`). `now`; `_tx`: `UPDATE sprint_items SET sprint_id=?, updated_at=?
  WHERE id=?`; `append_event(conn, item_id, EventKind.item_updated, {"field":
  "sprint_id", "from": prev, "to": sprint_id}, now)`. Return fresh `SprintItem`.
- `sprint_id=None` unassigns (→ backlog/deferred). Only `sprint_id` is touched — no
  sprint data is copied onto the item (§3.2).

### 2.3 Reads

**`read_item(conn, item_id) -> ItemRead`** — item plus `blockers_cleared` derived on
read (never stored; §3.2).
- `item = _load_item(...)`. If `item.blocked_by`: `SELECT id, state FROM tickets WHERE
  id IN (…)` (placeholders from the list) → `{row["id"]: row["state"]}`;
  `cleared = blockers_cleared(item.blocked_by, states)`. Else `cleared = False`.
- Read-only against the `tickets` table (T04 owns ticket **writers**; this only
  reads `state`). No clock. Return `ItemRead(item, cleared)`.

**`read_sprint(conn, sprint_id) -> Sprint`** — thin `_load_sprint` wrapper for
callers/tests.

(Current-sprint selection is provided as the pure `logic.current_sprint_id`; the
stage-4 API wires the planning date to it. No data-layer current-sprint reader is in
this ticket's writer list, so none is added here beyond the pure function + its unit
test.)

---

## 3. Event kinds and payloads (all via `append_event`, all `EventKind` members)

| Writer | EventKind | Payload |
|---|---|---|
| `create_sprint` | `sprint_created` | `{name, date_start, date_end}` |
| `update_sprint_field` | `sprint_updated` | `{field, from, to}` |
| `freeze_kickoff` | `kickoff_frozen` | `{frozen_at}` |
| `freeze_review` | `review_frozen` | `{frozen_at}` |
| `add_addendum` | `addendum_added` | `{date, text}` |
| `create_item` | `sprint_item_created` | `{title, project, sprint_id}` |
| `update_item_field` | `item_updated` | `{field, from, to}` |
| `assign_item_sprint` | `item_updated` | `{field:"sprint_id", from, to}` |
| `transition_item_status` | `item_status_changed` | `{from, to, cause}` (`cause` = `"agent"`/`"human"`) |
| `propose_item_status` | `proposal_filed` | `{field:"status", body:{to_status, note}, proposed_by}` |
| `propose_item_status` (supersede) | `proposal_superseded` | `{field:"status", replaced_body:{to_status, note, proposed_by, created_at}}` |
| `accept_item_status` | `proposal_accepted` **then** `item_status_changed` | `{field:"status", body:{to_status}, resolved_by, edited:false}` then `{from, to, cause:"accept"}` |

Idempotent freeze re-calls emit no event. All enum values serialized as `.value`.

---

## 4. `tests/unit/test_sprints.py`

`from __future__ import annotations`. Imports: `inspect`; `pytest`;
`planner.core.contracts.Project`, `planner.core.contracts.Priority`;
`planner.core.errors.ErrorCode, PlannerError`;
`planner.core.events.read_events_since`; `planner.sprints.contracts.ItemStatus`;
`planner.sprints.data` writers/reads. Fixtures `tmp_db`, `fake_clock` from the shared
`conftest.py` (read-only).

Local helpers (real bodies, no skips/xfail/empty):
```python
def _insert_ticket(conn, ticket_id: str, state: str) -> None:
    # T04 owns ticket writers; a direct INSERT is an accepted test-fixture shortcut
    # for setting blocker states (noted explicitly per ticket).
    conn.execute(
        "INSERT INTO tickets (id, title, state, created_at, updated_at) "
        "VALUES (?, ?, ?, 0, 0)", (ticket_id, "blk", state),
    )

def _events(conn, entity_id: str, kind: str) -> list[dict]:
    return [e.payload for e in read_events_since(conn, 0, 100_000)
            if e.entity_id == entity_id and e.kind == kind]
```
(Direct ticket INSERT is called out here as the sanctioned setup path: only `id`,
`title`, `state`, `created_at`, `updated_at` are non-defaulted NOT-NULL columns;
`project` may be NULL under its CHECK.)

### 4.1 Item 10 — `test_a10_*` (sprint-item permissions)

**`test_a10_agent_todo_to_active`**
- `create_item(...project=Project.Vylo, clock=fake_clock)` (starts `todo`).
- `transition_item_status(tmp_db, item.id, ItemStatus.active, clock=fake_clock)`.
- Assert returned `.status == ItemStatus.active`.
- Assert exactly one `item_status_changed` event with payload
  `{"from": "todo", "to": "active", "cause": "agent"}`.

**`test_a10_agent_direct_active_to_done_rejected`**
- Create item, transition to `active`.
- `with pytest.raises(PlannerError) as ei: transition_item_status(tmp_db, id,
  ItemStatus.done, clock=fake_clock)`.
- Assert `ei.value.code == ErrorCode.item_transition_forbidden` (**exact**).
- Assert `read_item(tmp_db, id).item.status == ItemStatus.active` (unchanged) and no
  `item_status_changed` event carries `"to": "done"`.

**`test_a10_done_via_proposal_then_accept`**
- Create item, transition to `active`.
- `propose_item_status(tmp_db, id, ItemStatus.done, note="ready",
  proposed_by="agent-x", clock=fake_clock)`. Assert status still `active`,
  `status_proposal.to_status == ItemStatus.done`; assert `proposal_filed` payload
  `{"field":"status","body":{"to_status":"done","note":"ready"},"proposed_by":"agent-x"}`.
- `accept_item_status(tmp_db, id, clock=fake_clock)`. Assert final `.status ==
  ItemStatus.done` and `.status_proposal is None` (cleared).
- Assert `proposal_accepted` payload `{"field":"status","body":{"to_status":"done"},
  "resolved_by":"human","edited":False}` and `item_status_changed`
  `{"from":"active","to":"done","cause":"accept"}`.
- **§4.4.7 guard:** `params = inspect.signature(accept_item_status).parameters`;
  assert `"next_ceiling" not in params and "at_cap" not in params` (item accepts
  carry no onward grant).

**`test_a10_proposal_supersedes_prior`**
- Create item → `active`; propose `done` (note "d1"); propose
  `deferred_next_sprint` (note "d2").
- Assert one `proposal_superseded` event with `replaced_body.to_status == "done"`;
  assert current `status_proposal.to_status == ItemStatus.deferred_next_sprint`,
  status still `active`.

**`test_a10_blocked_by_stored_and_blockers_cleared`**
- `_insert_ticket(tmp_db, "t_a", "in_progress")`, `_insert_ticket(tmp_db, "t_b",
  "done")`. Create item (`todo`).
- `transition_item_status(tmp_db, id, ItemStatus.blocked, clock=fake_clock,
  blocked_by=["t_a", "t_b"])`. Assert `.status == ItemStatus.blocked` and
  `.blocked_by == ["t_a", "t_b"]` (stored).
- `read_item(tmp_db, id).blockers_cleared is False` (`t_a` not done).
- `tmp_db.execute("UPDATE tickets SET state='done' WHERE id='t_a'")`;
  `read_item(tmp_db, id).blockers_cleared is True` (all blockers done).

**`test_a10_blocked_requires_blockers`**
- Create item (`todo`). `transition_item_status(..., ItemStatus.blocked,
  blocked_by=[])` → `PlannerError`, assert `code == ErrorCode.validation`; status
  unchanged (`todo`). (Encodes "non-empty `blocked_by`" from §3.2.)

### 4.2 Item 20 — `test_a20_*` (freeze rules + overlap)

**`test_a20_kickoff_write_after_freeze_rejected`**
- `create_sprint(tmp_db, name="S", date_start="2026-07-01", date_end="2026-07-14",
  clock=fake_clock)`.
- `update_sprint_field(tmp_db, sp.id, "primary_bet", "x", clock=fake_clock)` → assert
  `.primary_bet == "x"` and a `sprint_updated` event `{field:"primary_bet",...}`.
- `freeze_kickoff(tmp_db, sp.id, clock=fake_clock)` → assert `.kickoff_frozen_at` set
  and a `kickoff_frozen` event.
- `with pytest.raises(PlannerError) as ei: update_sprint_field(tmp_db, sp.id,
  "primary_bet", "y", clock=fake_clock)`; assert `ei.value.code ==
  ErrorCode.frozen_write`; assert `read_sprint(...).primary_bet == "x"` (unchanged).
- Idempotency (edge §5.2): call `freeze_kickoff` again; assert `kickoff_frozen_at`
  equals the first value (latch preserved).

**`test_a20_weekly_addenda_allowed_after_kickoff_freeze`**
- Create sprint; `freeze_kickoff`. `add_addendum(tmp_db, sp.id, date="2026-07-05",
  text="note", clock=fake_clock)` → assert `len(.weekly_addenda) == 1` with
  `Addendum(date="2026-07-05", text="note")`; assert an `addendum_added` event
  `{date, text}`. (The EXCEPT carve-out, §3.1.)

**`test_a20_review_freeze_independent_of_kickoff`**
- Create sprint. `update_sprint_field(..., "outcomes", "o")` succeeds.
- `freeze_review(...)` → assert `.review_frozen_at` set and `.kickoff_frozen_at is
  None` (independence).
- `update_sprint_field(..., "outcomes", "o2")` → `PlannerError`, `code ==
  frozen_write`.
- **Independence both ways:** after review freeze, `update_sprint_field(...,
  "primary_bet", "bet")` still **succeeds** (kickoff not frozen). And, separately in
  **`test_a20_kickoff_freeze_leaves_review_writable`**: create sprint,
  `freeze_kickoff`, then `update_sprint_field(..., "outcomes", "o")` **succeeds**.

**`test_a20_sprint_overlap_rejected`**
- `create_sprint(A, "2026-07-01", "2026-07-14")`.
- Overlapping interior: `create_sprint(B, "2026-07-10", "2026-07-20")` →
  `PlannerError`, `code == ErrorCode.sprint_overlap`.
- **Inclusive boundary:** `create_sprint(C, "2026-07-14", "2026-07-21")`
  (`date_start == A.date_end`) → `sprint_overlap` (inclusive ⇒ overlap).
- Adjacent, non-overlapping: `create_sprint(D, "2026-07-15", "2026-07-21")` →
  **succeeds** (`07-15 > 07-14`); assert D exists via `read_sprint`.

**`test_a20_current_sprint_selection`** (validates the pure `current_sprint_id`)
- Build `DateRange`s for A(`07-01..07-14`) and D(`07-15..07-21`).
- `current_sprint_id("2026-07-10", [A, D]) == A.id`;
  `current_sprint_id("2026-07-14", [A, D]) == A.id` (inclusive end);
  `current_sprint_id("2026-07-15", [A, D]) == D.id`;
  `current_sprint_id("2026-06-30", [A, D]) is None`.

Every test body performs real assertions; no `skip` / `xfail` / `only` / empty
bodies / commented-out tests (§18.2 instrument sweep, item 21).

---

## 5. Edge decisions (explicit)

**§5.1 — Can a human `item_update` write `status` directly? No.** `update_item_field`
whitelists only `{title, body, priority, deadline, project, current_state_note}` and
rejects `status` with `validation`. Status moves only through
`transition_item_status`, `propose_item_status`, or `accept_item_status` — one
canonical writer per edge (CLAUDE.md, §4.4.6). Likewise `blocked_by`,
`status_proposal`, and `sprint_id` are barred from this door (`sprint_id` has its own
`assign_item_sprint`).

**§5.2 — Freeze is an idempotent latch.** `freeze_kickoff`/`freeze_review` set the
timestamp only if currently `None`; a repeat call returns the sprint unchanged and
emits no event, preserving the original freeze time. Rationale: a freeze timestamp is
a real datum; re-latching must not clobber it, and a double-freeze is harmless rather
than an error. No fence contradicts this; asserted in
`test_a20_kickoff_write_after_freeze_rejected`.

**§5.3 — Who leaves `blocked`, and how? The human, via the same status-transition
writer.** SPEC §3.2 grants agents only `todo↔active` and `*→blocked`; it grants no
`blocked→…` exit. So leaving `blocked` is a **human** action:
`transition_item_status(..., by_agent=False)` uses `classify_human_transition`, which
permits any move within `{todo, active, blocked}` (so `blocked→todo` /
`blocked→active`) while still routing `done`/`deferred_next_sprint` through
`propose`/`accept`. This invents **no** new agent power (agents remain confined to the
contract set) and keeps one canonical writer for the direct status edges. Not
fence-tested; specified for one-writer integrity. `blockers_cleared` is only a signal
to the human — it never auto-moves the item (§3.2 "derived, not a status change").

**§5.4 — `blockers_cleared` compares against the string `"done"`, not a ticket-state
enum.** The comparison stays inside the sprints domain (stdlib + sprints/core
contracts) and reads the `tickets.state` TEXT column, avoiding a cross-domain import
of T04's in-flight `tickets` contract. An unknown/missing blocker id counts as
not-done (conservative). Never stored (no column); computed in `read_item`.

**§5.5 — Item accept has no edit variant.** A ticket field value is free text (so
edit-accept exists there); an item's "value" is a status from a fixed set, so
"editing" it would be a different proposal target. `accept_item_status` therefore only
accepts the proposed status, always logging `edited: false`. Combined with §4.4.7,
its signature carries neither an `edited` input nor any grant pair.

**§5.6 — `accept` with no pending proposal** → `ErrorCode.validation` ("no pending
status proposal"); the item exists, the proposal does not.

---

## 6. Conformance checklist

- **ruff (E,F,W,I,UP,B, line ≤ 100):** `from __future__ import annotations` in every
  module; `X | None` unions, builtin generics (`list[str]`, `dict[str, Any]`); no
  mutable default args (`blocked_by=None`, normalized internally); imports sorted
  (isort/`I`); no bare-`except` (the `_tx` handler catches `BaseException` then
  re-raises); lines wrapped ≤ 100.
- **mypy strict over `src/`:** every param and return annotated; `Clock` is a
  Protocol satisfied structurally by `TestClock`/`RealClock`; enum round-trips typed;
  `sqlite3.Row` accessed by column name; `_tx` typed `Iterator[None]`; no `Any`
  leakage beyond `JsonDict` payloads. (Tests live under `tests/`, outside the mypy
  `files=["src"]` set, but stay annotated for clarity.)
- **Pure logic isolation:** `logic/` imports only stdlib + `planner.*.contracts`; no
  errors/db/events/clock/sqlite3 — passes the PRINCIPLES acid test (unit-testable
  with no mocks; `test_a20_current_sprint_selection` exercises `ranges.py` directly).
- **Columns == dataclass fields** one-for-one (§3 / `db.py`), JSON columns
  (`weekly_addenda`, `blocked_by`, `status_proposal`) round-tripped as canonical JSON.
- **Owned paths only:** `src/planner/sprints/logic/**`, `src/planner/sprints/data.py`,
  `tests/unit/test_sprints.py`. No edits to contracts, `conftest.py`, `api.py`, or any
  `core/*`.
- **Both fences green** through `./verify`: `test_a10_*`, `test_a20_*`.

---

## 7. Binding amendments (post codex plan review — see plan-review.md)

These override anything above where they conflict. The implementer applies all five.

**A1 — `blocked_by` normalization (mypy strict).** In `transition_item_status`, the
parameter is `blocked_by: Sequence[str] | None = None`. Normalize before any
classifier call: `intended: list[str] = list(blocked_by or []) if to_status is
ItemStatus.blocked else []`. Only `intended` (never the raw param) is passed to
`classify_agent_transition` / `classify_human_transition` and stored.

**A2 — test imports and error-code literals.** `tests/unit/test_sprints.py` imports
`Addendum` (used in the addenda assertion) and compares error codes only as
`ErrorCode.<member>` (e.g. `ei.value.code == ErrorCode.frozen_write`), never as bare
names or strings.

**A3 — assert structured error detail, not just codes.**
- `test_a20_kickoff_write_after_freeze_rejected` (and the review-freeze test):
  additionally assert `ei.value.detail["field"] == <field>` and
  `ei.value.detail["group"] == "kickoff"` / `"review"`.
- `test_a20_sprint_overlap_rejected`: additionally assert
  `ei.value.detail["conflict_id"] == A.id` on both rejected creates.

**A4 — fence `deferred_next_sprint` on both sides.**
- In `test_a10_agent_direct_active_to_done_rejected` (or a sibling `test_a10_` test):
  also assert direct `transition_item_status(..., ItemStatus.deferred_next_sprint)`
  raises `ErrorCode.item_transition_forbidden`.
- Extend `test_a10_proposal_supersedes_prior`: after the supersede, call
  `accept_item_status` and assert final `.status == ItemStatus.deferred_next_sprint`
  and `.status_proposal is None` — accept-success proven for the second
  proposal-only status. (No `sprint_id` side effects are asserted or implemented:
  accepting `deferred_next_sprint` changes status only.)

**A5 — blocked-edge event asserted.** `test_a10_blocked_by_stored_and_blockers_cleared`
also asserts exactly one `item_status_changed` event with payload
`{"from": "todo", "to": "blocked", "cause": "agent"}`.

Codex finding 5 (mechanical human-only enforcement inside `accept_item_status`) is
refuted for this ticket — caller identity is API-layer wiring (stage 4, `agent_forbidden`)
and out of the owned file set; recorded as an integrator concern in plan-review.md.
