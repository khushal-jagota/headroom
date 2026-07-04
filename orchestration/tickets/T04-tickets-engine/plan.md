# T04 — Ticket state machine + resolution engine: implementation plan

Status: reviewed plan for implementation. Every behavior decision is made here. The implementer
makes zero design decisions; where the SPEC left a value open, the choice is pinned below and
marked **PINNED (delegated choice)**.

Contracts are law and are never modified: `src/planner/tickets/contracts.py`,
`src/planner/core/contracts.py` (EventKind payload comments binding), `src/planner/core/errors.py`.
Infrastructure as given: `core/db.py`, `core/events.py`, `core/ids.py`, `core/clock.py`,
`core/config.py`. T01 plan amendment 1 binds: `ceiling` / `next_ceiling` are restricted to
`STATE_ORDER` members (never `dropped`), validated on every write path.

Files owned (nothing else is touched):

- `src/planner/tickets/logic/` — new pure package (5 modules + `__init__.py`)
- `src/planner/tickets/data.py` — canonical writers over `sqlite3.Connection`
- `tests/unit/test_tickets_engine.py` — items 2, 3, 4, 5, 6, 7, 8, 13, 36

---

## 0. Pinned vocabulary (tests assert these exact strings)

### 0.1 `state_changed.cause` values — PINNED (delegated choice)

| cause string | emitted by |
|---|---|
| `"auto_accept"` | agent proposal auto-accepts and advances (§4.4.2) |
| `"human_accept"` | human accept or edit-accept of a gating-field proposal (§4.4.4) — edit-ness lives in `proposal_accepted.edited`, not in the cause |
| `"review_approve"` | the dedicated `needs_review` approve action (§4.4.5) |
| `"human_state_jump"` | human direct state set (§4.3) |
| `"drop"` | human drop action (§4.1) |

### 0.2 `grant_changed.cause` values — PINNED (delegated choice)

| cause string | emitted by |
|---|---|
| `"onward_grant"` | the mandatory grant pair applied on a human gating-field accept (§4.4.7) |
| `"human_grant"` | the direct human grant-change writer (ceiling/at-cap pickers) |

### 0.3 `resolved_by` values

- `"auto"` — auto-accepts (SPEC §4.4.2, verbatim).
- `"human"` — human accept and edit-accept. **PINNED (delegated choice)**: the human resolver
  string is exactly `"human"`, matching the actor string (§0.4).

### 0.4 Actor strings

- The human is exactly the string `"human"` — **PINNED (delegated choice)**. Everything else
  (`"agent"`, run-scoped actor strings, `PLAN_ACTOR` values) is an agent.
- Every writer takes `actor: str` explicitly. Proposals record `proposed_by = actor` verbatim.
- Claim validation (`PLAN_RUN_ID` + `PLAN_CLAIM` vs `claim_lock`, §7.6) is **not** this ticket —
  T05 wraps these writers at the API layer. T04 writers never read or write
  `claim_lock` / `claim_expires`.

All constants live in `src/planner/tickets/logic/resolution.py` as module-level `Final[str]`
(`CAUSE_AUTO_ACCEPT`, `CAUSE_HUMAN_ACCEPT`, `CAUSE_REVIEW_APPROVE`, `CAUSE_HUMAN_STATE_JUMP`,
`CAUSE_DROP`, `CAUSE_ONWARD_GRANT`, `CAUSE_HUMAN_GRANT`, `RESOLVED_BY_AUTO`, `RESOLVED_BY_HUMAN`)
except `HUMAN_ACTOR`, which lives in `logic/admission.py`. Tests import them rather than
retyping strings, but the values above are frozen — changing one is a contract change.

---

## 1. Package layout and import discipline

```
src/planner/tickets/logic/
    __init__.py        # docstring only; no re-exports (modules imported by full path)
    fields_codec.py    # TicketFields <-> JSON text for the tickets.fields column
    machine.py         # state order math, gating/advance application, grant resolution
    admission.py       # who may write what, when: §4.3 / §7.6 / §3.3 gate checks
    decisions.py       # the Decision / EventSpec dataclasses (internal shapes)
    resolution.py      # §4.4 semantics: action -> Decision
src/planner/tickets/data.py   # canonical writers: txn + apply + events
tests/unit/test_tickets_engine.py
```

Import rules (enforced by inspection at review):

- `logic/*` imports **only**: stdlib (`json`, `dataclasses`, `datetime`, `typing`),
  `planner.tickets.contracts`, `planner.core.contracts`, `planner.core.errors`.
  No `sqlite3`, no FastAPI, no pydantic, no `planner.core.db/events/ids/clock/config`.
- `data.py` imports: `sqlite3` (types only — the connection is passed in), `contextlib`,
  `planner.core.events` (`append_event`), `planner.core.ids` (`new_id`), the contracts/errors
  modules, and the `logic` package modules. No FastAPI/pydantic, no `planner.core.db`
  (the connection arrives constructed), no `planner.core.config` (tunables passed as arguments).
- The test module imports `planner.tickets.data`, the logic modules it asserts against,
  contracts, errors, and `planner.core.events.read_events_since`.
- Every module starts with `from __future__ import annotations` (repo style).

