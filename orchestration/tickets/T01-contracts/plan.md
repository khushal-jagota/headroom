# T01 — Contracts skeleton: implementation plan

Blueprint for the stage-1 contracts skeleton. Every shape an implementer needs is stated here exactly; nothing is left to invention. SPEC references are given per section. Python target: ≥3.12 (venv is 3.14) — `enum.StrEnum` is used for every string enum so `json.dumps` and SQLite bindings take members directly.

Conventions used throughout:
- All timestamps are unix seconds (`int`) named `created_at` / `updated_at` per SPEC §3.
- All dates are ISO `YYYY-MM-DD` strings in storage and on the wire; `datetime.date` inside pure logic.
- All JSON columns are `TEXT` holding canonical JSON; the contract dataclass is the single shape definition, the column name equals the contract field name.
- Domain `contracts.py` files import **stdlib only** (`dataclasses`, `enum`, `typing`, `datetime`). Pydantic/FastAPI appear only in `core/server.py` and the domain `api.py` files. `core/db.py`, `clock.py`, `ids.py`, `events.py`, `errors.py`, `config.py`, `contracts.py`, `adapters/*` (except nothing) are stdlib-only too (config uses PyYAML, which is allowed there — it is the config module's own dependency, not a framework).

---

## 1. File tree created by T01

```
pyproject.toml                        # new
config.yaml                           # new — every tunable, §13 + ticket list
.gitignore                            # already exists; ensure data/ ignored
src/planner/__init__.py               # __version__ = "2.0.0"
src/planner/__main__.py               # from planner.cli.main import main; main()
src/planner/py.typed                  # mypy marker, ships with package
src/planner/core/__init__.py
src/planner/core/contracts.py         # EventKind, LinkKind, shared aliases
src/planner/core/errors.py            # PlannerError, ErrorCode, JSON error shape
src/planner/core/config.py            # Config dataclass, YAML+env load, fail-safe read
src/planner/core/clock.py             # Clock protocol, RealClock, TestClock
src/planner/core/ids.py               # prefixed slug generation, day ids, claim tokens
src/planner/core/db.py                # connect (WAL) + full DDL + create_schema()
src/planner/core/events.py            # append_event / read_events_since — only event API
src/planner/core/adapters/__init__.py
src/planner/core/adapters/base.py     # Protocols + adapter request/result shapes
src/planner/core/adapters/fakes.py    # FakeSpawn, FakeBoundary, EchoGateway, OfflineGateway
src/planner/core/adapters/real.py     # RealSpawn/RealBoundary/RealGateway thin stubs
src/planner/core/adapters/registry.py # build_adapters(config) selection rules
src/planner/core/server.py            # FastAPI app factory shell, WS stub, test router
src/planner/tickets/__init__.py
src/planner/tickets/contracts.py
src/planner/tickets/api.py            # APIRouter stubs
src/planner/sprints/__init__.py
src/planner/sprints/contracts.py
src/planner/sprints/api.py
src/planner/days/__init__.py
src/planner/days/contracts.py
src/planner/days/api.py
src/planner/dispatch/__init__.py
src/planner/dispatch/contracts.py
src/planner/dispatch/api.py           # runs heartbeat/close routes
src/planner/seed/__init__.py
src/planner/seed/contracts.py
src/planner/seed/api.py               # POST /api/seed stub
src/planner/chat/__init__.py
src/planner/chat/contracts.py
src/planner/chat/api.py
src/planner/cli/__init__.py
src/planner/cli/main.py               # complete §8 verb tree (click), handlers stubbed
assets/.gitkeep
tests/unit/.gitkeep
tests/e2e/.gitkeep
tests/fixtures/.gitkeep
skills/.gitkeep
scripts/.gitkeep
```

No `logic/` or `data/` files yet — T01 is shapes and surfaces only. Later tickets add `tickets/logic/`, `tickets/data/`, etc.; the folders are created then, not now (empty Python packages invite drift).

`.gitignore` must contain `data/` (verify; add if missing).

---

## 2. `src/planner/core/contracts.py` — shared kinds

Stdlib only. Contains exactly: `EventKind`, `LinkKind`, and two aliases.

```python
from enum import StrEnum
from typing import Any

JsonDict = dict[str, Any]      # event payloads, adapter blobs
UnixTime = int                 # unix seconds


class LinkKind(StrEnum):                   # SPEC §3.6, exact strings
    belongs_to = "belongs_to"              # ticket → sprint item
    parent_child = "parent_child"          # ticket → ticket
    blocks = "blocks"                      # ticket → ticket | ticket → sprint item
    relates = "relates"                    # any → any


class EventKind(StrEnum):
    # --- named explicitly in SPEC ---
    state_changed = "state_changed"                  # §4.4.6 {from, to, cause}
    proposal_accepted = "proposal_accepted"          # §4.4.2/4 {field, body, resolved_by, edited}
    proposal_superseded = "proposal_superseded"      # §4.4.1 {field, replaced_body}
    day_ticket_removed = "day_ticket_removed"        # §3.4 {ticket_id}
    day_closed = "day_closed"                        # §6.2 {done_count, not_done_count}
    auto_blocked = "auto_blocked"                    # §7.5 {consecutive_failures}

    # --- supplemental: creation, one per entity ---
    ticket_created = "ticket_created"
    sprint_created = "sprint_created"
    sprint_item_created = "sprint_item_created"
    idea_created = "idea_created"
    day_created = "day_created"                      # §3.4 materialization

    # --- supplemental: proposals and fields ---
    proposal_filed = "proposal_filed"                # {field, body, proposed_by} (also item status: field="status")
    note_updated = "note_updated"                    # §4.2 notes slot {field}
    recap_updated = "recap_updated"                  # §3.3
    grant_changed = "grant_changed"                  # ceiling/at_cap change {ceiling, at_cap, cause}

    # --- supplemental: plain field updates (§3.2 "event-logged" updates) ---
    ticket_updated = "ticket_updated"                # {field, from, to} — priority/deadline/sprint/title/project
    item_updated = "item_updated"
    sprint_updated = "sprint_updated"
    day_updated = "day_updated"                      # notes/brief manual edits
    item_status_changed = "item_status_changed"      # {from, to, cause} incl. agent todo↔active

    # --- supplemental: day plan lifecycle ---
    plan_proposed = "plan_proposed"                  # boundary judgment stored {tree}
    plan_node_accepted = "plan_node_accepted"        # {node} ("root" | child position)
    plan_accepted_all = "plan_accepted_all"
    plan_node_invalidated = "plan_node_invalidated"  # {node}
    plan_replanned = "plan_replanned"                # §6.3 {old_tree, scope: "root"|"child", node}
    plan_rejected = "plan_rejected"                  # reject-all {old_tree}
    day_ticket_added = "day_ticket_added"            # {ticket_id, position, cause}
    boundary_failed = "boundary_failed"              # §6.2 adapter failure/timeout {error}

    # --- supplemental: runs and claims ---
    run_started = "run_started"                      # {run_id, pid}
    run_closed = "run_closed"                        # {run_id, status, summary}
    claim_heartbeat = "claim_heartbeat"              # {run_id, claim_expires}
    claim_reclaimed = "claim_reclaimed"              # {run_id, reason: "expired"|"dead_pid"}
    auto_block_cleared = "auto_block_cleared"        # human unblock action

    # --- supplemental: freeze, links, chat ---
    kickoff_frozen = "kickoff_frozen"
    review_frozen = "review_frozen"
    addendum_added = "addendum_added"                # {date, text}
    link_added = "link_added"                        # {from_id, to_id, kind}
    link_removed = "link_removed"
    chat_session_created = "chat_session_created"    # {session_key}
```

Payload key conventions are documented in the comment per member; payloads are `JsonDict` (no per-event dataclasses — the events table is an audit trail and invalidation signal, not a typed API).

`entity_id` for events: the id of the entity whose state changed (ticket/item/sprint/day/idea). Run and claim events use the **ticket id** as `entity_id` (the run id is in the payload) so a ticket's event log is one query. Link events use `from_id`. Boundary/day-plan events use the day id.

---

## 3. `src/planner/tickets/contracts.py`

```python
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal


class TicketState(StrEnum):        # §4.1, exact order
    needs_success = "needs_success"
    needs_approach = "needs_approach"
    needs_plan = "needs_plan"
    in_progress = "in_progress"
    needs_review = "needs_review"
    done = "done"
    dropped = "dropped"            # terminal, human-only, outside the linear order


# Linear pipeline order (dropped excluded). Index comparison implements "≤ ceiling".
STATE_ORDER: Final[tuple[TicketState, ...]] = (
    TicketState.needs_success, TicketState.needs_approach, TicketState.needs_plan,
    TicketState.in_progress, TicketState.needs_review, TicketState.done,
)


class FieldName(StrEnum):          # §4.2 — the exactly-four fields keys
    success = "success"
    approach = "approach"
    plan = "plan"
    result = "result"


class Priority(StrEnum):           # §3.2/§3.3
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class AtCap(StrEnum):              # §4.3
    stop = "stop"
    propose = "propose"


# §4.2 table — gating field per pre-terminal state. needs_review has none (human approve).
GATING_FIELD: Final[dict[TicketState, FieldName]] = {
    TicketState.needs_success: FieldName.success,
    TicketState.needs_approach: FieldName.approach,
    TicketState.needs_plan: FieldName.plan,
    TicketState.in_progress: FieldName.result,
}

# §4.2 table — accepted proposal advances to. in_progress → needs_review is the
# default route; the ceiling=done special case (§4.4.5) is resolution-engine logic,
# not a second table entry.
ADVANCE_TARGET: Final[dict[TicketState, TicketState]] = {
    TicketState.needs_success: TicketState.needs_approach,
    TicketState.needs_approach: TicketState.needs_plan,
    TicketState.needs_plan: TicketState.in_progress,
    TicketState.in_progress: TicketState.needs_review,
}


@dataclass(frozen=True)
class Proposal:                    # §4.2 proposal slot
    body: str
    proposed_by: str               # actor string: "agent", run id context, or PLAN_ACTOR
    created_at: int


@dataclass
class FieldSlot:                   # §4.2 — one of the four field objects
    value: str | None = None       # canonical; resolution engine is the only writer
    proposal: Proposal | None = None
    notes: str | None = None       # free guidance; result.notes = review notes (§4.2)


@dataclass
class TicketFields:                # tickets.fields JSON column, exactly four keys
    success: FieldSlot = field(default_factory=FieldSlot)
    approach: FieldSlot = field(default_factory=FieldSlot)
    plan: FieldSlot = field(default_factory=FieldSlot)
    result: FieldSlot = field(default_factory=FieldSlot)


# --- the grant pair (§4.4.7) ---
NO_FURTHER: Final = "none"                     # wire sentinel: ceiling becomes the newly entered state
NextCeiling = TicketState | Literal["none"]


@dataclass(frozen=True)
class GrantPair:                   # required on every human accept/edit-accept
    next_ceiling: NextCeiling
    at_cap: AtCap


@dataclass
class Ticket:                      # §3.3 — column names match exactly
    id: str
    title: str                     # ≤ title_max_chars (200), every write path
    state: TicketState
    priority: Priority             # default P3
    deadline: str | None           # ISO date
    project: str | None            # Project value; NULL when parented (derived)
    sprint_item_id: str | None
    sprint_id: str | None          # writable only when sprint_item_id IS NULL
    recap: str                     # writable only past needs_success
    ceiling: TicketState           # default needs_success (R2)
    at_cap: AtCap                  # default propose (R2)
    auto_blocked: bool
    consecutive_failures: int      # §7.5
    chat_session_key: str | None
    alias: str | None              # migration "Ticket ID:" (§12), unique when present
    fields: TicketFields
    claim_lock: str | None         # §7.3 claim token; NULL = unclaimed
    claim_expires: int | None
    created_at: int
    updated_at: int
```

Notes for the implementer:
- `project` is typed `str | None` here but validated against `sprints.contracts.Project` — the enum lives in sprints (its home domain, items own the project notion) and tickets/contracts must not import across domains just for an annotation. Cross-checked at the write path later. (Alternative — importing Project — creates a tickets→sprints contracts dependency; contracts stay leaf-flat instead.)
- `GrantPair` is the domain shape; the API request model (pydantic) in `tickets/api.py` mirrors it field-for-field and converts immediately.
- No `blockers`/`blocked` field on Ticket: blocked-ness is derived from links (§3.6), never stored.

---

## 4. `src/planner/sprints/contracts.py`

```python
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class Project(StrEnum):            # §3.2, exact strings (capitalised as in SPEC)
    Vylo = "Vylo"
    Tribe = "Tribe"
    Learning = "Learning"
    Other = "Other"


class ItemStatus(StrEnum):         # §3.2
    todo = "todo"
    active = "active"
    done = "done"
    blocked = "blocked"
    deferred_next_sprint = "deferred_next_sprint"


# Agent-permitted direct transitions (§3.2): todo↔active, and → blocked (with blockers).
AGENT_ITEM_TRANSITIONS: Final[frozenset[tuple[ItemStatus, ItemStatus]]] = frozenset({
    (ItemStatus.todo, ItemStatus.active),
    (ItemStatus.active, ItemStatus.todo),
    (ItemStatus.todo, ItemStatus.blocked),
    (ItemStatus.active, ItemStatus.blocked),
})

# Statuses reachable only via human-accepted proposal (§3.2).
PROPOSAL_ONLY_STATUSES: Final[frozenset[ItemStatus]] = frozenset({
    ItemStatus.done, ItemStatus.deferred_next_sprint,
})

# Freeze groups (§3.1): frozen-write checks compare against these tuples.
KICKOFF_FIELDS: Final[tuple[str, ...]] = ("limiting_factor", "primary_bet", "supports", "premortem")
REVIEW_FIELDS: Final[tuple[str, ...]] = (
    "outcomes", "solo_reflection", "joint_discussion", "updates_to_thinking", "carry_forward",
)


@dataclass(frozen=True)
class Addendum:                    # §3.1 weekly_addenda entry
    date: str                      # ISO date
    text: str


@dataclass(frozen=True)
class ItemStatusProposal:          # §3.2 status proposal — the item's single gating field
    to_status: ItemStatus          # must be in PROPOSAL_ONLY_STATUSES
    note: str | None               # optional rationale shown in Review
    proposed_by: str
    created_at: int


@dataclass
class Sprint:                      # §3.1
    id: str
    name: str
    date_start: str                # ISO, inclusive
    date_end: str                  # ISO, inclusive
    limiting_factor: str
    primary_bet: str
    supports: str
    premortem: str
    weekly_addenda: list[Addendum] = field(default_factory=list)   # append-only
    kickoff_frozen_at: int | None = None
    outcomes: str = ""
    solo_reflection: str = ""
    joint_discussion: str = ""
    updates_to_thinking: str = ""
    carry_forward: str = ""
    review_frozen_at: int | None = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class SprintItem:                  # §3.2
    id: str
    title: str
    body: str
    status: ItemStatus
    priority: Priority             # imported from planner.tickets.contracts
    deadline: str | None
    project: Project
    current_state_note: str
    sprint_id: str | None          # NULL = backlog/deferred
    blocked_by: list[str] = field(default_factory=list)   # ticket ids; non-empty iff blocked
    status_proposal: ItemStatusProposal | None = None
    created_at: int = 0
    updated_at: int = 0
```

One cross-domain contract import exists and is deliberate: `sprints/contracts.py` imports `Priority` from `tickets/contracts.py` (one shape, one place; both domains genuinely share it, and tickets is its primary home because the dispatcher orders by it). No other contracts file imports another domain's contracts except as listed in §7 (adapters) below. `blockers_cleared` is derived on read (§3.2) — it appears in API response models, never in the dataclass or DDL.

---

## 5. `src/planner/days/contracts.py`

```python
from dataclasses import dataclass, field
from enum import StrEnum
from datetime import date, datetime
from typing import Callable, Final


class NodeStatus(StrEnum):         # §6.3
    proposed = "proposed"
    accepted = "accepted"
    invalidated = "invalidated"


@dataclass
class PlanRoot:                    # §6.3 root node
    focus: str
    status: NodeStatus = NodeStatus.proposed


@dataclass
class PlanNode:                    # §6.3 child node
    ticket_id: str | None
    note: str
    status: NodeStatus
    position: int


@dataclass
class PlanTree:                    # days.plan JSON column: {root, children}
    root: PlanRoot
    children: list[PlanNode] = field(default_factory=list)   # one level in v1


@dataclass
class Day:                         # §3.4
    id: str                        # day_YYYY-MM-DD (planning date)
    brief: str
    notes: str
    plan: PlanTree | None
    chat_session_key: str | None
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class DayTicket:                   # day_tickets row (§3.4)
    day_id: str
    ticket_id: str
    position: int                  # contiguous from 0


# Planning-date math (§6.1): implemented in days/logic/dates.py at stage 3.
# Signature is the contract: planning_date(now, boundary_hour) -> calendar date of (now - boundary_hour hours).
PlanningDateFn = Callable[[datetime, int], date]
```

---

## 6. `src/planner/dispatch/contracts.py`

```python
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from planner.tickets.contracts import AtCap, Priority, TicketState


class RunStatus(StrEnum):          # §7.3 six + spawn_failed (§7.5)
    running = "running"
    done = "done"
    blocked = "blocked"
    crashed = "crashed"
    timed_out = "timed_out"
    reclaimed = "reclaimed"
    spawn_failed = "spawn_failed"


# Statuses that count as failures for the circuit breaker (§7.5).
FAILURE_STATUSES: Final[frozenset[RunStatus]] = frozenset({
    RunStatus.crashed, RunStatus.timed_out, RunStatus.spawn_failed,
})

# Outcomes an agent may pass to `plan run close --outcome` (terminal, agent-reportable).
AGENT_CLOSE_OUTCOMES: Final[frozenset[RunStatus]] = frozenset({
    RunStatus.done, RunStatus.blocked,
})


@dataclass
class Run:                         # §7.3 runs row — column names match
    id: str                        # run_<slug>
    ticket_id: str
    status: RunStatus
    started_at: int
    ended_at: int | None
    summary: str | None
    error: str | None
    pid: int | None


@dataclass(frozen=True)
class DispatchCandidate:
    """Input row for the pure eligibility + ordering functions (§7.2).
    Assembled by the data layer; logic never touches the DB."""
    ticket_id: str
    state: TicketState             # from planner.tickets.contracts
    priority: Priority
    deadline: str | None           # ISO date, NULLs ordered last
    created_at: int
    ceiling: TicketState
    at_cap: AtCap
    auto_blocked: bool
    has_active_claim: bool         # claim_lock set and unexpired
    is_blocked: bool               # derived from links (§3.6)
    gating_pending: bool           # current gating field has a pending proposal
```

`dispatch/contracts.py` imports `TicketState`, `Priority`, `AtCap` from `tickets/contracts.py` — same one-place rule as above.

---

## 7. `src/planner/seed/contracts.py`

```python
from dataclasses import dataclass, field
from typing import Final


# §12 mapping tables — exact source strings on the left.
ITEM_STATUS_MAP: Final[dict[str, ItemStatus]] = {
    "Todo": ItemStatus.todo,
    "In Progress": ItemStatus.active,
    "Done": ItemStatus.done,
    "Blocked": ItemStatus.blocked,
    "Deferred": ItemStatus.deferred_next_sprint,
}

READINESS_MAP: Final[dict[str, TicketState]] = {
    "Concepts": TicketState.needs_success,
    "Needs Shaping": TicketState.needs_approach,
    "Ready": TicketState.needs_plan,
    "In Progress": TicketState.in_progress,
}

# `Priority:` labels are literal P0..P3 in the source (verified in the snapshot) — identity map.
PRIORITY_MAP: Final[dict[str, Priority]] = {p.value: p for p in Priority}

# `Urgency:` is present-but-empty throughout the snapshot. Mapped as a fallback only:
# consulted when a Priority label is absent; empty/unknown values are ignored (P3 default applies).
URGENCY_MAP: Final[dict[str, Priority]] = {
    "critical": Priority.P0, "high": Priority.P1, "medium": Priority.P2, "low": Priority.P3,
}   # keys compared case-insensitively

# Project headings/labels are the enum's exact strings — identity map.
PROJECT_MAP: Final[dict[str, Project]] = {p.value: p for p in Project}

# `Mode:` is dropped (§12); the parser records it nowhere but it is NOT a skipped
# section (it is a recognised, deliberately-discarded field).


# --- parsed intermediates (parser output, importer input; no DB types) ---

@dataclass
class ParsedSprint:
    name: str
    date_start: str
    date_end: str
    limiting_factor: str = ""
    primary_bet: str = ""
    supports: str = ""
    premortem: str = ""
    outcomes: str = ""
    solo_reflection: str = ""
    joint_discussion: str = ""
    updates_to_thinking: str = ""
    carry_forward: str = ""


@dataclass
class ParsedItem:                  # sprint-tracking.md item or deferred.md item
    title: str
    status: ItemStatus
    priority: Priority
    project: Project
    body: str = ""
    deadline: str | None = None
    deferred: bool = False         # True → sprint_id stays NULL


@dataclass
class ParsedTicket:                # workspace.md ticket
    title: str
    state: TicketState
    priority: Priority
    alias: str | None = None       # "Ticket ID:"
    chat_session_key: str | None = None   # "Chat ID:"
    body: str = ""                 # → fields.success/approach values per §12
    success: str | None = None
    approach: str | None = None
    item_title: str | None = None  # link target for unambiguous title match


@dataclass
class ParsedIdea:
    title: str
    body: str = ""
    project: Project | None = None


@dataclass(frozen=True)
class SkippedSection:              # §12: silent drops forbidden — every skip is enumerated
    source_file: str               # path relative to the seed source dir
    heading: str | None            # nearest heading, if any
    reason: str                    # human-readable why it could not be parsed
    excerpt: str                   # first ~120 chars of the skipped text


@dataclass
class MigrationReport:             # printed, and structured under --json
    sprints: int = 0
    sprint_items: int = 0          # items with a sprint
    deferred_items: int = 0        # items imported with sprint_id NULL
    tickets: int = 0
    ideas: int = 0
    links: int = 0                 # belongs_to links made by title match
    duplicates_skipped: int = 0    # idempotent re-run hits (alias/title match)
    skipped: list[SkippedSection] = field(default_factory=list)
```

Imports `ItemStatus`, `Project` from sprints contracts and `TicketState`, `Priority` from tickets contracts — mapping tables must target the real enums, not copies.

---

## 8. `src/planner/chat/contracts.py`

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMessage:
    role: str                      # "user" | "assistant"
    text: str
    created_at: int


@dataclass(frozen=True)
class ChatSendResult:              # what the gateway adapter returns per send
    reply_text: str
    session_key: str               # persisted onto the entity's chat_session_key


@dataclass(frozen=True)
class GatewayStatus:               # availability signal for the panel (§11)
    available: bool
    detail: str | None = None      # e.g. "connection refused" — shown nowhere, logged
```

---

## 9. `src/planner/core/adapters/` — protocols, fakes, real stubs, registry

### 9.1 `base.py` — protocols and their shapes

Stdlib only. Imports `PlanTree`, `PlanNode` from `days/contracts.py` and `ChatSendResult`, `GatewayStatus` from `chat/contracts.py` — safe because contracts are leaf modules; the dependency arrow is core-adapters → domain-contracts, never contracts → core.

```python
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SpawnRequest:                # §7.4 — everything the subprocess boundary needs
    ticket_id: str
    run_id: str
    claim: str
    server_url: str
    log_path: str                  # per-run log file under logs_dir
    hermes_bin: str
    profile: str                   # R3: "default"
    skill: str                     # R3: "planning-worker"


@dataclass(frozen=True)
class SpawnResult:
    ok: bool
    pid: int | None = None         # set when ok
    error: str | None = None       # set when not ok → run status spawn_failed


class SpawnAdapter(Protocol):
    def spawn(self, request: SpawnRequest) -> SpawnResult: ...


@dataclass(frozen=True)
class BoundaryInputs:              # §6.2 deterministic-pass outputs, DB-internal only (R4)
    planning_date: str             # ISO
    carryover: list[dict]          # ticket digests: {id, title, state, priority}
    overdue: list[dict]            # same digest shape, tickets and items
    approvals_digest: list[dict]   # {entity_id, kind, waiting_since}


@dataclass(frozen=True)
class BoundaryJudgment:
    brief_markdown: str
    plan_tree: PlanTree


class BoundaryAdapter(Protocol):
    # Timeout (boundary_timeout_seconds, 60s) is owned by the CALLER — the boundary
    # scheduler wraps calls; adapters just do the work or raise.
    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment: ...
    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree: ...
    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode: ...


class GatewayAdapter(Protocol):
    def status(self) -> GatewayStatus: ...
    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult: ...
```

### 9.2 `fakes.py` — behavior specification

- `FakeSpawnAdapter`: records every `SpawnRequest` in `self.calls: list[SpawnRequest]`; returns results from a scriptable queue (`self.results: list[SpawnResult]`, popped FIFO; default when empty: `SpawnResult(ok=True, pid=<next fake pid starting 90001>)`). Never touches the OS.
- `FakeBoundaryAdapter`: constructor takes optional scripted `judgment_result`, `replan_tree`, `replan_child_node`; defaults produce a deterministic brief (`"# Brief for {planning_date}"`) and a tree with root `focus="Fake focus"` and one child per carryover entry (status `proposed`, positions from 0). A `fail: bool` flag makes every call raise `RuntimeError("fake boundary failure")` for the failure-path tests. Records calls.
- `EchoGatewayAdapter`: `status()` → available. `send()` echoes: reply `"echo: {text}"`; if `session_key is None`, mints `"fake-sess-{n}"` (n increments); else returns the given key. Records calls.
- `OfflineGatewayAdapter`: `status()` → `GatewayStatus(available=False, detail="gateway offline")`; `send()` raises `PlannerError(ErrorCode.gateway_offline, ...)`.

### 9.3 `real.py` — thin stubs

`RealSpawnAdapter(config)`, `RealBoundaryAdapter(config)`, `RealGatewayAdapter(config)` — constructors store config; every method raises `NotImplementedError("wired in stage 4")`. They exist so the registry, config keys, and types are final now; later tickets fill in subprocess/`tui_gateway.ws` logic without touching any signature.

### 9.4 `registry.py` — selection rules

```python
@dataclass(frozen=True)
class Adapters:
    spawn: SpawnAdapter
    boundary: BoundaryAdapter
    gateway: GatewayAdapter

def build_adapters(config: Config) -> Adapters: ...
```

Selection per adapter from config keys `spawn_adapter` / `boundary_adapter` / `gateway_adapter`, each ∈ {`auto`, `real`, `fake`} (+ `offline` for gateway only). Rule: `auto` → `fake` when `config.test_mode` else `real`; explicit values always win (this is how dogfood Level C enables the real spawn adapter under a test-mode-off server, and how e2e item 26 selects `offline`). Unknown value → `PlannerError(ErrorCode.validation, ...)` at startup.

---

## 10. `src/planner/core/` primitives

### 10.1 `ids.py`

```python
ID_PREFIXES: Final = {"sprint": "sp", "sprint_item": "si", "ticket": "t", "idea": "idea", "run": "run"}
SLUG_ALPHABET: Final = "0123456789abcdefghjkmnpqrstuvwxyz"   # lowercase, no i/l/o
SLUG_LEN: Final = 8

def new_id(prefix: str) -> str          # f"{prefix}_{8 random chars}" via secrets.choice
def day_id(planning_date: date) -> str  # f"day_{planning_date.isoformat()}"
def new_claim() -> str                  # f"claim_{12 random chars}" — claim tokens are longer
```

Run ids are `run_`-prefixed slugs from the same generator (SPEC lists entity prefixes but delegates run ids; slugs keep `PLAN_RUN_ID` self-describing). Claim tokens are distinct from run ids so §7.6 validation checks two independent values.

### 10.2 `clock.py`

```python
class Clock(Protocol):
    def now(self) -> datetime: ...      # timezone-aware, LOCAL time (§6.1 math is local)
    def now_unix(self) -> int: ...

class RealClock:                        # datetime.now().astimezone()

class TestClock:                        # mutable, for PLAN_FAKE_NOW + POST /api/test/set-now (D5)
    def __init__(self, start: datetime) -> None
    def set(self, now: datetime) -> None
```

`PLAN_FAKE_NOW` is parsed as ISO; a naive value is interpreted as local time (`.astimezone()` after attach). The clock choice is made once at startup: `TestClock` iff `config.test_mode and config.fake_now`, else `RealClock`. `PLAN_FAKE_NOW` never reaches the clock when test mode is off because config discards it (see 10.4) — that is where acceptance item 21's "ignored" is enforced.

### 10.3 `events.py`

The only module that writes or reads the events table:

```python
def append_event(conn, entity_id: str, kind: EventKind, payload: JsonDict, created_at: int) -> int
def read_events_since(conn, since_id: int, limit: int = 500) -> list[EventRow]   # for the WS tailer

@dataclass(frozen=True)
class EventRow:
    id: int; entity_id: str; kind: str; payload: JsonDict; created_at: int
```

No update/delete functions exist anywhere (§3 append-only). `EventRow` lives here (it is a core infra shape, not a domain one).

### 10.4 `config.py`

```python
@dataclass(frozen=True)
class Config:
    # §13 named keys              YAML key                   env override                 default
    db_path: str                  # db_path                  PLAN_DB_PATH                 "data/planning.db"
    port: int                     # port                     PLAN_PORT                    8767
    boundary_hour: int            # boundary_hour            PLAN_BOUNDARY_HOUR           5      (R1)
    tick_seconds: int             # tick_seconds             PLAN_TICK_SECONDS            60
    claim_ttl_seconds: int        # claim_ttl_seconds        PLAN_CLAIM_TTL_SECONDS       900
    max_runs: int                 # max_runs                 PLAN_MAX_RUNS                2      (R7)
    failure_limit: int            # failure_limit            PLAN_FAILURE_LIMIT           2
    dispatch_enabled: bool        # dispatch_enabled         PLAN_DISPATCH_ENABLED        True
    hermes_bin: str               # hermes_bin               PLAN_HERMES_BIN              "hermes"
    hermes_profile: str           # hermes_profile           PLAN_HERMES_PROFILE          "default"   (R3)
    worker_skill: str             # worker_skill             PLAN_WORKER_SKILL            "planning-worker" (R3)
    # other tunables named in SPEC (same PLAN_<UPPERCASED KEY> convention)
    ws_poll_ms: int               # ws_poll_ms               PLAN_WS_POLL_MS              300    (§9)
    ui_debounce_ms: int           # ui_debounce_ms           PLAN_UI_DEBOUNCE_MS          250    (§9; served to the UI via GET /api/meta)
    run_max_seconds: int          # run_max_seconds          PLAN_RUN_MAX_SECONDS         1800   (§7.3)
    boundary_timeout_seconds: int # boundary_timeout_seconds PLAN_BOUNDARY_TIMEOUT_SECONDS 60    (§6.2)
    title_max_chars: int          # title_max_chars          PLAN_TITLE_MAX_CHARS         200    (§3.3)
    dispatcher_lock_path: str     # dispatcher_lock_path     PLAN_DISPATCHER_LOCK_PATH    "data/dispatcher.lock" (§7.1)
    logs_dir: str                 # logs_dir                 PLAN_LOGS_DIR                "data/logs" (§7.4)
    # adapter selection (registry §9.4)
    spawn_adapter: str            # spawn_adapter            PLAN_SPAWN_ADAPTER           "auto"
    boundary_adapter: str         # boundary_adapter         PLAN_BOUNDARY_ADAPTER        "auto"
    gateway_adapter: str          # gateway_adapter          PLAN_GATEWAY_ADAPTER         "auto"
    # test mode — ENV ONLY, never in config.yaml
    test_mode: bool               # —                        PLAN_TEST_MODE ("1")         False
    fake_now: str | None          # —                        PLAN_FAKE_NOW (ISO)          None; forced to None unless test_mode
```

Loading: `load_config(path: str | None = None, env: Mapping[str, str] | None = None) -> Config` — defaults ← `config.yaml` (missing file = defaults, missing keys = defaults) ← env. Bool parsing accepts `1/true/yes/on` / `0/false/no/off` case-insensitively; anything else → `PlannerError(ErrorCode.validation)`. `fake_now` is set to `None` whenever `test_mode` is false — the single enforcement point for item 21.

Fail-safe dispatch flag (§7.1): a separate function, `read_dispatch_enabled(path: str | None, env) -> bool`, re-reads YAML+env fresh and returns `False` on ANY exception (missing file is not an error — defaults apply; a malformed file or unparseable value returns `False`). The dispatcher calls this every tick instead of trusting the startup `Config`.

The server bind host is a module constant `HOST: Final = "127.0.0.1"` (§2 says the bind is fixed, not tunable).

`config.yaml` (checked in) lists every YAML key above with its default and a one-line comment; test-mode keys are absent by design.

### 10.5 `errors.py` — structured errors

```python
class ErrorCode(StrEnum):
    frozen_write = "frozen_write"                  # §5 kickoff/review writes after freeze
    at_cap_stop = "at_cap_stop"                    # §4.3 agent proposal at ceiling with stop
    grant_missing = "grant_missing"                # §4.4.7 accept without the full pair
    grant_invalid = "grant_invalid"                # next_ceiling before the newly entered state / unknown value
    stale_claim = "stale_claim"                    # §7.6 stale/foreign claim; detail names the mismatch
    recap_too_early = "recap_too_early"            # §3.3 recap write at needs_success
    title_too_long = "title_too_long"              # §3.3 > title_max_chars
    sprint_overlap = "sprint_overlap"              # §3.1 overlapping date ranges
    link_cycle = "link_cycle"                      # §3.6 blocks/parent_child transitive cycle
    link_invalid = "link_invalid"                  # self-link, second belongs_to, bad endpoints
    sprint_derived = "sprint_derived"              # §3.3 sprint_id write on a parented ticket
    item_transition_forbidden = "item_transition_forbidden"  # §3.2 agent direct done/deferred
    agent_forbidden = "agent_forbidden"            # claim/actor-agent request hitting a human-only action
    gateway_offline = "gateway_offline"            # §11
    db_not_empty = "db_not_empty"                  # §12 seed --demo on a non-empty DB
    not_found = "not_found"
    validation = "validation"                      # generic input validation


class PlannerError(Exception):
    def __init__(self, code: ErrorCode, message: str, detail: JsonDict | None = None): ...
    def to_payload(self) -> JsonDict:   # {"error": {"code": ..., "message": ..., "detail": {...}}}
```

Wire shape everywhere (API bodies and CLI `--json` stderr): `{"error": {"code": str, "message": str, "detail": object}}` — `detail` is `{}` when absent, never omitted.

`stale_claim` detail must name the mismatch (§7.6): `{"reason": "expired"|"foreign"|"none_active", "presented_run_id": ..., "presented_claim": ..., "active_claim_present": bool}` — never echoing the real claim token back.

HTTP status mapping (one handler in `core/server.py`): `not_found` → 404, `stale_claim` → 409, `gateway_offline` → 503, everything else → 400. Any endpoint hit in `/api/test/*` outside test mode → plain 404 (routes not mounted; no PlannerError involved). CLI exit codes: any `{"error": ...}` response → 1; connection failure to the server → 2; success → 0.

---

## 11. `src/planner/core/db.py` — complete DDL

`connect(db_path)` opens sqlite3 with `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`, `row_factory=sqlite3.Row`, `isolation_level=None` (explicit transactions in writer functions later). `create_schema(conn)` executes the DDL below idempotently (`CREATE TABLE IF NOT EXISTS`), then sets `PRAGMA user_version=1`. Booleans are INTEGER 0/1; JSON is TEXT; all times INTEGER unix seconds; dates TEXT ISO.

```sql
CREATE TABLE IF NOT EXISTS sprints (
  id                  TEXT PRIMARY KEY,              -- sp_<slug>
  name                TEXT NOT NULL,
  date_start          TEXT NOT NULL,                 -- ISO date, inclusive
  date_end            TEXT NOT NULL,                 -- ISO date, inclusive
  limiting_factor     TEXT NOT NULL DEFAULT '',
  primary_bet         TEXT NOT NULL DEFAULT '',
  supports            TEXT NOT NULL DEFAULT '',
  premortem           TEXT NOT NULL DEFAULT '',
  weekly_addenda      TEXT NOT NULL DEFAULT '[]',    -- JSON list[Addendum], append-only
  kickoff_frozen_at   INTEGER,
  outcomes            TEXT NOT NULL DEFAULT '',
  solo_reflection     TEXT NOT NULL DEFAULT '',
  joint_discussion    TEXT NOT NULL DEFAULT '',
  updates_to_thinking TEXT NOT NULL DEFAULT '',
  carry_forward       TEXT NOT NULL DEFAULT '',
  review_frozen_at    INTEGER,
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  CHECK (date_start <= date_end)
);

CREATE TABLE IF NOT EXISTS sprint_items (
  id                  TEXT PRIMARY KEY,              -- si_<slug>
  title               TEXT NOT NULL,
  body                TEXT NOT NULL DEFAULT '',
  status              TEXT NOT NULL DEFAULT 'todo'
                      CHECK (status IN ('todo','active','done','blocked','deferred_next_sprint')),
  priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline            TEXT,
  project             TEXT NOT NULL CHECK (project IN ('Vylo','Tribe','Learning','Other')),
  current_state_note  TEXT NOT NULL DEFAULT '',
  sprint_id           TEXT REFERENCES sprints(id),   -- NULL = backlog/deferred
  blocked_by          TEXT NOT NULL DEFAULT '[]',    -- JSON list[str] of ticket ids
  status_proposal     TEXT,                          -- JSON ItemStatusProposal | NULL
  created_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
  id                   TEXT PRIMARY KEY,             -- t_<slug>
  title                TEXT NOT NULL CHECK (length(title) <= 200),
  state                TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (state IN ('needs_success','needs_approach','needs_plan',
                                        'in_progress','needs_review','done','dropped')),
  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
  deadline             TEXT,
  project              TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- NULL when parented
  sprint_item_id       TEXT REFERENCES sprint_items(id),
  sprint_id            TEXT REFERENCES sprints(id),  -- writable only when sprint_item_id IS NULL
  recap                TEXT NOT NULL DEFAULT '',
  ceiling              TEXT NOT NULL DEFAULT 'needs_success'
                       CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
                                          'in_progress','needs_review','done')),
  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
  auto_blocked         INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,   -- §7.5
  chat_session_key     TEXT,
  alias                TEXT,                         -- migration "Ticket ID:" (§12)
  fields               TEXT NOT NULL DEFAULT '{"success":{"value":null,"proposal":null,"notes":null},"approach":{"value":null,"proposal":null,"notes":null},"plan":{"value":null,"proposal":null,"notes":null},"result":{"value":null,"proposal":null,"notes":null}}',
  claim_lock           TEXT,                         -- §7.3 claim token; NULL = unclaimed
  claim_expires        INTEGER,
  created_at           INTEGER NOT NULL,
  updated_at           INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state);

-- Note: ceiling CHECK excludes 'dropped' (a ceiling is "highest state advancement may
-- move INTO"; dropped is not an advancement target). The title CHECK carries the literal
-- 200 because DDL cannot read config; the config value title_max_chars is the one the
-- write paths enforce, and both are asserted equal at startup (create_schema asserts
-- config.title_max_chars == 200 or fails loudly — a deliberate guard, not dead config).

CREATE TABLE IF NOT EXISTS days (
  id               TEXT PRIMARY KEY,                 -- day_YYYY-MM-DD (planning date, §3.4)
  brief            TEXT NOT NULL DEFAULT '',
  notes            TEXT NOT NULL DEFAULT '',
  plan             TEXT,                             -- JSON PlanTree | NULL
  chat_session_key TEXT,
  created_at       INTEGER NOT NULL,
  updated_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS day_tickets (
  day_id    TEXT NOT NULL REFERENCES days(id),
  ticket_id TEXT NOT NULL REFERENCES tickets(id),
  position  INTEGER NOT NULL CHECK (position >= 0),  -- contiguous from 0, writer-enforced
  PRIMARY KEY (day_id, ticket_id)
);
-- No UNIQUE(day_id, position): re-packing after removal would trip immediate
-- uniqueness mid-update. Contiguity is the canonical writer's invariant, unit-tested (item 12).

CREATE TABLE IF NOT EXISTS ideas (
  id         TEXT PRIMARY KEY,                       -- idea_<slug>
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  project    TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- nullable (§3.5)
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS links (                   -- §3.6, exactly the spec's three columns
  from_id TEXT NOT NULL,
  to_id   TEXT NOT NULL,
  kind    TEXT NOT NULL CHECK (kind IN ('belongs_to','parent_child','blocks','relates')),
  PRIMARY KEY (from_id, to_id, kind),
  CHECK (from_id <> to_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_links_one_belongs_to
  ON links(from_id) WHERE kind = 'belongs_to';       -- at most one belongs_to per ticket
CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id, kind);
-- Cycle rejection (transitive) is write-path logic, not DDL.

CREATE TABLE IF NOT EXISTS events (                  -- §3, append-only
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id  TEXT NOT NULL,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',             -- JSON
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id, id);

CREATE TABLE IF NOT EXISTS runs (                    -- §7.3
  id         TEXT PRIMARY KEY,                       -- run_<slug>
  ticket_id  TEXT NOT NULL REFERENCES tickets(id),
  status     TEXT NOT NULL
             CHECK (status IN ('running','done','blocked','crashed','timed_out','reclaimed','spawn_failed')),
  started_at INTEGER NOT NULL,
  ended_at   INTEGER,
  summary    TEXT,
  error      TEXT,
  pid        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_ticket ON runs(ticket_id, started_at);

CREATE TABLE IF NOT EXISTS boundary_runs (           -- §6.2: never twice per date
  planning_date TEXT PRIMARY KEY,                    -- ISO date
  ran_at        INTEGER NOT NULL,
  judgment      TEXT NOT NULL CHECK (judgment IN ('ok','skipped','failed'))
);
-- judgment records the judgment-pass outcome (ok / skipped per §6.2 prior-planning
-- rule / failed per adapter failure) — needed as evidence and for DOGFOOD, and it is
-- completion metadata, not derived view state.
```

Runs carry no `created_at`/`updated_at`: §3's "all entities" covers the five id-prefixed entities; §7.3 defines the runs row exhaustively and `started_at` is its creation time. `blockers_cleared` (§3.2) and blocked-ness (§3.6) are computed on read — no columns. DDL column names match the contract dataclasses field-for-field (verified by the T01 acceptance smoke: `PRAGMA table_info` names ⊇ dataclass field names for each table/dataclass pair).

---

## 12. `src/planner/core/server.py` — app factory shell

```python
def create_app(config: Config, clock: Clock, adapters: Adapters, conn_factory: Callable[[], sqlite3.Connection]) -> FastAPI
```

- Registers the `PlannerError` → JSON handler (status mapping from §10.5).
- Includes each domain router under `/api` (tickets, sprints, days, dispatch, seed, chat).
- Serves `assets/` at `/assets` (StaticFiles) and `GET /` → the app shell page (stub returning minimal HTML in T01).
- `GET /api/meta` → `{ "ui_debounce_ms": ..., "ws_poll_ms": ..., "test_mode": ... }` — the frontend's only source of tunables.
- `WS /api/events?since=<id>` — accepts the connection and closes immediately in T01 (tailer lands in stage 4); the route path and query param are the contract.
- Test router mounted **only** when `config.test_mode`: `POST /api/test/tick-boundary`, `POST /api/test/tick-dispatcher`, `POST /api/test/set-now {"now": iso}` (D5) — all `NotImplementedError` stubs in T01. Not mounted → natural 404 outside test mode.

Uvicorn launch lives in the CLI's `plan serve` handler (host `127.0.0.1`, port from config), stubbed to the extent that it builds config/clock/adapters/app and calls `uvicorn.run` — this one handler is allowed to work in T01 since it is pure wiring (acceptance only requires `plan --help`).

---

## 13. Complete API route table (§9) — stubs in T01, contract forever

All routes below raise `NotImplementedError` in T01 except where noted. Agent-context writes carry headers `X-Plan-Run-Id`, `X-Plan-Claim`, `X-Plan-Actor` (the CLI fills them from `PLAN_RUN_ID`/`PLAN_CLAIM`/`PLAN_ACTOR`); §7.6 validation happens server-side in stage 4. Human-only actions are marked (H) — they reject claim-carrying requests with `agent_forbidden`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/tickets` | create ticket (title, priority?, deadline?, project?, sprint_id?, sprint_item_id?) |
| GET | `/api/tickets` | list, filters `state`, `project`, `sprint_id`, `sprint_item_id` |
| GET | `/api/tickets/{id}` | full ticket: fields, links, day/sprint assignment, run summary, derived blocked flag |
| PATCH | `/api/tickets/{id}` | plain field update: title, priority, deadline, project, sprint_id (rules §3.3) |
| POST | `/api/tickets/{id}/propose/{field}` | file/replace proposal on a field (agent path; resolution engine) |
| POST | `/api/tickets/{id}/accept/{field}` | (H) accept or edit-accept; body `{edited_body?, next_ceiling, at_cap}` — grant pair mandatory |
| POST | `/api/tickets/{id}/approve` | (H) `needs_review` → `done`; no grant pair (§4.4.7) |
| PUT | `/api/tickets/{id}/notes/{field}` | write a field's `notes` slot (human or agent, any time) |
| PUT | `/api/tickets/{id}/recap` | write recap (rejected before `needs_approach`) |
| POST | `/api/tickets/{id}/grant` | (H) change `{ceiling, at_cap}` outside an accept |
| POST | `/api/tickets/{id}/state` | (H) jump to any state `{to}` (skipped gating values stay null) |
| POST | `/api/tickets/{id}/drop` | (H) → `dropped` |
| POST | `/api/tickets/{id}/unblock` | (H) clear `auto_blocked` + reset `consecutive_failures` (§7.5) |
| GET | `/api/tickets/{id}/events` | event log for the Ticket screen |
| GET | `/api/tickets/{id}/runs` | run history |
| GET | `/api/tickets/{id}/copy-text` | plain-text block for the Copy-ticket button (§10 UI) |
| POST | `/api/items` | create sprint item |
| GET | `/api/items` | list, filters `status`, `project`, `sprint_id` (`sprint_id=null` = backlog) |
| GET | `/api/items/{id}` | item + ticket rollup + `blockers_cleared` (derived) |
| PATCH | `/api/items/{id}` | field updates + agent-permitted status writes (todo↔active, blocked+blocked_by) |
| POST | `/api/items/{id}/propose-status` | agent proposal for done/deferred_next_sprint |
| POST | `/api/items/{id}/accept-status` | (H) accept pending status proposal; no onward grant |
| POST | `/api/sprints` | (H) create sprint (rejects overlap) |
| GET | `/api/sprints` | list |
| GET | `/api/sprints/{id}` | sprint detail |
| PATCH | `/api/sprints/{id}` | edit kickoff/review fields pre-freeze, name/dates |
| POST | `/api/sprints/{id}/freeze-kickoff` | (H) set `kickoff_frozen_at` |
| POST | `/api/sprints/{id}/freeze-review` | (H) set `review_frozen_at` |
| POST | `/api/sprints/{id}/addenda` | append `{date, text}` to `weekly_addenda` (allowed post-freeze) |
| GET | `/api/sprint/current` | §5 view: items grouped by status + rollups + loose tickets |
| GET | `/api/day/{date}` | day view (materializes on read, §3.4): brief, notes, plan, ordered tickets |
| PATCH | `/api/day/{date}` | edit notes / brief |
| POST | `/api/day/{date}/tickets` | add ticket at end `{ticket_id}` |
| DELETE | `/api/day/{date}/tickets/{ticket_id}` | remove = defer (§3.4; positions re-pack) |
| POST | `/api/day/{date}/plan/accept` | (H) accept one node `{node: "root" | position}` |
| POST | `/api/day/{date}/plan/accept-all` | (H) accept root + children; add child tickets to day list |
| POST | `/api/day/{date}/plan/invalidate` | (H) invalidate `{node}`; root → full replan, child → node replan (R5) |
| POST | `/api/day/{date}/plan/reject-all` | (H) clear plan to null |
| POST | `/api/ideas` | create idea |
| GET | `/api/ideas` | list |
| POST | `/api/links` | add link `{from_id, to_id, kind}` (cycle/self/belongs_to rules) |
| DELETE | `/api/links` | remove link (same three fields as query params) |
| POST | `/api/runs/{id}/heartbeat` | extend claim by one TTL (claim headers required) |
| POST | `/api/runs/{id}/close` | close run `{outcome, summary}` (claim headers required) |
| GET | `/api/board` | §9: columns per state, `dropped` hidden; card fields per §10.3 |
| GET | `/api/queues` | all three derived views: `{approvals: [...], pickup: [...], overdue: [...]}` (§4.5) |
| POST | `/api/chat/{entity_id}/send` | `{text}` → `{reply_text, session_key}`; persists session key |
| GET | `/api/chat/{entity_id}/status` | `{available}` — gateway availability for the panel |
| POST | `/api/seed` | `{source_dir}` or `{demo: true}` → MigrationReport JSON |
| GET | `/api/meta` | UI tunables + test_mode flag (works in T01) |
| WS | `/api/events?since=<id>` | event tail, `{events: [...], cursor}` batches (§9) |
| POST | `/api/test/tick-boundary` | test mode only (D6) |
| POST | `/api/test/tick-dispatcher` | test mode only (D6) |
| POST | `/api/test/set-now` | test mode only (D5) `{now: iso}` |

Route ownership: ticket routes plus `/api/board` and `/api/queues` → `tickets/api.py` (board and queues are ticket-centric derived views; the approvals queue pulls item proposals via sprints read helpers in stage 4). Item and sprint routes (+`/api/sprint/current`) → `sprints/api.py`. Day routes → `days/api.py`. Run routes → `dispatch/api.py`. Chat → `chat/api.py`. Seed → `seed/api.py`. Links → `tickets/api.py` (links are ticket-anchored; every kind has a ticket source). Meta/WS/test → `core/server.py`.

---

## 14. CLI — `src/planner/cli/main.py` (§8 exact)

Click group tree; every verb supports `--json`. All handlers except `serve` are HTTP calls via `httpx` (pinned in requirements) to `PLAN_SERVER_URL` (default `http://127.0.0.1:8767`) — stubbed in T01 as `raise NotImplementedError` after argument parsing, so `--help` renders the full tree and exits 0.

```
plan
├── serve                                  # runs uvicorn foreground (wiring works in T01)
├── seed  (--source DIR | --demo) [--json]
├── ticket
│   ├── create  --title TEXT [--priority P0..P3] [--deadline DATE] [--project NAME]
│   │           [--sprint ID] [--item ID]
│   ├── show    [TICKET_ID]                # id defaults to $PLAN_TICKET_ID everywhere below
│   ├── list    [--state S] [--project P] [--sprint ID]
│   └── set     [TICKET_ID] [--priority P] [--deadline DATE|none] [--day DATE|today]
│               [--sprint ID|none]         # §8: priority/deadline/day/sprint ONLY —
│                                          # no ceiling/at_cap flags exist
├── propose FIELD [TICKET_ID]  (body: stdin or --body-file PATH; `-` = stdin)
├── recap        [TICKET_ID]   (same body rules)
├── note  FIELD  [TICKET_ID]   (same body rules)
├── item
│   ├── create  --title TEXT --project NAME [--priority P] [--deadline DATE] [--sprint ID]
│   ├── show    ITEM_ID
│   ├── list    [--status S] [--project P] [--backlog]
│   ├── set     ITEM_ID [--status todo|active|blocked] [--blocked-by t_x,t_y] [--note TEXT]
│   │                                       # §3.2 agent-permitted transitions only
│   └── propose-status ITEM_ID --to done|deferred_next_sprint  (optional body note via stdin/--body-file)
├── sprint
│   └── show    [--json]                   # current sprint view
├── idea
│   ├── create  --title TEXT [--project NAME]  (body via stdin/--body-file, optional)
│   └── list
├── day
│   ├── show          [DATE]               # default: current planning date
│   ├── add-ticket    TICKET_ID [DATE]
│   └── remove-ticket TICKET_ID [DATE]
├── link
│   ├── add  FROM TO --kind belongs_to|parent_child|blocks|relates
│   └── rm   FROM TO --kind KIND
├── run
│   ├── heartbeat                          # run id from $PLAN_RUN_ID, claim from $PLAN_CLAIM
│   └── close --outcome done|blocked --summary -   (summary via stdin/--body-file)
└── queue
    ├── approvals
    ├── pickup
    └── overdue
```

Conventions (module-level helpers in `cli/main.py`):
- Body text NEVER as an inline argument (§8): `read_body(body_file: str | None) -> str` — `-` or absent-with-piped-stdin reads stdin; `--body-file PATH` reads the file; empty body → exit 1 validation error.
- Env defaults: `PLAN_SERVER_URL`, `PLAN_TICKET_ID` (used when a ticket-taking verb omits the id), `PLAN_RUN_ID`, `PLAN_CLAIM`, `PLAN_ACTOR` (default `"agent"`; sent as `X-Plan-Actor`).
- Exit codes: 0 success; 1 any structured `{"error": ...}` (validation/domain); 2 connection error (server unreachable). `--json` prints the raw response JSON to stdout (errors to stderr, still JSON); without `--json`, a terse human line (agents use `--json`; the human never uses the CLI at all).
- No resolution verbs exist: no accept, approve, grant, unblock, or day-plan subcommands anywhere in the tree (§8).
- `python -m planner` == `plan` (via `__main__.py`).

---

## 15. `pyproject.toml` sketch

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "planner"
version = "2.0.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi", "uvicorn", "click", "httpx", "pydantic", "PyYAML",
]   # versions pinned by requirements.txt (the venv's source of truth)

[project.scripts]
plan = "planner.cli.main:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
planner = ["py.typed"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]

[tool.mypy]
strict = true
files = ["src"]
mypy_path = "src"
python_version = "3.12"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

`config.yaml` at repo root: every key from §10.4's table (except the two env-only test keys), value = default, one comment each. It is the checked-in defaults file, not an example.

---

## 16. What T01 does NOT implement

Shapes and surfaces only. Explicitly out:
- **No resolution logic**: no proposal filing/acceptance behavior, no state advancement, no grant handling — `GATING_FIELD`/`ADVANCE_TARGET`/`GrantPair` are data, the engine is stage 3.
- **No dispatcher behavior**: no tick, no eligibility/ordering evaluation, no claim CAS, no reclaim, no circuit breaker, no file lock — only the `Run` shapes, DDL columns, and adapter protocols.
- **No boundary job**: no planning-date function body (signature/alias only), no deterministic pass, no judgment invocation, no scheduler.
- **No seed parsing/importing**: mapping tables and parsed/report shapes only.
- **No WS tailing, no test-endpoint behavior, no chat sending** (fakes exist as classes but nothing calls them in the server).
- **No UI**: `assets/` stays empty (`.gitkeep`); `GET /` returns a placeholder shell.
- **No tests beyond T01's own acceptance smoke** (the verify instrument is T02): T01 ships zero files in `tests/` beyond `.gitkeep`s.
- API handlers and CLI handlers raise `NotImplementedError`; only `load_config`, `create_schema`/`connect`, `ids`, `clock`, `append_event`/`read_events_since`, error types, fakes' in-memory behavior, registry selection, `GET /api/meta`, and `plan serve` wiring actually execute.

## 17. Acceptance (ticket's integration bar) — how the implementer self-checks

1. `pip install -e .` into `.venv` succeeds; `plan --help` exits 0 and shows the full §14 tree.
2. `ruff check .` clean; `mypy` (strict, src) clean; `python -c "import planner"` + import of every submodule succeeds (`python -m compileall src/`).
3. Temp-DB smoke: `connect` + `create_schema` on a temp path creates all ten tables; `PRAGMA table_info` column names ⊇ contract dataclass field names for sprints/sprint_items/tickets/days/ideas/runs (script-level check run by hand, not a committed test — committed tests start at T02 with the instrument).
4. No pydantic/FastAPI import in any `contracts.py`, `db.py`, `clock.py`, `ids.py`, `events.py`, `errors.py`, `config.py`, `adapters/*` (grep-verifiable).

Decisions made here that go to decisions.md at integration: run ids as `run_` slugs with separate `claim_` tokens; `fields`/`blocked_by`/`weekly_addenda`/`plan`/`status_proposal` as JSON TEXT columns named after their contract fields; `boundary_runs.judgment` outcome column; adapter selection keys `auto|real|fake(|offline)`; Urgency fallback mapping; `Priority` homed in tickets contracts and imported by sprints/dispatch; `GET /api/meta` as the UI-tunable channel; board/queues routes homed in tickets/api.




---

## 18. Amendments after codex review (binding over the body above)

Per orchestration/tickets/T01-contracts/plan-review.md:

1. `ceiling` and `next_ceiling` values are restricted to members of `STATE_ORDER` (never `dropped`); write paths validate against it; contract comments state it. Typing stays `TicketState` for simplicity; the DDL CHECK and validators carry the restriction.
2. `Project` and `Priority` are declared in `core/contracts.py` (cross-domain vocabulary beside `LinkKind`); `tickets/`, `sprints/`, `dispatch/`, `seed/` contracts import them from core. `Ticket.project: Project | None`; `SprintItem.priority: Priority`.
3. `Idea` dataclass (id, title, body, project: Project | None, created_at, updated_at) lives in `sprints/contracts.py`.
4. `EventRow` is declared in `core/contracts.py`; `core/events.py` imports it.
5. `Link` dataclass (from_id, to_id, kind: LinkKind) is declared in `core/contracts.py`.
6. CLI: `item set` has NO `--note` flag (transitions only). Body input requires explicit `-` (stdin) or `--body-file PATH`; there is no implicit-stdin mode.
7. New config keys, same conventions: `events_read_limit` = 500 (PLAN_EVENTS_READ_LIMIT), `db_busy_timeout_ms` = 5000 (PLAN_DB_BUSY_TIMEOUT_MS). `read_events_since` takes its default from config at the call site (core/events.py stays config-free: the limit is always passed in).
8. `assets/tokens.css` ships in T01: one `:root` block with every category from PRINCIPLES — surfaces, text tokens, accent (bright/surface/text), radius scale, motion durations (fast/base/slow), spacing scale, border widths — starter values, tuned in stage 5. `node --check` does not apply to CSS; no JS ships in T01.
9. decisions.md D4 amended (click + httpx allowed; layering is the rule).
10. `GET /api/day/{date}` and the day CLI verbs accept the literal `today` (server resolves via planning date).
11. The title-max/config assertion runs at server startup (app factory), not inside `create_schema`.