Rejections: pure logic raises `PlannerError` directly — that **is** the structured rejection
(errors.py's own contract: "Every domain rejection raises PlannerError with a stable code").
`data.py` never catches it; the transaction wrapper rolls back, so a raise guarantees
"nothing changed".

---

## 2. File-by-file blueprint

### 2.1 `src/planner/tickets/logic/__init__.py`

Docstring only:

```python
"""Pure ticket-domain logic: state machine math, write admission, and the §4.4
resolution decisions. Stdlib + contracts/errors imports only; no I/O anywhere."""
```

### 2.2 `src/planner/tickets/logic/fields_codec.py` — the serializer (PINNED location)

The `tickets.fields` JSON (de)serializer lives **here**, in the logic package (it is pure:
`json` + contracts). `data.py` calls it at every row boundary. The JSON shape matches the DDL
default byte-for-byte in structure: four keys, each
`{"value": str|null, "proposal": {"body","proposed_by","created_at"}|null, "notes": str|null}`.

```python
def fields_to_json(fields: TicketFields) -> str: ...
def fields_from_json(raw: str) -> TicketFields: ...
def get_slot(fields: TicketFields, field: FieldName) -> FieldSlot: ...
def with_slot(fields: TicketFields, field: FieldName, slot: FieldSlot) -> TicketFields: ...
```

- `fields_to_json`: builds the nested dict (enum-free, plain str/int/None) and returns
  `json.dumps(payload)`. Key order fixed: `success, approach, plan, result`; slot keys
  `value, proposal, notes`; proposal keys `body, proposed_by, created_at`.
- `fields_from_json`: `json.loads` then strict validation — the root must be a dict containing
  all four field keys, each slot a dict, types checked with `isinstance` (this is also what
  satisfies mypy strict over the `Any` that `json.loads` returns). Any malformation raises
  `PlannerError(ErrorCode.validation, "corrupt ticket fields JSON")`. **PINNED (delegated
  choice)**: strict parse, no tolerant defaults — the DDL default guarantees well-formed rows,
  so malformation is corruption and must be loud.
- `get_slot` / `with_slot`: mapping between `FieldName` and the four `TicketFields` attributes.
  `with_slot` is **copy-on-write**: it returns a *new* `TicketFields` (and new `FieldSlot`s are
  always constructed rather than mutated) so decision functions never mutate their input ticket.
  Maps to §4.2 ("exactly four keys").

### 2.3 `src/planner/tickets/logic/machine.py` — state order math

```python
def state_index(state: TicketState) -> int: ...
def is_terminal(state: TicketState) -> bool: ...
def gating_field(state: TicketState) -> FieldName | None: ...
def advance_target(state: TicketState, ceiling: TicketState) -> TicketState: ...
def auto_accept_target(state: TicketState, ceiling: TicketState, field: FieldName) -> TicketState | None: ...
def at_or_beyond_ceiling(state: TicketState, ceiling: TicketState) -> bool: ...
def validate_ceiling(ceiling: TicketState) -> None: ...
def resolve_grant(
    new_state: TicketState, next_ceiling: NextCeiling | None, at_cap: AtCap | None
) -> GrantPair: ...
def has_pending_gating_proposal(state: TicketState, fields: TicketFields) -> bool: ...
```

SPEC mapping and exact behavior:

- `state_index` — `STATE_ORDER.index(state)`; a state outside `STATE_ORDER` (i.e. `dropped`)
  raises `PlannerError(ErrorCode.validation, "state outside the linear order")`. Implements the
  "index compare" reading of ≤ (§4.4.2, contracts comment on `STATE_ORDER`).
- `is_terminal` — `state in (TicketState.done, TicketState.dropped)` (§4.1).
- `gating_field` — `GATING_FIELD.get(state)`; `None` for `needs_review`, `done`, `dropped`
  (§4.2 table).
- `advance_target(state, ceiling)` — the §4.2 advance column with the §4.4.5 special case:
  returns `TicketState.done` if `state is TicketState.in_progress and ceiling is
  TicketState.done`, else `ADVANCE_TARGET[state]`; raises
  `PlannerError(ErrorCode.validation, "state has no advance target")` for states not in
  `ADVANCE_TARGET` (defensive — callers only reach it from gating states).
- `auto_accept_target(state, ceiling, field)` — §4.4.2 verbatim: returns `None` when
  `is_terminal(state)`, when `field != gating_field(state)`, or when
  `state_index(advance_target(state, ceiling)) > state_index(ceiling)`; otherwise returns the
  advance target. Non-`None` ⇔ the proposal auto-accepts.
- `at_or_beyond_ceiling` — `state_index(state) >= state_index(ceiling)`; the at-cap branch
  selector for §4.3 (see §4 below for the beyond-ceiling ruling).
- `validate_ceiling` — `ceiling in STATE_ORDER` else
  `PlannerError(ErrorCode.grant_invalid, "ceiling must be a linear state", {"ceiling": ceiling.value})`.
  Implements T01 amendment 1.
- `resolve_grant(new_state, next_ceiling, at_cap)` — §4.4.7:
  - either argument `None` → `PlannerError(ErrorCode.grant_missing,
    "accept requires the onward grant pair", {"missing": [...]})` where `missing` lists
    `"next_ceiling"` and/or `"at_cap"` (both listed when both absent);
  - `next_ceiling == NO_FURTHER` (`"none"`) → `GrantPair(new_state_as_ceiling)` i.e.
    ceiling = the newly entered state;
  - otherwise `next_ceiling` must be a `STATE_ORDER` member (so `TicketState.dropped` →
    `grant_invalid`) with `state_index(next_ceiling) >= state_index(new_state)`, else
    `PlannerError(ErrorCode.grant_invalid, "next_ceiling must be at or beyond the new state",
    {"next_ceiling": <str>, "new_state": new_state.value})`;
  - returns the resolved `GrantPair(next_ceiling=<resolved ceiling state>, at_cap=at_cap)`.
    (The returned `next_ceiling` member always holds a concrete `TicketState`, never `"none"`.)
- `has_pending_gating_proposal(state, fields)` — `True` iff `gating_field(state)` is not `None`
  and that slot's `proposal` is not `None`. This is the §4.4.3 approval-queue membership
  predicate; the queue *view* is out of scope here, but item 3's "ticket in approval queue"
  assertion runs against this function, and the T06/API queue endpoint composes it.

### 2.4 `src/planner/tickets/logic/admission.py` — gate checks

```python
HUMAN_ACTOR: Final[str] = "human"

def is_human(actor: str) -> bool: ...
def require_human(actor: str, action: str) -> None: ...
def check_agent_proposal(
    state: TicketState, ceiling: TicketState, at_cap: AtCap, field: FieldName
) -> None: ...
def check_recap_writable(state: TicketState) -> None: ...
def check_sprint_assignable(ticket_id: str, sprint_item_id: str | None) -> None: ...
def validate_title(title: str, max_chars: int) -> None: ...
def validate_deadline(deadline: str | None) -> None: ...
def validate_body(body: str, what: str) -> None: ...
```

- `require_human(actor, action)` — raises `PlannerError(ErrorCode.agent_forbidden,
  f"{action} is a human-only action", {"action": action, "actor": actor})` when
  `actor != HUMAN_ACTOR`. `action` values are the writer names (`"accept_proposal"`,
  `"approve_review"`, `"set_state"`, `"drop_ticket"`, `"change_grant"`).
- `check_agent_proposal` — the full §4.3 admission matrix (see §4 below). Raise order:
  terminal check, then the at-cap branch. Implements §4.3 sentences 2–4 and the "agents may
  file a proposal on the current gating field only" clause.
- `check_recap_writable` — `state is TicketState.needs_success` →
  `PlannerError(ErrorCode.recap_too_early, "recap is writable only past needs_success",
  {"state": "needs_success"})`. All other states — including `done` and `dropped` — pass
  (ruling in §7.2 of this plan). Implements §3.3.
- `check_sprint_assignable` — `sprint_item_id is not None` →
  `PlannerError(ErrorCode.sprint_derived, "sprint_id is derived from the parent item",
  {"ticket_id": ticket_id, "sprint_item_id": sprint_item_id})`. Implements §3.3 / item 13.
- `validate_title` — empty title → `PlannerError(ErrorCode.validation, "title must be
  non-empty")` (**PINNED, delegated choice** — a titleless ticket is unusable and the CLI/UI
  always send one); `len(title) > max_chars` → `PlannerError(ErrorCode.title_too_long,
  f"title exceeds {max_chars} characters", {"length": len(title), "max": max_chars})`.
  No trimming — the title is stored exactly. Implements §3.3 "Title length ≤ 200 chars,
  enforced on every write path".
- `validate_deadline` — `None` passes; otherwise `datetime.date.fromisoformat` must succeed,
  else `PlannerError(ErrorCode.validation, "deadline must be an ISO date",
  {"deadline": deadline})` (§3.3: nullable ISO date).
- `validate_body(body, what)` — empty string → `PlannerError(ErrorCode.validation,
  f"{what} must be non-empty")`. Applied to proposal bodies and edit-accept text.
  **PINNED (delegated choice)**: empty proposals/edits are rejected; recap and notes are NOT
  passed through this (empty recap/note = deliberate clearing, see §7).

### 2.5 `src/planner/tickets/logic/decisions.py` — internal decision shapes

These are internal logic-layer shapes required by this ticket's design (they are not domain
contracts and are not exposed over the wire):

```python
@dataclass(frozen=True)
class EventSpec:
    kind: EventKind
    payload: JsonDict

@dataclass(frozen=True)
class Decision:
    events: tuple[EventSpec, ...]
    new_fields: TicketFields | None = None    # replacement fields object; None = untouched
    new_state: TicketState | None = None      # None = no transition
    new_ceiling: TicketState | None = None    # None = grant untouched
    new_at_cap: AtCap | None = None           # None = grant untouched
```

`new_ceiling`/`new_at_cap` are always set together or not at all (the grant is a pair, §4.3).
Purity contract: decision functions never mutate their input `Ticket`; `new_fields` is always a
fresh object built via `fields_codec.with_slot`.

### 2.6 `src/planner/tickets/logic/resolution.py` — §4.4 semantics as decisions

Constants from §0, plus:

```python
def decide_file_proposal(
    ticket: Ticket, field: FieldName, body: str, actor: str, now: int
) -> Decision: ...
def decide_accept(
    ticket: Ticket,
    field: FieldName,
    actor: str,
    edited_body: str | None,
    next_ceiling: NextCeiling | None,
    at_cap: AtCap | None,
) -> Decision: ...
def decide_approve(ticket: Ticket, actor: str) -> Decision: ...
def decide_state_jump(ticket: Ticket, new_state: TicketState, actor: str) -> Decision: ...
def decide_drop(ticket: Ticket, actor: str) -> Decision: ...
def decide_grant_change(
    ticket: Ticket, ceiling: TicketState, at_cap: AtCap, actor: str
) -> Decision: ...

def _state_change(old: TicketState, new: TicketState, cause: str) -> EventSpec: ...
```

`_state_change` is the **sole constructor** of `state_changed` EventSpecs in the codebase
(payload `{"from": old.value, "to": new.value, "cause": cause}`); every decide function that
transitions calls it exactly once. Combined with `data._apply_decision` being the sole event
appender for decisions, this is the §4.4.6 "exactly one `state_changed` per transition,
one canonical writer, no second path" guarantee (see the edge table, §5).

Behavior of each function is specified in §3 (semantics map), §4 (admission matrix), and §6
(payloads). Validation-order pins that matter:

- `decide_accept` runs: `require_human` → `validate_body(edited_body)` if edit →
  locate pending proposal (none → `PlannerError(ErrorCode.not_found,
  "no pending proposal on field", {"ticket_id": ..., "field": field.value})` — **PINNED
  (delegated choice)**: `not_found`, because the accept targets a proposal that does not exist)
  → if gating: compute `advance_target` then `resolve_grant` (so `grant_missing` /
  `grant_invalid` fire before any Decision exists — combined with the data.py rollback this is
  the "rejected with NOTHING changed" guarantee of §4.4.7) → build Decision.
- `decide_approve`: `require_human` → `ticket.state is not TicketState.needs_review` →
  `PlannerError(ErrorCode.validation, "approve requires state needs_review",
  {"state": ticket.state.value})` → Decision(new_state=done, one `_state_change`,
  no grant fields, no proposal event). §4.4.5: a dedicated action, not a field proposal;
  §4.4.7: carries no onward grant.
- `decide_state_jump`: `require_human` → `new_state is TicketState.dropped` →
  `PlannerError(ErrorCode.validation, "use the drop action")` (one writer per edge — drop has
  its own) → `ticket.state is TicketState.dropped` → `PlannerError(ErrorCode.validation,
  "dropped is terminal")` → `new_state == ticket.state` → `PlannerError(ErrorCode.validation,
  "ticket already in that state")` (**PINNED, delegated choice**: a no-op jump is rejected so a
  `state_changed` event always records a real transition) → Decision(new_state, one
  `_state_change` with cause `"human_state_jump"`). Fields are untouched — skipped gating
  fields keep `value = null` (§4.3); pending proposals are left in place (**PINNED, delegated
  choice**: they remain resolvable/supersedable; nothing in §4.3/§4.4 clears them). The jump
  never changes ceiling/at_cap and ignores the ceiling entirely (§4.3), including jumping out
  of `done` (reopen) — §4.3 says "any state" and only `dropped` is declared terminal
  (**PINNED, delegated choice**).
- `decide_drop`: `require_human` → `ticket.state is TicketState.done` →
  `PlannerError(ErrorCode.validation, "done tickets cannot be dropped", {"state": "done"})`
  (§4.1: reachable from any non-`done` state — pinned rejection) → `ticket.state is
  TicketState.dropped` → same error shape with `{"state": "dropped"}` (**PINNED, delegated
  choice**: `dropped` is terminal, re-drop is rejected rather than a silent no-op) →
  Decision(new_state=dropped, one `_state_change` cause `"drop"`).
- `decide_grant_change`: `require_human` → `validate_ceiling(ceiling)` → Decision(new_ceiling,
  new_at_cap, one `grant_changed` EventSpec cause `"human_grant"`). No relation to the current
  state is validated — the human may set the ceiling below the current state (that produces the
  at-or-beyond-cap admission posture, §4). Always emits the event even if values are unchanged
  (**PINNED, delegated choice**: the grant is an explicit human statement each time; writers
  stay branch-free).

### 2.7 `src/planner/tickets/data.py` — canonical writers

Module docstring pins the invariant: *"The only module that writes ticket rows. State,
ceiling/at_cap, and `fields` mutations happen in exactly one function (`_apply_decision`);
every public writer is one transaction."*

Internal helpers:

```python
@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

def _row_to_ticket(row: sqlite3.Row) -> Ticket: ...
def _load_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket: ...
def _apply_decision(
    conn: sqlite3.Connection, ticket: Ticket, decision: Decision, now: int
) -> Ticket: ...
```

- The connection comes from `core.db.connect` with `isolation_level=None` (autocommit), so the
  explicit `BEGIN IMMEDIATE` is the transaction boundary; `busy_timeout` (set in `connect`)
  handles writer contention. Every public writer's read-decide-apply runs inside one `_txn`.
- `_load_ticket` — `SELECT * FROM tickets WHERE id = ?`; missing →
  `PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})`.
  `_row_to_ticket` converts enums via constructors (`TicketState(row["state"])` …),
  `auto_blocked` via `bool(row["auto_blocked"])`, `fields` via `fields_codec.fields_from_json`.
- `_apply_decision` — the **single door**: one `UPDATE tickets SET fields=?, state=?, ceiling=?,
  at_cap=?, updated_at=? WHERE id=?` (unchanged aspects re-write current values), then
  `append_event(conn, ticket.id, spec.kind, spec.payload, now)` for each `EventSpec` in order.
  It is the only call site of `append_event` for `state_changed`, `proposal_filed`,
  `proposal_superseded`, `proposal_accepted`, and `grant_changed`. Returns the re-read Ticket.

Public writers (exact signatures; every one returns the freshly re-read `Ticket`, sets
`updated_at = now` on success, and changes nothing on any raise):

```python
def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    actor: str,
    now: int,
    title_max_chars: int,
    project: Project | None = None,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
) -> Ticket: ...

def read_ticket(conn: sqlite3.Connection, ticket_id: str) -> Ticket: ...
def get_effective_sprint_id(conn: sqlite3.Connection, ticket_id: str) -> str | None: ...

def file_proposal(
    conn: sqlite3.Connection, ticket_id: str, *, field: FieldName, body: str,
    actor: str, now: int,
) -> Ticket: ...

def accept_proposal(
    conn: sqlite3.Connection, ticket_id: str, *, field: FieldName, actor: str, now: int,
    edited_body: str | None = None,
    next_ceiling: NextCeiling | None = None,
    at_cap: AtCap | None = None,
) -> Ticket: ...

def approve_review(conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int) -> Ticket: ...
def set_state(
    conn: sqlite3.Connection, ticket_id: str, *, new_state: TicketState, actor: str, now: int
) -> Ticket: ...
def drop_ticket(conn: sqlite3.Connection, ticket_id: str, *, actor: str, now: int) -> Ticket: ...
def change_grant(
    conn: sqlite3.Connection, ticket_id: str, *, ceiling: TicketState, at_cap: AtCap,
    actor: str, now: int,
) -> Ticket: ...

def set_note(
    conn: sqlite3.Connection, ticket_id: str, *, field: FieldName, note: str | None,
    actor: str, now: int,
) -> Ticket: ...
def write_recap(
    conn: sqlite3.Connection, ticket_id: str, *, body: str, actor: str, now: int
) -> Ticket: ...
def set_priority(
    conn: sqlite3.Connection, ticket_id: str, *, priority: Priority, actor: str, now: int
) -> Ticket: ...
def set_deadline(
    conn: sqlite3.Connection, ticket_id: str, *, deadline: str | None, actor: str, now: int
) -> Ticket: ...
def set_sprint(
    conn: sqlite3.Connection, ticket_id: str, *, sprint_id: str | None, actor: str, now: int
) -> Ticket: ...
```

SPEC mapping per writer: see §3 (resolution writers), §7 (remaining writers).

Resolution-family writers (`file_proposal`, `accept_proposal`, `approve_review`, `set_state`,
`drop_ticket`, `change_grant`) are thin: `_txn` → `_load_ticket` → `resolution.decide_*` →
`_apply_decision`. Plain writers (`set_note`, `write_recap`, `set_priority`, `set_deadline`,
`set_sprint`, `create_ticket`) call the pure admission validators, then perform their own
single-column `UPDATE`/`INSERT` + `append_event` inside `_txn` — their UPDATE statements never
touch `state`, `ceiling`, `at_cap`, or `fields.value` (grep-able invariant). **PINNED**: only
the six resolution actions produce `Decision`s; plain field writes don't need the machinery and
must not acquire a second state-writing path.

The clock is passed as `now: int` (unix seconds) — callers use `clock.now_unix()`; tests use
`fake_clock.now_unix()`. The title limit is passed as `title_max_chars: int` with **no
default** — callers pass `config.title_max_chars` (**PINNED**: consistent with §2 "no tunable
inline"; the DDL `CHECK (length(title) <= 200)` is the backstop and T01 amendment 11's startup
assertion keeps config and DDL agreeing at 200).

### 2.8 `tests/unit/test_tickets_engine.py`

Specified fully in §9.

---

## 3. §4.4 semantics 1–7 — explicit function map

| § | Semantic | Named function(s) |
|---|---|---|
| 4.4.1 | Agent files a proposal; at most one pending per field; replacement logged `proposal_superseded` | `resolution.decide_file_proposal` (supersede branch), applied by `data.file_proposal` |
| 4.4.2 | Auto-accept iff gating field AND advance-target ≤ ceiling; value ← body, proposal cleared, one-step advance, `proposal_accepted` + `state_changed`, `resolved_by:"auto"` | `machine.auto_accept_target` (condition), `resolution.decide_file_proposal` (auto branch) |
| 4.4.3 | Otherwise pending; pending on current gating field ⇒ approval-queue member | `resolution.decide_file_proposal` (pending branch); membership predicate `machine.has_pending_gating_proposal` (queue view itself out of scope) |
| 4.4.4 | Human accept (value ← body) / edit-accept (value ← edited text, `edited: true`); no reject action anywhere | `resolution.decide_accept`; no reject function exists in any module and no writer clears a proposal except supersede/accept |
| 4.4.5 | `in_progress` result routing: → `needs_review` unless `ceiling = done` → `done`; `needs_review` approve (dedicated, human, no proposal) → `done` | `machine.advance_target` (special case), used by BOTH `decide_file_proposal` and `decide_accept`; `resolution.decide_approve` |
| 4.4.6 | Every transition: exactly one `state_changed {from,to,cause}`; one canonical writer per edge | `resolution._state_change` (sole constructor) + `data._apply_decision` (sole appender); edge table §5 |
| 4.4.7 | Onward grant pair mandatory on every human gating-field accept; `none` ⇒ ceiling = new state; missing pair → `grant_missing`, nothing changed; auto-accepts change neither ceiling nor at_cap | `machine.resolve_grant`, called from `resolution.decide_accept` (gating branch only); the auto branch of `decide_file_proposal` never sets `new_ceiling`/`new_at_cap` |

**Supersede-then-file ordering (PINNED)**: when a new proposal replaces a pending one and
*parks*, events are appended in the order `proposal_superseded` → `proposal_filed`. When it
replaces a pending one and *auto-accepts*, the order is `proposal_superseded` →
`proposal_accepted` → `state_changed`.

**`proposal_filed` is emitted only when the proposal parks pending (PINNED, delegated
choice)**: §4.4.2 enumerates the auto-accept's events exhaustively ("events `proposal_accepted`
+ `state_changed` logged") — the auto path emits exactly those two; the supplemental
`proposal_filed` exists to record the park that §4.4.3 describes.

**Result routing applies on BOTH auto and human accept paths — reasoning**: §4.4.5 states the
rule for "an accepted `result`" with no qualifier on who resolved it, and §4.4 defines one
engine — the resolver identity changes `resolved_by` and the grant obligation, never the state
math. Both `decide_file_proposal` and `decide_accept` therefore compute the new state through
the same `machine.advance_target(state, ceiling)`, where `ceiling` is the ticket's ceiling **in
force at resolution time (pre-accept)**; the onward grant pair is applied *after* entry (it
governs onward agent work from the newly entered state, §4.4.7) and never feeds the routing
decision. Consequence, pinned: a human accepting a parked `result` on a ticket whose ceiling
was previously raised to `done` sends it straight to `done`; with any lower ceiling it enters
`needs_review` and the human approves from there.

---

## 4. §4.3 admission matrix (`admission.check_agent_proposal`)

Let `at_cap_position = machine.at_or_beyond_ceiling(state, ceiling)`. Human callers never reach
this check — it applies to `file_proposal` for **every** actor (**PINNED, delegated choice**:
§4.4.1 says "an agent files a proposal"; there is no human proposal flow anywhere in SPEC — the
UI's human writes are accepts/edits — so `file_proposal` treats every caller as an agent
proposer and applies §4.3 uniformly; `proposed_by` records the actor string either way).
"Human writers ignore ceiling entirely" (§4.3) applies to the human writers of §2.6/§7 —
accept, jump, drop, grant, recap, note, plain fields — none of which consult the ceiling.

| Position | at_cap | Proposed field | Outcome |
|---|---|---|---|
| `state` terminal (`done`/`dropped`) | any | any | `PlannerError(ErrorCode.validation, "no proposals on a terminal ticket", {"state": <str>})` — **PINNED (delegated choice)**: terminal states have no gating field and no agent work (§4.1, §7.2) |
| below ceiling (`state_index(state) < state_index(ceiling)`) | `stop` or `propose` — **identical** | gating field | admitted → always auto-accepts (below ceiling, `advance_target ≤ ceiling` holds by construction) |
| below ceiling | either — identical | non-gating field | admitted → parks pending (`proposal_filed`) |
| at/beyond ceiling | `stop` | **any** field (gating included) | `PlannerError(ErrorCode.at_cap_stop, "ticket is at its ceiling with at_cap=stop", {"gating_field": <str or None>, "state": <str>, "ceiling": <str>, "at_cap": "stop"})` — detail includes the current gating field as required |
| at/beyond ceiling | `propose` | gating field | admitted → parks pending; nothing advances |
| at/beyond ceiling | `propose` | non-gating field | `PlannerError(ErrorCode.validation, "at the ceiling agents may propose only the current gating field", {"field": <str>, "gating_field": <str or None>, "state": <str>})` — **PINNED (delegated choice)**: §4.3 says "agents may file a proposal on the current gating field only"; rejected with `validation` because no dedicated code exists and `agent_forbidden` is reserved for agent calls to human-only *actions*, which this is not |

Beyond-ceiling ruling — **PINNED (delegated choice)**: a state *above* the ceiling (possible
after a human jump, since humans ignore the ceiling) is treated as at-cap
(`state_index >= ceiling_index`). The grant means "how far agents may advance"; at or beyond
that mark the grant is exhausted and only the at-cap vocabulary applies. Treating
beyond-ceiling as below-ceiling would let agents park proposals on arbitrary fields past the
grant, contradicting the at-cap clause's intent.

`needs_review` corollary (no gating field): below ceiling (`ceiling = done`) any field parks;
at/beyond ceiling with `propose` every field is rejected (there is no gating field to propose
on) and `detail["gating_field"]` is `None`. This matches §4.2's review flow, where the review
agent updates `result.notes` via `set_note` — never via proposals.

---

## 5. Transition-edge table — one canonical writer, no second path

All transitions are performed by **`data._apply_decision`** — the only function that includes
`state` in an `UPDATE tickets` statement and the only appender of `state_changed` events. The
"writer" column names the single public entry point whose Decision carries that edge; the
Decision's `state_changed` EventSpec is built only by `resolution._state_change`, called exactly
once per transitioning decision. There is no other code path: plain writers' UPDATEs exclude
`state`/`ceiling`/`at_cap`/`fields`, logic is pure, and no other module writes ticket rows.

| # | Edge | Cause(s) | Public writer (decision builder) |
|---|---|---|---|
| 1 | `needs_success → needs_approach` | `auto_accept` / `human_accept` | `file_proposal` (`decide_file_proposal` auto branch) / `accept_proposal` (`decide_accept` gating branch) — one edge, two causes, same `advance_target` math, same applier |
| 2 | `needs_approach → needs_plan` | `auto_accept` / `human_accept` | same pair as #1 |
| 3 | `needs_plan → in_progress` | `auto_accept` / `human_accept` | same pair as #1 |
| 4 | `in_progress → needs_review` (ceiling ≠ done) | `auto_accept` / `human_accept` | same pair as #1 |
| 5 | `in_progress → done` (ceiling = done, §4.4.5) | `auto_accept` / `human_accept` | same pair as #1 |
| 6 | `needs_review → done` | `review_approve` | `approve_review` (`decide_approve`) — the only writer for this edge; accepts no grant |
| 7 | any `STATE_ORDER` state → any *other* `STATE_ORDER` state (incl. reopening from `done`) | `human_state_jump` | `set_state` (`decide_state_jump`) |
| 8 | any state except `done`/`dropped` → `dropped` | `drop` | `drop_ticket` (`decide_drop`) |

Non-edges, pinned as rejections (nothing else exists): jump to `dropped` via `set_state`
(validation — drop owns edge 8), any transition out of `dropped` (validation — terminal), drop
from `done` (validation, §4.1), drop from `dropped` (validation), same-state jump (validation),
approve outside `needs_review` (validation). Agent-caused transitions exist **only** through
edges 1–5's auto branch — there is no agent path into `needs_review→done`, jumps, or drops
(`agent_forbidden` on those writers).

Every row above writes exactly one `state_changed {from, to, cause}` per transition.

---

## 6. Event payloads — exact shapes (per core/contracts.py comments, binding)

All enum-typed values are serialized as their `.value` strings. `entity_id` is always the
ticket id; `created_at` is the writer's `now`.

| Kind | Payload — exact keys and values |
|---|---|
| `state_changed` | `{"from": <old state str>, "to": <new state str>, "cause": <§0.1 string>}` |
| `proposal_accepted` | `{"field": <field str>, "body": <the stored value: proposal body, or the edited text on edit-accept>, "resolved_by": "auto"\|"human", "edited": false\|true}` — `edited` is `true` only on edit-accept (auto and plain human accept: `false`) |
| `proposal_superseded` | `{"field": <field str>, "replaced_body": <the replaced proposal's body>}` |
| `proposal_filed` | `{"field": <field str>, "body": <body>, "proposed_by": <actor string verbatim>}` |
| `grant_changed` | `{"ceiling": <state str>, "at_cap": "stop"\|"propose", "cause": "onward_grant"\|"human_grant"}` |
| `recap_updated` | `{}` — **PINNED (delegated choice)**: the contract comment names no keys; empty payload, the recap itself lives on the row |
| `note_updated` | `{"field": <field str>}` |
| `ticket_created` | `{}` — **PINNED (delegated choice)**: no keys named in the contract; creation values live on the row |
| `ticket_updated` | `{"field": "priority"\|"deadline"\|"sprint_id", "from": <old value or null>, "to": <new value or null>}` |

Event ordering within one write (append order = event id order, asserted in tests):

- park with supersede: `proposal_superseded`, `proposal_filed`
- auto-accept (with prior pending): [`proposal_superseded`,] `proposal_accepted`, `state_changed`
- human gating accept: `proposal_accepted`, `state_changed`, `grant_changed` (**PINNED**:
  accept → advance → onward grant, and `grant_changed` is emitted on **every** human
  gating-field accept, the pair being restated each time per §4.4.7)
- human non-gating accept: `proposal_accepted` only
- approve / jump / drop: one `state_changed` only
- grant change: one `grant_changed` only
- create / recap / note / priority / deadline / sprint: their single event only

---

## 7. Remaining writers — behavior pins

### 7.1 `create_ticket` (any actor)

R2 defaults: `state = needs_success`, `ceiling = needs_success`, `at_cap = propose`,
`priority = P3` (§4.3, §16 R2). `id = ids.new_id("t")`; `fields = TicketFields()` serialized via
`fields_codec`; `recap = ""`; `auto_blocked = 0`, `consecutive_failures = 0`; `claim_lock`,
`claim_expires`, `chat_session_key`, `alias` NULL; `created_at = updated_at = now`. All columns
written explicitly (no reliance on DDL defaults). Validations, in order: `validate_title`,
`validate_deadline`; if `sprint_item_id` given — the item must exist (`SELECT` →
`PlannerError(not_found, {"sprint_item_id": ...})`), `sprint_id` must be `None`
(else `sprint_derived` — the derived rule holds at creation), and `project` must be `None`
(else `PlannerError(validation, "project is derived when parented")` — §3.3 "nullable when
parented — derived from parent's item", **PINNED, delegated choice**); if `sprint_id` given —
the sprint must exist (`SELECT` → `not_found, {"sprint_id": ...}`). `project` is optional for
standalone tickets (**PINNED, delegated choice**: the DDL allows NULL and quick-capture flows
(§17) may not know the project). Event: `ticket_created {}`.

### 7.2 `write_recap` (any actor — `plan recap` is an agent verb)

`check_recap_writable(state)`: rejected with `recap_too_early` **only** at `needs_success`
(§3.3: "writable only when state is past `needs_success`"). **PINNED ruling**: `done` and
`dropped` both allow recap writes — §3.3 phrases the rule as a single lower bound with exactly
one rejected state, and the recap is a record of what happened, most valuable at terminal
states (a dropped ticket's "why we dropped it" is legitimate recap content). Overwrite always
allowed; empty body allowed (clears the recap — the column default is `''`). Event
`recap_updated {}`. No state effect (asserted in item 8).

### 7.3 `set_note` (any actor, any time — §4.2 "writable directly by human or agent at any
time")

Writes `fields.<field>.notes` via `fields_codec.with_slot` (value/proposal untouched) —
note: this is a `fields` column write but NOT a `fields.*.value` write; it bypasses
`_apply_decision` deliberately (notes are not resolution state) with its own UPDATE of the
`fields` and `updated_at` columns only. `note: str | None`; `None` clears (**PINNED**). Works
in every state including `done`/`dropped` (review-notes flow, §4.2). Event
`note_updated {"field": ...}`. No state effect.

### 7.4 `set_priority` / `set_deadline` (any actor — CLI `plan ticket set`)

`set_priority`: UPDATE + `ticket_updated {"field": "priority", "from": <old>, "to": <new>}`.
`set_deadline`: `validate_deadline` first; payload `from`/`to` are ISO strings or null. Both
always write and always log, even when unchanged (**PINNED, delegated choice**: branch-free
writers; the event stream records the write as issued).

### 7.5 `set_sprint` (any actor) — item 13

`check_sprint_assignable(ticket_id, ticket.sprint_item_id)` — parented →
`sprint_derived`, nothing changes. Standalone: if `sprint_id` is not `None` the sprint row must
exist (`SELECT` → `not_found`); `None` unassigns. Event
`ticket_updated {"field": "sprint_id", "from": ..., "to": ...}`. The "parent's sprint derived"
assertion reads through `get_effective_sprint_id`: returns `tickets.sprint_id` when
`sprint_item_id IS NULL`, else the parent `sprint_items.sprint_id` — a read helper, no stored
duplication (§3.3, §14 derived-on-read).

### 7.6 `change_grant` (human-only)

§2.6 `decide_grant_change`. Ceiling validated against `STATE_ORDER` (amendment 1); `dropped` or
any non-member → `grant_invalid`. Event `grant_changed` with cause `"human_grant"`.

### 7.7 `set_state` (human-only) and `drop_ticket` (human-only)

Pinned fully in §2.6. One `state_changed` each; causes `"human_state_jump"` / `"drop"`.

### 7.8 Title enforcement

`validate_title` runs on **every write path that takes a title** — in T04 that is exactly
`create_ticket` (no title-edit writer is in this ticket's scope; a future one must call the
same validator — noted in §11).

---

## 8. Actor rules summary

| Writer | `actor="human"` | agent actor |
|---|---|---|
| `create_ticket`, `file_proposal`*, `set_note`, `write_recap`, `set_priority`, `set_deadline`, `set_sprint`, `read_ticket`, `get_effective_sprint_id` | allowed | allowed |
| `accept_proposal`, `approve_review`, `set_state`, `drop_ticket`, `change_grant` | allowed | `PlannerError(ErrorCode.agent_forbidden, ..., {"action": <writer name>, "actor": <actor>})` via `admission.require_human`, before anything is read or written |

\* `file_proposal` applies the §4 admission matrix to every caller and records
`proposed_by = actor` verbatim (§4.4.1; ruling pinned in §4). Claim validation is T05's layer
above these signatures.

---

## 9. Test plan — `tests/unit/test_tickets_engine.py`

Harness: shared fixtures `tmp_db` (temp SQLite with real schema), `fake_clock`, `cfg` —
read-only, used as-is. No mocks anywhere; everything drives through `planner.tickets.data`
against the real DB. Module-level helpers (plain functions, not fixtures):

```python
def _create(conn: Connection, cfg: Config, clock: TestClock, **kw: Any) -> Ticket:
    # data.create_ticket(conn, title=kw.pop("title", "Test ticket"), actor="human",
    #                    now=clock.now_unix(), title_max_chars=cfg.title_max_chars, **kw)
def _grant(conn, t, ceiling, at_cap, clock) -> Ticket:   # change_grant as "human"
def _events(conn, cfg, ticket_id, kind: EventKind | None = None) -> list[EventRow]:
    # read_events_since(conn, 0, cfg.events_read_limit) filtered by entity_id (+kind), id order
```

Item 13's parented setup inserts the parent rows **directly via SQL** (sprints/items writers
belong to other tickets) — **PINNED**:

```sql
INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at)
VALUES ('sp_test', 'Test sprint', '2026-07-01', '2026-07-12', :now, :now);
INSERT INTO sprint_items (id, title, project, sprint_id, created_at, updated_at)
VALUES ('si_test', 'Parent item', 'Vylo', 'sp_test', :now, :now);
```

No `pytest.skip` / `xfail` / empty bodies anywhere (item 21's scan enforces this repo-wide).
Nine tests, exactly these names:

### `test_a02_gating_chain_one_state_per_accept` — fence: "accepting success/approach/plan/result proposals advances exactly one state each, in order, with one `state_changed` event per step"

- Create; `change_grant(ceiling=needs_review, at_cap=propose)` as `"human"`.
- `file_proposal` as `"agent"` on `success` ("success body") → assert state `needs_approach`,
  `fields.success.value == "success body"`, `fields.success.proposal is None`.
- Same for `approach` → `needs_plan`; `plan` → `in_progress`; `result` → `needs_review` —
  asserting after each step the state equals exactly the next state in order and the cumulative
  `state_changed` count is 1, 2, 3, 4.
- Final `state_changed` events (in id order) have `(payload["from"], payload["to"])` ==
  `[("needs_success","needs_approach"), ("needs_approach","needs_plan"),
  ("needs_plan","in_progress"), ("in_progress","needs_review")]`, every `payload["cause"] ==
  "auto_accept"`, payload keys exactly `{"from","to","cause"}`.
- Each `proposal_accepted` payload has `resolved_by == "auto"` and `edited is False`.

### `test_a03_ceiling_auto_accept_until_cap_then_pending` — fence: "ceiling `needs_plan` with `at_cap = propose`, proposals filed on success then approach auto-accept and advance; the plan proposal stays pending (state `needs_plan`, ticket in approval queue)"

- Create; grant (`needs_plan`, `propose`).
- File `success` → state `needs_approach`; file `approach` → state `needs_plan` (both auto).
- File `plan` ("plan body") → state **still** `needs_plan`; `fields.plan.value is None`;
  `fields.plan.proposal.body == "plan body"`, `.proposed_by == "agent"`.
- Approval-queue membership: `machine.has_pending_gating_proposal(t.state, t.fields) is True`.
- Exactly 2 `state_changed` events; exactly 1 `proposal_filed` event, payload
  `{"field": "plan", "body": "plan body", "proposed_by": "agent"}`.

### `test_a04_at_cap_stop_vs_propose` — fence: "at its ceiling with `stop`, any agent proposal — including the current gating field — is rejected with the structured error; with `propose`, a proposal on the current gating field files as pending and nothing advances; below the ceiling the behavior is identical for both values"

- Ticket A: grant (`needs_approach`, `propose`); file `success` (auto → at ceiling
  `needs_approach`). Then grant (`needs_approach`, `stop`).
  - `file_proposal` on `approach` (gating) → `pytest.raises(PlannerError)`;
    `exc.code is ErrorCode.at_cap_stop`; `exc.detail["gating_field"] == "approach"`.
  - `file_proposal` on `plan` (non-gating) → same `at_cap_stop`.
  - Ticket unchanged after both: state `needs_approach`, `approach` slot has no proposal,
    event count unchanged.
- Grant (`needs_approach`, `propose`):
  - file `approach` → parks: state still `needs_approach`, `fields.approach.proposal.body`
    set, `fields.approach.value is None` (nothing advances).
  - file `plan` (non-gating at cap with propose) → `pytest.raises(PlannerError)`,
    `exc.code is ErrorCode.validation` (pinned ruling §4), ticket unchanged.
- Below ceiling identical: tickets B (grant `needs_plan, stop`) and C (grant `needs_plan,
  propose`), both at `needs_success`:
  - file `success` on each → both auto-advance to `needs_approach` (identical states,
    identical `success.value`).
  - file `plan` (non-gating, below ceiling) on each → both park pending (identical slots:
    proposal body present, value None).

### `test_a05_one_pending_proposal_per_field_supersede` — fence: "a second proposal supersedes the first; `proposal_superseded` event carries the replaced body"

- Create (defaults: state = ceiling = `needs_success`, `at_cap = propose` ⇒ gating proposal
  parks).
- File `success` "first body" → pending.
- File `success` "second body" → `fields.success.proposal.body == "second body"` (exactly one
  pending proposal — the slot holds one).
- Exactly one `proposal_superseded` event; payload `== {"field": "success",
  "replaced_body": "first body"}`.
- Ordering: the `proposal_superseded` event id < the second `proposal_filed` event id
  (supersede-then-file, pinned §3); exactly 2 `proposal_filed` events total.
- State never moved (`needs_success`); no `state_changed` events.

### `test_a06_edit_accept_stores_edited_text` — fence: "accepting with edited text stores the edited text exactly as `value`, flags `edited: true` in the event"

- Create (defaults); file `success` "draft body" → parks.
- `accept_proposal(field=success, actor="human", edited_body="edited body exactly",
  next_ceiling="none", at_cap=AtCap.propose)`.
- `fields.success.value == "edited body exactly"` (exact equality); proposal cleared.
- `proposal_accepted` payload `== {"field": "success", "body": "edited body exactly",
  "resolved_by": "human", "edited": True}`.
- State `needs_approach`; `state_changed` payload `{"from": "needs_success",
  "to": "needs_approach", "cause": "human_accept"}`; ceiling == `needs_approach`
  (`"none"` ⇒ the newly entered state), at_cap == `propose`;
  `grant_changed` payload `{"ceiling": "needs_approach", "at_cap": "propose",
  "cause": "onward_grant"}`.

### `test_a07_result_routing` — fence: "accepted result with ceiling `needs_review` → state `needs_review`; with ceiling `done` → `done`; approve action in `needs_review` → `done`"

- Ticket 1: grant (`needs_review`, `propose`); file success/approach/plan (auto ×3 →
  `in_progress`); file `result` → state `needs_review`, `fields.result.value` set.
- `approve_review(actor="human")` → state `done`; the new `state_changed` payload
  `{"from": "needs_review", "to": "done", "cause": "review_approve"}`; ceiling/at_cap
  unchanged by approve.
- Ticket 2: grant (`done`, `propose`); file all four → after `result` the state is `done`;
  the `state_changed` sequence contains `("in_progress", "done")` and **no**
  `needs_review` entry.
- Ticket 3 (human path, pinned §3 reasoning): grant (`in_progress`, `propose`); three autos →
  `in_progress` (at cap); file `result` → parks; grant (`done`, `propose`); `accept_proposal
  (result, actor="human", next_ceiling="none", at_cap=AtCap.stop)` → state `done` directly
  (pre-accept ceiling `done` routes past `needs_review` on the human path too).

### `test_a08_recap_rules` — fence: "write at `needs_success` rejected with structured error; write at `needs_approach` succeeds, overwrites prior recap, logs event, changes no state"

- Create → `write_recap(body="too early", actor="agent")` → `pytest.raises(PlannerError)`,
  `exc.code is ErrorCode.recap_too_early`; `read_ticket(...).recap == ""`.
- Grant (`needs_approach`, `propose`); file `success` → state `needs_approach`.
- `write_recap("first recap")` → `recap == "first recap"`; one `recap_updated` event.
- `write_recap("second recap")` → `recap == "second recap"` (overwrite); two `recap_updated`
  events; state still `needs_approach`; `state_changed` count unchanged (exactly 1, from the
  auto-accept).
- Pinned-ruling lock: drop the ticket (`drop_ticket`, actor `"human"`), then
  `write_recap("post-drop recap")` succeeds and `recap == "post-drop recap"` (recap writable at
  terminal states, §7.2 ruling).

### `test_a13_sprint_assignment_rules` — fence: "standalone ticket accepts `sprint_id`; parented ticket's `sprint_id` write rejected; parent's sprint derived"

- Insert `sp_test` sprint row via direct SQL (pinned §9 preamble).
- Standalone: create; `set_sprint(sprint_id="sp_test", actor="agent")` →
  `read_ticket(...).sprint_id == "sp_test"`; `ticket_updated` payload
  `== {"field": "sprint_id", "from": None, "to": "sp_test"}`.
- Insert `si_test` item row (sprint `sp_test`) via direct SQL; create parented ticket with
  `sprint_item_id="si_test"`.
- `set_sprint(sprint_id="sp_test")` on it → `pytest.raises(PlannerError)`,
  `exc.code is ErrorCode.sprint_derived`; `sprint_id` still `None`; no `ticket_updated` event
  on the parented ticket.
- Derived read-through: `get_effective_sprint_id(conn, parented.id) == "sp_test"` (the parent
  item's sprint), while `get_effective_sprint_id(conn, standalone.id) == "sp_test"` comes from
  its own column.

### `test_a36_onward_grant` — fence: item 36, all six clauses

- **Missing pair**: create (defaults); file `success` "body" → parks. Record state snapshot +
  event count. `accept_proposal(..., actor="human", next_ceiling=None, at_cap=AtCap.stop)` →
  `grant_missing`; `accept_proposal(..., next_ceiling="none", at_cap=None)` → `grant_missing`;
  after each: state `needs_success`, `success.value is None`, `success.proposal.body ==
  "body"` (intact), ceiling `needs_success`, at_cap `propose`, event count unchanged —
  **changes nothing**.
- **Invalid pair (extra, pins §2.3)**: `next_ceiling=TicketState.needs_success` (before the new
  state `needs_approach`) → `grant_invalid`; `next_ceiling=TicketState.dropped` →
  `grant_invalid`; nothing changed either time.
- **Agent forbidden (extra, pins §8)**: `accept_proposal(..., actor="agent",
  next_ceiling="none", at_cap=AtCap.propose)` → `agent_forbidden`, nothing changed.
- **(`none`, `stop`) rests**: accept with `next_ceiling="none", at_cap=AtCap.stop` → state
  `needs_approach`, ceiling `needs_approach`, at_cap `stop`; a subsequent agent
  `file_proposal` on `approach` → `at_cap_stop` (the ticket rests).
- **(`none`, `propose`) permits pending**: second ticket, park + accept
  (`"none"`, `propose`) → agent files `approach` → parks pending
  (`fields.approach.proposal` set, state still `needs_approach`, no advance).
- **Later ceiling permits advancement**: third ticket, park + accept
  (`next_ceiling=TicketState.needs_plan`, `propose`) → ceiling `needs_plan`; agent files
  `approach` → auto-advances to `needs_plan`.
- **Auto-accepts change neither ceiling nor at_cap**: on that third ticket, after the auto
  accept: ceiling still `needs_plan`, at_cap still `propose`; `grant_changed` event count did
  not increase during the auto accept (only the one from the human accept exists).
- **Approve needs no grant**: fourth ticket, grant (`needs_review`, `propose`), four autos →
  `needs_review`; `approve_review(actor="human")` (signature takes no grant) → `done`;
  ceiling and at_cap unchanged by the approve; no new `grant_changed` event.

---

## 10. mypy strict + ruff compliance

- Config already pins ruff (line 100, py312, `E,F,W,I,UP,B`) and mypy strict over `src/`
  (tests are outside mypy `files`, but are still written with annotations on helpers).
- Every module: `from __future__ import annotations`; imports sorted for `I`; no line > 100
  (payload dicts and signatures wrap).
- JSON boundary (`Any` laundering) is confined to `fields_codec.fields_from_json`, which
  narrows `json.loads` output with `isinstance` checks before constructing typed
  `TicketFields`/`FieldSlot`/`Proposal` — no `type: ignore` anywhere. `sqlite3.Row`
  subscripting yields `Any`; `_row_to_ticket` converts through enum constructors and `bool()`
  so every `Ticket` field is concretely typed.
- Event payloads are `JsonDict` (`dict[str, Any]`) per core contracts; `EventSpec.kind` is
  `EventKind`, matching `append_event`'s signature exactly.
- Dataclasses `EventSpec`/`Decision` are frozen with immutable defaults (`tuple`), so no `B`
  (mutable-default) findings; `_txn` is a `contextlib.contextmanager` typed
  `Iterator[None]`.
- `StrEnum` members serialize via `.value` explicitly in payload construction (no reliance on
  implicit str behavior, keeps `UP`/readability clean).
- `node --check` is irrelevant (no JS touched); `python -m compileall src/` covered by the new
  modules being import-clean.

---

## 11. Out of scope (binding fences)

- **No day-assignment writer** — `day_tickets` writers belong to the days domain (T03).
  Orchestrator ruling, binding; the ticket.md mention of "day" in the writer list is
  overridden.
- No approval-queue / pickup / overdue view endpoints (only the pure membership predicate).
- No claim validation, no `claim_lock`/`claim_expires` reads or writes, no unblock/auto-block
  writer (T05/dispatch), no runs.
- No API/CLI wiring, no FastAPI/pydantic imports, no changes to `tickets/api.py` stubs.
- No sprint/sprint-item/link/day writers (tests insert parent rows via raw SQL).
- No title-edit writer (create is the only title path in T04).
- No contract, conftest, or infrastructure edits.

## 12. Open seams (for T05 / the API layer)

- **Claim layer (T05)** wraps `file_proposal` / `write_recap` (and run verbs) *above* these
  signatures: validate `PLAN_RUN_ID`+`PLAN_CLAIM` against the row, then call with
  `actor=<resolved actor string>`. T04 signatures need no change for that.
- **Human endpoints** call the human-only writers with `actor="human"` — the API layer is the
  place that decides a request is the human (UI) vs an agent (CLI/claim env, §7.6).
- **Grant pair over the wire**: the API parses `next_ceiling` from the request body; an
  *absent* key maps to `None` (→ `grant_missing` here); an unparseable/unknown string should be
  mapped by the API to `grant_invalid` before or at enum conversion — logic here guarantees it
  for `dropped`/before-new-state values.
- **Queues (T06/API)**: approval queue = `machine.has_pending_gating_proposal` over tickets
  (+ items + `needs_review`, other domains); dispatcher eligibility (§7.2) can reuse
  `machine.gating_field`, `machine.auto_accept_target` (clause (a): non-None target),
  and `machine.at_or_beyond_ceiling` + `at_cap` (clause (b)).
- **Serialization**: `read_ticket` → `Ticket` dataclass + `fields_codec.fields_to_json` are the
  API's rendering sources; `get_effective_sprint_id` feeds ticket GET/board payloads.
- **Events feed** untouched: everything goes through `append_event`, so `read_events_since`
  serves the WS layer as-is.
- Future title-edit writer must call `admission.validate_title` with `config.title_max_chars`.

## 13. Delegated-choice log (to copy into decisions.md at integration)

1. Cause vocabulary (§0.1/§0.2) and `resolved_by: "human"`; human actor string `"human"`.
2. `proposal_filed` only on park; auto-accept emits exactly `proposal_accepted` +
   `state_changed` (§3).
3. Supersede-then-file / supersede-then-accept ordering; human accept event order
   accepted → state → grant; `grant_changed` on every human gating accept (§6).
4. Result routing uses the pre-accept ceiling on both paths (§3 reasoning).
5. Non-gating agent proposal at/beyond cap with `propose` → `ErrorCode.validation` (§4).
6. Beyond-ceiling treated as at-cap (§4); terminal-state proposals → `validation` (§4).
7. `file_proposal` applies agent admission to all actors (no human proposal flow) (§4).
8. Recap writable at `done` and `dropped`; only `needs_success` rejects (§7.2).
9. Drop rejected from `done` (§4.1) and from `dropped`; jump-to-dropped rejected (drop owns the
   edge); no transitions out of `dropped`; human reopen out of `done` allowed; same-state jump
   rejected; pending proposals survive jumps (§2.6).
10. Grant halves passed as two optional args so item 36's "missing either half" is
    representable at the data layer; `GrantPair` is the resolved return type (§2.3).
11. `title_max_chars` and `now` passed as arguments (config/clock stay at the call site);
    empty titles, empty proposal bodies, and empty edit-accept text rejected with
    `validation`; empty recap/note allowed as clearing writes (§2.4, §7).
12. `fields` serializer in `logic/fields_codec.py`, strict parse (§2.2).
13. `no pending proposal` on accept → `not_found` (§2.6); plain writers always write + log even
    when unchanged (§7.4); grant change always logs (§2.6).
14. Item-13 test seeds sprint/item rows via direct SQL (§9).

---

## 14. Amendments after codex plan review (BINDING over the body above)

Per orchestration/tickets/T04-tickets-engine/plan-review.md:

**A1 — Recap gate rejects `dropped` too (supersedes §2.4, §7.2, §9/test_a08, log item 8).**
`admission.check_recap_writable(state)` raises
`PlannerError(ErrorCode.recap_too_early, "recap is writable only past needs_success",
{"state": state.value})` when `state is TicketState.needs_success` **or**
`state is TicketState.dropped`. Rationale: "writable only when past `needs_success`" is a
positive permission; `dropped` sits outside `STATE_ORDER`, so it is not "past
`needs_success`", and writing there would violate the "only". `needs_approach` through
`done` allow writes. test_a08's final step becomes: `drop_ticket` (actor `"human"`), then
`write_recap("post-drop recap")` → `pytest.raises(PlannerError)` with
`exc.code is ErrorCode.recap_too_early`, `exc.detail["state"] == "dropped"`, and
`read_ticket(...).recap == "second recap"` (unchanged).

**A2 — Single edge-owner function for the gating-acceptance edges 1–5 (supersedes the §5
narrative; the table rows stand).** New internal function in `logic/resolution.py`:

```python
def _accept_gating_proposal(
    ticket: Ticket,
    field: FieldName,
    stored_body: str,
    resolved_by: str,
    edited: bool,
    grant: GrantPair | None,
    cause: str,
    superseded_body: str | None = None,
) -> Decision: ...
```

It is the **sole constructor** of any Decision that traverses edges 1–5: sets
`value ← stored_body`, clears the proposal slot, computes the new state through
`machine.advance_target(ticket.state, ticket.ceiling)` (pre-accept ceiling, §3), and emits —
in order — [`proposal_superseded` if `superseded_body` is not None,] `proposal_accepted
{field, body: stored_body, resolved_by, edited}`, the single `_state_change(old, new,
cause)`, [and `grant_changed {…, cause: "onward_grant"}` iff `grant` is not None].
Callers: `decide_file_proposal` auto branch (`resolved_by="auto"`, `edited=False`,
`grant=None`, `cause="auto_accept"`) and `decide_accept` gating branch
(`resolved_by="human"`, `edited` per `edited_body`, `grant=resolve_grant(...)`,
`cause="human_accept"`). Neither branch builds acceptance events or transition state
itself; grep for `_state_change(` must show exactly the call sites inside
`_accept_gating_proposal`, `decide_approve`, `decide_state_jump`, `decide_drop`.

**A3 — `resolve_grant` return shape (fixes the §2.3 typo).** The `"none"` branch returns
`GrantPair(next_ceiling=new_state, at_cap=at_cap)`; every return carries both members, and
`at_cap` is never defaulted.

Codex finding 2 (day-assignment writer) is REFUTED in plan-review.md — §11's exclusion
stands: T03 owns the day-ticket writers in `src/planner/days/data.py`.
