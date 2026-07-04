# T01 impl-review — codex exec run by integration-check agent on 2026-07-04 11:49 BST

Reading additional input from stdin...
OpenAI Codex v0.142.5
--------
workdir: /Users/khushaljagota/.hermes/planning-v2
model: gpt-5.5
provider: openai
approval: never
sandbox: read-only
reasoning effort: xhigh
reasoning summaries: none
session id: 019f2cbf-52bd-7c01-80b5-d9d257239b19
--------
user
You are auditing freshly written code against its reviewed blueprint and spec. Read orchestration/tickets/T01-contracts/ticket.md, then orchestration/tickets/T01-contracts/plan.md (its section 18 amendments are binding). Then inspect the actual source: pyproject.toml, config.yaml, assets/tokens.css, and everything under src/planner/. Report CONCRETE deviations: shapes that differ from the plan, missing DDL columns or enum members, contracts importing pydantic/fastapi, CLI verbs missing or extra vs plan section 14, API routes missing vs plan section 13, config keys missing vs plan section 10.4 + amendment 7, amendments 1-11 not honored. Number findings with file paths. If faithful: 'NO VIOLATIONS' plus up to 3 sharpening suggestions.
deprecated: `[features].web_search_request` is deprecated because web search is enabled by default.
Set `web_search` to `"live"`, `"indexed"`, `"cached"`, or `"disabled"` at the top level (or under a profile) in config.toml if you want to override it.
warning: Skill descriptions were shortened to fit the 2% skills context budget. Codex can still see every skill, but some descriptions are shorter. Disable unused skills or plugins to leave more room for the rest.
codex
I’ll read the ticket and blueprint first, then compare the declared contracts, config, API/CLI surfaces, and planner source against the binding amendments.
exec
/bin/zsh -lc 'rg --files' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
src/planner/__main__.py
src/planner/days/contracts.py
src/planner/days/api.py
src/planner/days/__init__.py
src/planner/py.typed
src/planner/cli/main.py
src/planner/cli/__init__.py
src/planner/__init__.py
src/planner/dispatch/contracts.py
src/planner/dispatch/api.py
src/planner/dispatch/__init__.py
src/planner/sprints/contracts.py
src/planner/sprints/api.py
src/planner/sprints/__init__.py
src/planner/chat/contracts.py
src/planner/chat/api.py
src/planner/chat/__init__.py
src/planner/core/ids.py
src/planner/core/errors.py
src/planner/core/contracts.py
src/planner/core/adapters/base.py
src/planner/core/adapters/fakes.py
src/planner/core/adapters/real.py
src/planner/core/adapters/__init__.py
src/planner/core/adapters/registry.py
src/planner/core/__init__.py
src/planner/core/clock.py
src/planner/core/events.py
src/planner/core/config.py
src/planner/core/server.py
src/planner/core/db.py
src/planner/seed/contracts.py
src/planner/seed/api.py
src/planner/seed/__init__.py
src/planner/tickets/contracts.py
src/planner/tickets/api.py
src/planner/tickets/__init__.py
decisions.md
CLAUDE.md
migration/source-snapshot/sprints/current/sprint-kickoff.md
migration/source-snapshot/sprints/current/daily/2026-07-03/workspace.md
migration/source-snapshot/sprints/current/daily/2026-07-03/overview.md
migration/source-snapshot/sprints/current/daily/2026-07-03/tracker.md
migration/source-snapshot/sprints/current/daily/2026-07-02/workspace.md
migration/source-snapshot/sprints/current/daily/2026-07-02/overview.md
migration/source-snapshot/sprints/current/daily/2026-07-02/tracker.md
migration/source-snapshot/sprints/current/daily/2026-07-01/workspace.md
migration/source-snapshot/sprints/current/daily/2026-07-01/overview.md
migration/source-snapshot/sprints/current/daily/2026-07-01/tracker.md
migration/source-snapshot/sprints/current/sprint-review.md
migration/source-snapshot/sprints/current/sprint-tracking.md
migration/source-snapshot/deferred.md
migration/source-snapshot/ideas.md
assets/tokens.css
harness-prep.md
SPEC.md
orchestration/orchestrator-playbook.md
orchestration/tickets/T08-instrument/ticket.md
orchestration/tickets/T07-seed/dispatch.md
orchestration/tickets/T07-seed/ticket.md
orchestration/tickets/T03-days/dispatch.md
orchestration/tickets/T03-days/ticket.md
orchestration/tickets/T05-dispatch/dispatch.md
orchestration/tickets/T05-dispatch/ticket.md
orchestration/tickets/T02-verify/impl-review.md
orchestration/tickets/T02-verify/ticket.md
orchestration/tickets/T02-verify/plan-review.md
orchestration/tickets/T01-contracts/impl-review.md
orchestration/tickets/T01-contracts/plan.md
orchestration/tickets/T01-contracts/ticket.md
orchestration/tickets/T01-contracts/plan-review.md
orchestration/tickets/T06-sprints/dispatch.md
orchestration/tickets/T06-sprints/ticket.md
orchestration/tickets/T04-tickets-engine/dispatch.md
orchestration/tickets/T04-tickets-engine/ticket.md
scripts/verify.py
scripts/verify_lib.py
config.yaml
PROGRESS.md
tests/unit/conftest.py
pyproject.toml
GOAL-CONDITION.md
verify
requirements.txt
PRINCIPLES.md
codex-audit.md

codex
I’ve got the file map. Next I’m extracting the required surfaces from the T01 ticket and plan before looking at implementation details, so the comparison is anchored to the reviewed blueprint.
exec
/bin/zsh -lc "sed -n '1,240p' orchestration/tickets/T01-contracts/ticket.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
# T01 — Contracts skeleton (SPEC stage 1)

## Scope

Create the full contracts skeleton for the planner: schema DDL, enums, typed models, adapter interfaces, config loading, and CLI/API surface stubs. No behavior beyond config loading, DB schema creation, and id/clock/event primitives. This ticket produces the files every later ticket implements against.

Authoritative inputs: SPEC.md §2 (layout, stack), §3 (data model), §4.1–4.3 (states, fields shape, ceiling/at_cap), §6.1/6.3 (planning date signature, plan-tree shape), §7 (runs, claims, breaker fields), §8 (CLI verb surface), §9 (API surface), §13 (config and test mode), §14 (structural rules), §16 (rulings). PRINCIPLES.md binds throughout.

## Deliverables

1. `pyproject.toml` — package `planner` under `src/`, console script `plan = planner.cli.main:main`, ruff + mypy(strict on src/) + pytest configuration. `pip install -e .` into `.venv` must succeed.
2. `config.yaml` at repo root — every §13 default plus every other tunable named anywhere in SPEC (WS poll 300ms, UI debounce 250ms, run max runtime 30m, boundary adapter timeout 60s, title max 200, spawn profile/skill per R3, boundary hour per R1, max runs per R7, failure limit 2, claim TTL 900s).
3. `src/planner/core/` — `config.py` (dataclass, YAML + `PLAN_*` env overrides, test-mode flags; fail-safe read semantics for dispatch_enabled), `clock.py` (Clock protocol; real; test clock honoring `PLAN_FAKE_NOW` only when `PLAN_TEST_MODE=1`, mutable for the set-now test endpoint), `ids.py` (prefixed slugs `sp_ si_ t_ idea_`, `day_YYYY-MM-DD`), `db.py` (WAL connect + full DDL: sprints, sprint_items, tickets, days, day_tickets, ideas, links, events, runs, boundary_runs — fields exactly per SPEC §3/§7.3/§6.2; `chat_session_key` on both tickets and days; tickets carry `alias`, `claim_lock`, `claim_expires`, `consecutive_failures`; events append-only AUTOINCREMENT), `events.py` (append_event; no update/delete path), `errors.py` (PlannerError with `code`, `message`, `detail`; JSON shape `{"error": {code, message, detail}}`; error-code constants), `contracts.py` (EventKind constants incl. spec-named `state_changed`, `proposal_accepted`, `proposal_superseded`, `day_ticket_removed`, `day_closed`, `auto_blocked`; LinkKind enum), `adapters/` (Protocols: SpawnAdapter, BoundaryAdapter with judgment + replan-root + replan-child, GatewayAdapter; fakes: scripted spawn, scripted boundary/replan, echo gateway + offline gateway; real implementations as thin stubs wired to config; registry selecting real/fake from config+test mode), `server.py` (FastAPI app factory shell mounting domain routers, static `assets/`, WS stub, test-mode router stub).
4. Domain contracts, stdlib-only (`dataclasses`, `enum`, `typing`): `tickets/contracts.py` (TicketState in exact order, GATING_FIELD and ADVANCE_TARGET tables per §4.2, Priority, AtCap, Proposal/FieldSlot/TicketFields shapes, grant-pair types with `none` ceiling sentinel), `sprints/contracts.py` (Sprint, SprintItem, ItemStatus, Project, kickoff/review frozen-field groups, addenda entry, item status-proposal shape), `days/contracts.py` (Day, PlanTree/PlanNode, NodeStatus, planning-date signature), `dispatch/contracts.py` (Run, RunStatus incl. `spawn_failed`, eligibility/ordering input shapes), `seed/contracts.py` (migration report shape with per-kind counts + explicit skipped list; parsed intermediate shapes; Readiness and status mapping tables per §12), `chat/contracts.py` (message/session shapes, offline signal).
5. Surface stubs: each domain `api.py` exposing an APIRouter with the §9 routes raising NotImplementedError; `src/planner/cli/main.py` with the complete §8 verb tree (click), global `--json`, exit-code contract (0/1/2), `--body-file`/stdin conventions, env defaults — handlers stubbed.
6. Empty scaffolding: `assets/`, `tests/unit/`, `tests/e2e/`, `tests/fixtures/`, `skills/`, `scripts/`.

## Constraints

- Domain contracts import stdlib only. Core may import pydantic/FastAPI only in `server.py`/`api` surfaces, never in contracts, logic-facing types, db, clock, ids, events.
- No shape may be declared anywhere except its contracts file. DDL column names line up with contract field names.
- `ruff check .` and `mypy` (strict, src/) pass; `python -c "import planner"` and every submodule import succeed; `plan --help` exits 0.
- Nothing outside this repository is read or written.

## Acceptance for integration

ruff + mypy clean; editable install works; import smoke passes; CLI help renders the §8 verb tree; DDL creates all tables in a temp DB; schema columns match contracts.

exec
/bin/zsh -lc "sed -n '1,360p' orchestration/tickets/T01-contracts/plan.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
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

codex
The initial plan output stops mid-contracts, so I’m paging through the rest rather than relying on partial text.
exec
/bin/zsh -lc "sed -n '1161,1560p' orchestration/tickets/T01-contracts/plan.md" in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc "sed -n '761,1160p' orchestration/tickets/T01-contracts/plan.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
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

 succeeded in 0ms:

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


exec
/bin/zsh -lc "sed -n '361,760p' orchestration/tickets/T01-contracts/plan.md" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
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

codex
I’m now checking the implementation surfaces directly: config defaults, CSS token categories, contract imports, route decorators, CLI decorators, and DDL. After that I’ll drill into any mismatches with line references.
exec
/bin/zsh -lc 'nl -ba pyproject.toml' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	[build-system]
     2	requires = ["setuptools>=69"]
     3	build-backend = "setuptools.build_meta"
     4	
     5	[project]
     6	name = "planner"
     7	version = "2.0.0"
     8	description = "Planning system v2 — database-backed personal planning server and agent CLI."
     9	requires-python = ">=3.12"
    10	dependencies = [
    11	  "fastapi",
    12	  "uvicorn",
    13	  "click",
    14	  "httpx",
    15	  "pydantic",
    16	  "PyYAML",
    17	]
    18	
    19	[project.scripts]
    20	plan = "planner.cli.main:main"
    21	
    22	[tool.setuptools.packages.find]
    23	where = ["src"]
    24	
    25	[tool.setuptools.package-data]
    26	planner = ["py.typed"]
    27	
    28	[tool.ruff]
    29	line-length = 100
    30	target-version = "py312"
    31	src = ["src", "tests"]
    32	
    33	[tool.ruff.lint]
    34	select = ["E", "F", "W", "I", "UP", "B"]
    35	
    36	[tool.ruff.lint.per-file-ignores]
    37	# The schema DDL is a single SQL string; its column definitions run past 100 cols.
    38	"src/planner/core/db.py" = ["E501"]
    39	
    40	[tool.mypy]
    41	strict = true
    42	files = ["src"]
    43	mypy_path = "src"
    44	python_version = "3.12"
    45	
    46	[tool.pytest.ini_options]
    47	testpaths = ["tests"]
    48	addopts = "-q"

exec
/bin/zsh -lc 'nl -ba config.yaml' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	# Planner configuration — checked-in defaults. Every value here is overridable by
     2	# the matching PLAN_* environment variable. Test-mode keys (PLAN_TEST_MODE,
     3	# PLAN_FAKE_NOW) are environment-only and deliberately absent from this file.
     4	
     5	db_path: data/planning.db          # SQLite database location (§13)
     6	port: 8767                         # server port; bind host is fixed at 127.0.0.1 (§2)
     7	boundary_hour: 5                   # planning-day boundary, local hour (R1)
     8	tick_seconds: 60                   # dispatcher tick interval (§7.1)
     9	claim_ttl_seconds: 900             # claim time-to-live, 15 minutes (§7.3)
    10	max_runs: 2                        # max concurrent dispatched runs (R7)
    11	failure_limit: 2                   # consecutive failures before auto-block (§7.5)
    12	dispatch_enabled: true             # dispatcher master switch; re-read every tick (§7.1)
    13	hermes_bin: hermes                 # hermes executable for the spawn adapter (§7.4)
    14	hermes_profile: default            # spawn profile (R3)
    15	worker_skill: planning-worker      # dispatched-agent skill (R3)
    16	ws_poll_ms: 300                    # server-side event-tail poll interval (§9)
    17	ui_debounce_ms: 250                # UI refetch debounce, served via /api/meta (§9)
    18	run_max_seconds: 1800              # per-run max runtime, 30 minutes (§7.3)
    19	boundary_timeout_seconds: 60       # boundary adapter call timeout (§6.2)
    20	title_max_chars: 200               # ticket title length cap (§3.3)
    21	dispatcher_lock_path: data/dispatcher.lock   # advisory single-dispatcher lock (§7.1)
    22	logs_dir: data/logs                # per-run log files (§7.4)
    23	events_read_limit: 500             # max events returned per WS tail batch (§9)
    24	db_busy_timeout_ms: 5000           # SQLite busy timeout on connect
    25	spawn_adapter: auto                # auto | real | fake (§9.4)
    26	boundary_adapter: auto             # auto | real | fake (§9.4)
    27	gateway_adapter: auto              # auto | real | fake | offline (§9.4)

exec
/bin/zsh -lc 'nl -ba assets/tokens.css' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	/* The single token file. The app's entire personality is tunable here — no other
     2	 * file hardcodes a colour, radius, duration, spacing step, or border width.
     3	 * Starter dark-neutral values; tuned in stage 5. No new categories without
     4	 * evidence of need (PRINCIPLES.md, SPEC §10). */
     5	
     6	:root {
     7	  /* Surfaces — layered neutrals, darkest at the back */
     8	  --surface-base: #0f1113;
     9	  --surface-raised: #171a1d;
    10	  --surface-overlay: #1f2327;
    11	  --surface-sunken: #0a0c0d;
    12	
    13	  /* Text tokens — descending emphasis */
    14	  --text-strong: #f2f4f6;
    15	  --text-default: #c9ced3;
    16	  --text-muted: #8b9299;
    17	  --text-faint: #5c6369;
    18	
    19	  /* Accent — bright (focal), surface (accent fill), text (on accent) */
    20	  --accent-bright: #5b9dff;
    21	  --accent-surface: #16304f;
    22	  --accent-text: #eaf1fb;
    23	
    24	  /* Radius scale */
    25	  --radius-sm: 4px;
    26	  --radius-md: 8px;
    27	  --radius-lg: 14px;
    28	  --radius-pill: 999px;
    29	
    30	  /* Motion durations — entrances ease out, nothing bounces */
    31	  --motion-fast: 90ms;
    32	  --motion-base: 160ms;
    33	  --motion-slow: 280ms;
    34	  --motion-ease: cubic-bezier(0.2, 0.7, 0.3, 1);
    35	
    36	  /* Spacing scale */
    37	  --space-1: 4px;
    38	  --space-2: 8px;
    39	  --space-3: 12px;
    40	  --space-4: 16px;
    41	  --space-5: 24px;
    42	  --space-6: 32px;
    43	  --space-7: 48px;
    44	
    45	  /* Border widths */
    46	  --border-hairline: 1px;
    47	  --border-strong: 2px;
    48	}

exec
/bin/zsh -lc 'rg -n "''^(from|import) |pydantic|fastapi|BaseModel|APIRouter|FastAPI|@.*'"\\.(get|post|patch|put|delete|websocket)|@click|click\\.\" src/planner" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
src/planner/__main__.py:1:from planner.cli.main import main
src/planner/days/contracts.py:4:from __future__ import annotations
src/planner/days/contracts.py:6:from collections.abc import Callable
src/planner/days/contracts.py:7:from dataclasses import dataclass, field
src/planner/days/contracts.py:8:from datetime import date, datetime
src/planner/days/contracts.py:9:from enum import StrEnum
src/planner/days/api.py:4:from __future__ import annotations
src/planner/days/api.py:6:from typing import Any
src/planner/days/api.py:8:from fastapi import APIRouter
src/planner/days/api.py:10:router = APIRouter()
src/planner/days/api.py:13:@router.get("/day/{date}")
src/planner/days/api.py:18:@router.patch("/day/{date}")
src/planner/days/api.py:23:@router.post("/day/{date}/tickets")
src/planner/days/api.py:28:@router.delete("/day/{date}/tickets/{ticket_id}")
src/planner/days/api.py:33:@router.post("/day/{date}/plan/accept")
src/planner/days/api.py:38:@router.post("/day/{date}/plan/accept-all")
src/planner/days/api.py:43:@router.post("/day/{date}/plan/invalidate")
src/planner/days/api.py:48:@router.post("/day/{date}/plan/reject-all")
src/planner/cli/main.py:13:from __future__ import annotations
src/planner/cli/main.py:15:import sqlite3
src/planner/cli/main.py:16:from collections.abc import Callable
src/planner/cli/main.py:17:from typing import Any
src/planner/cli/main.py:19:import click
src/planner/cli/main.py:28:    return click.option(
src/planner/cli/main.py:37:@click.group()
src/planner/cli/main.py:79:@click.option("--source", default=None, help="Path to a markdown planning directory.")
src/planner/cli/main.py:80:@click.option("--demo", is_flag=True, default=False, help="Create the deterministic demo dataset.")
src/planner/cli/main.py:91:@click.argument("field", type=click.Choice(_FIELDS))
src/planner/cli/main.py:92:@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
src/planner/cli/main.py:93:@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
src/planner/cli/main.py:101:@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
src/planner/cli/main.py:102:@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
src/planner/cli/main.py:110:@click.argument("field", type=click.Choice(_FIELDS))
src/planner/cli/main.py:111:@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
src/planner/cli/main.py:112:@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
src/planner/cli/main.py:128:@click.option("--title", required=True, help="Ticket title (<= 200 chars).")
src/planner/cli/main.py:129:@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
src/planner/cli/main.py:130:@click.option("--deadline", default=None, help="ISO date.")
src/planner/cli/main.py:131:@click.option("--project", default=None, help="Vylo | Tribe | Learning | Other.")
src/planner/cli/main.py:132:@click.option("--sprint", default=None, help="Sprint id (standalone tickets only).")
src/planner/cli/main.py:133:@click.option("--item", default=None, help="Sprint item id to parent under.")
src/planner/cli/main.py:149:@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
src/planner/cli/main.py:157:@click.option("--state", default=None)
src/planner/cli/main.py:158:@click.option("--project", default=None)
src/planner/cli/main.py:159:@click.option("--sprint", default=None)
src/planner/cli/main.py:167:@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
src/planner/cli/main.py:168:@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
src/planner/cli/main.py:169:@click.option("--deadline", default=None, help="ISO date, or 'none' to clear.")
src/planner/cli/main.py:170:@click.option("--day", default=None, help="ISO date or 'today' to assign to a day.")
src/planner/cli/main.py:171:@click.option("--sprint", default=None, help="Sprint id, or 'none' to clear.")
src/planner/cli/main.py:194:@click.option("--title", required=True)
src/planner/cli/main.py:195:@click.option("--project", required=True, help="Vylo | Tribe | Learning | Other.")
src/planner/cli/main.py:196:@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
src/planner/cli/main.py:197:@click.option("--deadline", default=None, help="ISO date.")
src/planner/cli/main.py:198:@click.option("--sprint", default=None, help="Sprint id.")
src/planner/cli/main.py:213:@click.argument("item_id")
src/planner/cli/main.py:221:@click.option("--status", default=None)
src/planner/cli/main.py:222:@click.option("--project", default=None)
src/planner/cli/main.py:223:@click.option("--backlog", is_flag=True, default=False, help="Items with no sprint (backlog).")
src/planner/cli/main.py:231:@click.argument("item_id")
src/planner/cli/main.py:232:@click.option("--status", type=click.Choice(["todo", "active", "blocked"]), default=None)
src/planner/cli/main.py:233:@click.option("--blocked-by", default=None, help="Comma-separated blocker ticket ids.")
src/planner/cli/main.py:241:@click.argument("item_id")
src/planner/cli/main.py:242:@click.option(
src/planner/cli/main.py:243:    "--to", "to_status", type=click.Choice(["done", "deferred_next_sprint"]), required=True
src/planner/cli/main.py:245:@click.option("--body-file", default=None, help="Optional rationale body, or - for stdin.")
src/planner/cli/main.py:276:@click.option("--title", required=True)
src/planner/cli/main.py:277:@click.option("--project", default=None, help="Vylo | Tribe | Learning | Other.")
src/planner/cli/main.py:278:@click.option("--body-file", default=None, help="Optional body, or - for stdin.")
src/planner/cli/main.py:301:@click.argument("date", required=False)
src/planner/cli/main.py:309:@click.argument("ticket_id")
src/planner/cli/main.py:310:@click.argument("date", required=False)
src/planner/cli/main.py:318:@click.argument("ticket_id")
src/planner/cli/main.py:319:@click.argument("date", required=False)
src/planner/cli/main.py:335:@click.argument("from_id")
src/planner/cli/main.py:336:@click.argument("to_id")
src/planner/cli/main.py:337:@click.option("--kind", type=click.Choice(_LINK_KINDS), required=True)
src/planner/cli/main.py:345:@click.argument("from_id")
src/planner/cli/main.py:346:@click.argument("to_id")
src/planner/cli/main.py:347:@click.option("--kind", type=click.Choice(_LINK_KINDS), required=True)
src/planner/cli/main.py:370:@click.option("--outcome", type=click.Choice(["done", "blocked"]), required=True)
src/planner/cli/main.py:371:@click.option("--summary", default=None, help="Path to the summary body, or - for stdin.")
src/planner/dispatch/contracts.py:5:from __future__ import annotations
src/planner/dispatch/contracts.py:7:from dataclasses import dataclass
src/planner/dispatch/contracts.py:8:from enum import StrEnum
src/planner/dispatch/contracts.py:9:from typing import Final
src/planner/dispatch/contracts.py:11:from planner.core.contracts import Priority
src/planner/dispatch/contracts.py:12:from planner.tickets.contracts import AtCap, TicketState
src/planner/dispatch/api.py:4:from __future__ import annotations
src/planner/dispatch/api.py:6:from typing import Any
src/planner/dispatch/api.py:8:from fastapi import APIRouter
src/planner/dispatch/api.py:10:router = APIRouter()
src/planner/dispatch/api.py:13:@router.post("/runs/{run_id}/heartbeat")
src/planner/dispatch/api.py:18:@router.post("/runs/{run_id}/close")
src/planner/sprints/contracts.py:4:from __future__ import annotations
src/planner/sprints/contracts.py:6:from dataclasses import dataclass, field
src/planner/sprints/contracts.py:7:from enum import StrEnum
src/planner/sprints/contracts.py:8:from typing import Final
src/planner/sprints/contracts.py:10:from planner.core.contracts import Priority, Project
src/planner/sprints/api.py:5:from __future__ import annotations
src/planner/sprints/api.py:7:from typing import Any
src/planner/sprints/api.py:9:from fastapi import APIRouter
src/planner/sprints/api.py:11:router = APIRouter()
src/planner/sprints/api.py:14:@router.post("/items")
src/planner/sprints/api.py:19:@router.get("/items")
src/planner/sprints/api.py:28:@router.get("/items/{item_id}")
src/planner/sprints/api.py:33:@router.patch("/items/{item_id}")
src/planner/sprints/api.py:38:@router.post("/items/{item_id}/propose-status")
src/planner/sprints/api.py:43:@router.post("/items/{item_id}/accept-status")
src/planner/sprints/api.py:48:@router.post("/sprints")
src/planner/sprints/api.py:53:@router.get("/sprints")
src/planner/sprints/api.py:58:@router.get("/sprints/{sprint_id}")
src/planner/sprints/api.py:63:@router.patch("/sprints/{sprint_id}")
src/planner/sprints/api.py:68:@router.post("/sprints/{sprint_id}/freeze-kickoff")
src/planner/sprints/api.py:73:@router.post("/sprints/{sprint_id}/freeze-review")
src/planner/sprints/api.py:78:@router.post("/sprints/{sprint_id}/addenda")
src/planner/sprints/api.py:83:@router.get("/sprint/current")
src/planner/sprints/api.py:88:@router.post("/ideas")
src/planner/sprints/api.py:93:@router.get("/ideas")
src/planner/chat/contracts.py:4:from __future__ import annotations
src/planner/chat/contracts.py:6:from dataclasses import dataclass
src/planner/chat/api.py:3:from __future__ import annotations
src/planner/chat/api.py:5:from typing import Any
src/planner/chat/api.py:7:from fastapi import APIRouter
src/planner/chat/api.py:9:router = APIRouter()
src/planner/chat/api.py:12:@router.post("/chat/{entity_id}/send")
src/planner/chat/api.py:17:@router.get("/chat/{entity_id}/status")
src/planner/core/ids.py:4:from __future__ import annotations
src/planner/core/ids.py:6:import secrets
src/planner/core/ids.py:7:from datetime import date
src/planner/core/ids.py:8:from typing import Final
src/planner/core/errors.py:5:from __future__ import annotations
src/planner/core/errors.py:7:from enum import StrEnum
src/planner/core/errors.py:9:from planner.core.contracts import JsonDict
src/planner/core/contracts.py:6:from __future__ import annotations
src/planner/core/contracts.py:8:from dataclasses import dataclass
src/planner/core/contracts.py:9:from enum import StrEnum
src/planner/core/contracts.py:10:from typing import Any
src/planner/core/adapters/base.py:5:from __future__ import annotations
src/planner/core/adapters/base.py:7:from dataclasses import dataclass
src/planner/core/adapters/base.py:8:from typing import Any, Protocol
src/planner/core/adapters/base.py:10:from planner.chat.contracts import ChatSendResult, GatewayStatus
src/planner/core/adapters/base.py:11:from planner.days.contracts import PlanNode, PlanTree
src/planner/core/adapters/fakes.py:4:from __future__ import annotations
src/planner/core/adapters/fakes.py:6:from dataclasses import dataclass, field
src/planner/core/adapters/fakes.py:8:from planner.chat.contracts import ChatSendResult, GatewayStatus
src/planner/core/adapters/fakes.py:9:from planner.core.adapters.base import (
src/planner/core/adapters/fakes.py:15:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/adapters/fakes.py:16:from planner.days.contracts import NodeStatus, PlanNode, PlanRoot, PlanTree
src/planner/core/adapters/real.py:5:from __future__ import annotations
src/planner/core/adapters/real.py:7:from typing import TYPE_CHECKING
src/planner/core/adapters/real.py:9:from planner.chat.contracts import ChatSendResult, GatewayStatus
src/planner/core/adapters/real.py:10:from planner.core.adapters.base import (
src/planner/core/adapters/real.py:16:from planner.days.contracts import PlanNode, PlanTree
src/planner/core/adapters/registry.py:2:from config keys. `auto` resolves to fake under test mode and real otherwise;
src/planner/core/adapters/registry.py:5:from __future__ import annotations
src/planner/core/adapters/registry.py:7:from dataclasses import dataclass
src/planner/core/adapters/registry.py:8:from typing import TYPE_CHECKING
src/planner/core/adapters/registry.py:10:from planner.core.adapters.base import BoundaryAdapter, GatewayAdapter, SpawnAdapter
src/planner/core/adapters/registry.py:11:from planner.core.adapters.fakes import (
src/planner/core/adapters/registry.py:17:from planner.core.adapters.real import (
src/planner/core/adapters/registry.py:22:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/clock.py:5:from __future__ import annotations
src/planner/core/clock.py:7:from datetime import datetime
src/planner/core/clock.py:8:from typing import TYPE_CHECKING, Protocol
src/planner/core/events.py:5:from __future__ import annotations
src/planner/core/events.py:7:import json
src/planner/core/events.py:8:import sqlite3
src/planner/core/events.py:10:from planner.core.contracts import EventKind, EventRow, JsonDict
src/planner/core/config.py:5:from __future__ import annotations
src/planner/core/config.py:7:import os
src/planner/core/config.py:8:from collections.abc import Mapping
src/planner/core/config.py:9:from dataclasses import dataclass
src/planner/core/config.py:10:from pathlib import Path
src/planner/core/config.py:11:from typing import Final
src/planner/core/config.py:13:import yaml
src/planner/core/config.py:15:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/server.py:1:"""FastAPI app factory shell. Mounts the domain routers under /api, serves the
src/planner/core/server.py:7:This module and the domain api.py files are the only places FastAPI/pydantic
src/planner/core/server.py:10:from __future__ import annotations
src/planner/core/server.py:12:import sqlite3
src/planner/core/server.py:13:from collections.abc import Callable
src/planner/core/server.py:14:from typing import Any
src/planner/core/server.py:16:from fastapi import FastAPI, Request, WebSocket
src/planner/core/server.py:17:from fastapi.responses import HTMLResponse, JSONResponse
src/planner/core/server.py:18:from fastapi.staticfiles import StaticFiles
src/planner/core/server.py:20:from planner.chat.api import router as chat_router
src/planner/core/server.py:21:from planner.core.adapters.registry import Adapters
src/planner/core/server.py:22:from planner.core.clock import Clock
src/planner/core/server.py:23:from planner.core.config import Config
src/planner/core/server.py:24:from planner.core.errors import ErrorCode, PlannerError
src/planner/core/server.py:25:from planner.days.api import router as days_router
src/planner/core/server.py:26:from planner.dispatch.api import router as dispatch_router
src/planner/core/server.py:27:from planner.seed.api import router as seed_router
src/planner/core/server.py:28:from planner.sprints.api import router as sprints_router
src/planner/core/server.py:29:from planner.tickets.api import router as tickets_router
src/planner/core/server.py:49:) -> FastAPI:
src/planner/core/server.py:54:    app = FastAPI(title="planner", version="2.0.0")
src/planner/core/server.py:74:    @app.get("/api/meta")
src/planner/core/server.py:82:    @app.websocket("/api/events")
src/planner/core/server.py:89:    @app.get("/", response_class=HTMLResponse)
src/planner/core/server.py:95:        @app.post("/api/test/tick-boundary")
src/planner/core/server.py:99:        @app.post("/api/test/tick-dispatcher")
src/planner/core/server.py:103:        @app.post("/api/test/set-now")
src/planner/core/db.py:6:from __future__ import annotations
src/planner/core/db.py:8:import sqlite3
src/planner/core/db.py:9:from typing import Final
src/planner/seed/contracts.py:4:from __future__ import annotations
src/planner/seed/contracts.py:6:from dataclasses import dataclass, field
src/planner/seed/contracts.py:7:from typing import Final
src/planner/seed/contracts.py:9:from planner.core.contracts import Priority, Project
src/planner/seed/contracts.py:10:from planner.sprints.contracts import ItemStatus
src/planner/seed/contracts.py:11:from planner.tickets.contracts import TicketState
src/planner/seed/api.py:4:from __future__ import annotations
src/planner/seed/api.py:6:from typing import Any
src/planner/seed/api.py:8:from fastapi import APIRouter
src/planner/seed/api.py:10:router = APIRouter()
src/planner/seed/api.py:13:@router.post("/seed")
src/planner/tickets/contracts.py:5:from __future__ import annotations
src/planner/tickets/contracts.py:7:from dataclasses import dataclass, field
src/planner/tickets/contracts.py:8:from enum import StrEnum
src/planner/tickets/contracts.py:9:from typing import Final, Literal
src/planner/tickets/contracts.py:11:from planner.core.contracts import Priority, Project
src/planner/tickets/api.py:5:from __future__ import annotations
src/planner/tickets/api.py:7:from typing import Any
src/planner/tickets/api.py:9:from fastapi import APIRouter
src/planner/tickets/api.py:11:router = APIRouter()
src/planner/tickets/api.py:14:@router.post("/tickets")
src/planner/tickets/api.py:19:@router.get("/tickets")
src/planner/tickets/api.py:29:@router.get("/tickets/{ticket_id}")
src/planner/tickets/api.py:34:@router.patch("/tickets/{ticket_id}")
src/planner/tickets/api.py:39:@router.post("/tickets/{ticket_id}/propose/{field}")
src/planner/tickets/api.py:44:@router.post("/tickets/{ticket_id}/accept/{field}")
src/planner/tickets/api.py:49:@router.post("/tickets/{ticket_id}/approve")
src/planner/tickets/api.py:54:@router.put("/tickets/{ticket_id}/notes/{field}")
src/planner/tickets/api.py:59:@router.put("/tickets/{ticket_id}/recap")
src/planner/tickets/api.py:64:@router.post("/tickets/{ticket_id}/grant")
src/planner/tickets/api.py:69:@router.post("/tickets/{ticket_id}/state")
src/planner/tickets/api.py:74:@router.post("/tickets/{ticket_id}/drop")
src/planner/tickets/api.py:79:@router.post("/tickets/{ticket_id}/unblock")
src/planner/tickets/api.py:84:@router.get("/tickets/{ticket_id}/events")
src/planner/tickets/api.py:89:@router.get("/tickets/{ticket_id}/runs")
src/planner/tickets/api.py:94:@router.get("/tickets/{ticket_id}/copy-text")
src/planner/tickets/api.py:99:@router.post("/links")
src/planner/tickets/api.py:104:@router.delete("/links")
src/planner/tickets/api.py:109:@router.get("/board")
src/planner/tickets/api.py:114:@router.get("/queues")

exec
/bin/zsh -lc 'rg -n "CREATE TABLE|CREATE UNIQUE|CREATE INDEX|CHECK|status IN|kind IN|Config|events_read_limit|db_busy_timeout|title_max|EventKind|LinkKind|Project|Priority|class .*StrEnum|class .*Enum|dataclass|def " src/planner' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
src/planner/days/contracts.py:7:from dataclasses import dataclass, field
src/planner/days/contracts.py:12:class NodeStatus(StrEnum):         # §6.3
src/planner/days/contracts.py:18:@dataclass
src/planner/days/contracts.py:24:@dataclass
src/planner/days/contracts.py:32:@dataclass
src/planner/days/contracts.py:38:@dataclass
src/planner/days/contracts.py:49:@dataclass(frozen=True)
src/planner/days/api.py:14:async def get_day(date: str) -> dict[str, Any]:
src/planner/days/api.py:19:async def patch_day(date: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/days/api.py:24:async def add_day_ticket(date: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/days/api.py:29:async def remove_day_ticket(date: str, ticket_id: str) -> dict[str, Any]:
src/planner/days/api.py:34:async def plan_accept(date: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/days/api.py:39:async def plan_accept_all(date: str) -> dict[str, Any]:
src/planner/days/api.py:44:async def plan_invalidate(date: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/days/api.py:49:async def plan_reject_all(date: str) -> dict[str, Any]:
src/planner/cli/main.py:27:def json_option(func: Callable[..., Any]) -> Callable[..., Any]:
src/planner/cli/main.py:38:def main() -> None:
src/planner/cli/main.py:47:def serve(as_json: bool) -> None:
src/planner/cli/main.py:62:    with connect(config.db_path, config.db_busy_timeout_ms) as bootstrap:
src/planner/cli/main.py:68:    def conn_factory() -> sqlite3.Connection:
src/planner/cli/main.py:69:        return connect(config.db_path, config.db_busy_timeout_ms)
src/planner/cli/main.py:82:def seed(source: str | None, demo: bool, as_json: bool) -> None:
src/planner/cli/main.py:95:def propose(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
src/planner/cli/main.py:104:def recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
src/planner/cli/main.py:114:def note(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
src/planner/cli/main.py:123:def ticket() -> None:
src/planner/cli/main.py:135:def ticket_create(
src/planner/cli/main.py:151:def ticket_show(ticket_id: str | None, as_json: bool) -> None:
src/planner/cli/main.py:161:def ticket_list(state: str | None, project: str | None, sprint: str | None, as_json: bool) -> None:
src/planner/cli/main.py:173:def ticket_set(
src/planner/cli/main.py:189:def item() -> None:
src/planner/cli/main.py:200:def item_create(
src/planner/cli/main.py:215:def item_show(item_id: str, as_json: bool) -> None:
src/planner/cli/main.py:225:def item_list(status: str | None, project: str | None, backlog: bool, as_json: bool) -> None:
src/planner/cli/main.py:235:def item_set(item_id: str, status: str | None, blocked_by: str | None, as_json: bool) -> None:
src/planner/cli/main.py:247:def item_propose_status(item_id: str, to_status: str, body_file: str | None, as_json: bool) -> None:
src/planner/cli/main.py:256:def sprint() -> None:
src/planner/cli/main.py:262:def sprint_show(as_json: bool) -> None:
src/planner/cli/main.py:271:def idea() -> None:
src/planner/cli/main.py:280:def idea_create(title: str, project: str | None, body_file: str | None, as_json: bool) -> None:
src/planner/cli/main.py:287:def idea_list(as_json: bool) -> None:
src/planner/cli/main.py:296:def day() -> None:
src/planner/cli/main.py:303:def day_show(date: str | None, as_json: bool) -> None:
src/planner/cli/main.py:312:def day_add_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
src/planner/cli/main.py:321:def day_remove_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
src/planner/cli/main.py:330:def link() -> None:
src/planner/cli/main.py:339:def link_add(from_id: str, to_id: str, kind: str, as_json: bool) -> None:
src/planner/cli/main.py:349:def link_rm(from_id: str, to_id: str, kind: str, as_json: bool) -> None:
src/planner/cli/main.py:358:def run() -> None:
src/planner/cli/main.py:364:def run_heartbeat(as_json: bool) -> None:
src/planner/cli/main.py:373:def run_close(outcome: str, summary: str | None, as_json: bool) -> None:
src/planner/cli/main.py:382:def queue() -> None:
src/planner/cli/main.py:388:def queue_approvals(as_json: bool) -> None:
src/planner/cli/main.py:395:def queue_pickup(as_json: bool) -> None:
src/planner/cli/main.py:402:def queue_overdue(as_json: bool) -> None:
src/planner/dispatch/contracts.py:7:from dataclasses import dataclass
src/planner/dispatch/contracts.py:11:from planner.core.contracts import Priority
src/planner/dispatch/contracts.py:15:class RunStatus(StrEnum):          # §7.3 six + spawn_failed (§7.5)
src/planner/dispatch/contracts.py:36:@dataclass
src/planner/dispatch/contracts.py:48:@dataclass(frozen=True)
src/planner/dispatch/contracts.py:55:    priority: Priority
src/planner/dispatch/api.py:14:async def heartbeat(run_id: str) -> dict[str, Any]:
src/planner/dispatch/api.py:19:async def close_run(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/contracts.py:2:proposal. Stdlib only; Priority/Project imported from core."""
src/planner/sprints/contracts.py:6:from dataclasses import dataclass, field
src/planner/sprints/contracts.py:10:from planner.core.contracts import Priority, Project
src/planner/sprints/contracts.py:13:class ItemStatus(StrEnum):         # §3.2
src/planner/sprints/contracts.py:41:@dataclass(frozen=True)
src/planner/sprints/contracts.py:47:@dataclass(frozen=True)
src/planner/sprints/contracts.py:55:@dataclass
src/planner/sprints/contracts.py:77:@dataclass
src/planner/sprints/contracts.py:83:    priority: Priority
src/planner/sprints/contracts.py:85:    project: Project
src/planner/sprints/contracts.py:94:@dataclass
src/planner/sprints/contracts.py:99:    project: Project | None
src/planner/sprints/api.py:15:async def create_item(body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:20:async def list_items(
src/planner/sprints/api.py:29:async def get_item(item_id: str) -> dict[str, Any]:
src/planner/sprints/api.py:34:async def patch_item(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:39:async def propose_item_status(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:44:async def accept_item_status(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:49:async def create_sprint(body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:54:async def list_sprints() -> dict[str, Any]:
src/planner/sprints/api.py:59:async def get_sprint(sprint_id: str) -> dict[str, Any]:
src/planner/sprints/api.py:64:async def patch_sprint(sprint_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:69:async def freeze_kickoff(sprint_id: str) -> dict[str, Any]:
src/planner/sprints/api.py:74:async def freeze_review(sprint_id: str) -> dict[str, Any]:
src/planner/sprints/api.py:79:async def add_addendum(sprint_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:84:async def current_sprint() -> dict[str, Any]:
src/planner/sprints/api.py:89:async def create_idea(body: dict[str, Any]) -> dict[str, Any]:
src/planner/sprints/api.py:94:async def list_ideas() -> dict[str, Any]:
src/planner/chat/contracts.py:6:from dataclasses import dataclass
src/planner/chat/contracts.py:9:@dataclass(frozen=True)
src/planner/chat/contracts.py:16:@dataclass(frozen=True)
src/planner/chat/contracts.py:22:@dataclass(frozen=True)
src/planner/chat/api.py:13:async def send_message(entity_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/chat/api.py:18:async def gateway_status(entity_id: str) -> dict[str, Any]:
src/planner/core/ids.py:22:def _slug(length: int) -> str:
src/planner/core/ids.py:26:def new_id(prefix: str) -> str:
src/planner/core/ids.py:30:def day_id(planning_date: date) -> str:
src/planner/core/ids.py:34:def new_claim() -> str:
src/planner/core/errors.py:12:class ErrorCode(StrEnum):
src/planner/core/errors.py:19:    title_too_long = "title_too_long"              # §3.3 > title_max_chars
src/planner/core/errors.py:33:    def __init__(self, code: ErrorCode, message: str, detail: JsonDict | None = None) -> None:
src/planner/core/errors.py:39:    def to_payload(self) -> JsonDict:
src/planner/core/contracts.py:8:from dataclasses import dataclass
src/planner/core/contracts.py:16:class Project(StrEnum):                     # SPEC §3.2, exact strings (capitalised as in SPEC)
src/planner/core/contracts.py:23:class Priority(StrEnum):                     # SPEC §3.2/§3.3 — homed in core (shared vocabulary)
src/planner/core/contracts.py:30:class LinkKind(StrEnum):                     # SPEC §3.6, exact strings
src/planner/core/contracts.py:37:class EventKind(StrEnum):
src/planner/core/contracts.py:92:@dataclass(frozen=True)
src/planner/core/contracts.py:101:@dataclass(frozen=True)
src/planner/core/contracts.py:105:    kind: LinkKind
src/planner/core/adapters/base.py:7:from dataclasses import dataclass
src/planner/core/adapters/base.py:14:@dataclass(frozen=True)
src/planner/core/adapters/base.py:26:@dataclass(frozen=True)
src/planner/core/adapters/base.py:34:    def spawn(self, request: SpawnRequest) -> SpawnResult: ...
src/planner/core/adapters/base.py:37:@dataclass(frozen=True)
src/planner/core/adapters/base.py:45:@dataclass(frozen=True)
src/planner/core/adapters/base.py:54:    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment: ...
src/planner/core/adapters/base.py:55:    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree: ...
src/planner/core/adapters/base.py:56:    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode: ...
src/planner/core/adapters/base.py:60:    def status(self) -> GatewayStatus: ...
src/planner/core/adapters/base.py:61:    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult: ...
src/planner/core/adapters/fakes.py:6:from dataclasses import dataclass, field
src/planner/core/adapters/fakes.py:21:@dataclass
src/planner/core/adapters/fakes.py:27:    def spawn(self, request: SpawnRequest) -> SpawnResult:
src/planner/core/adapters/fakes.py:36:@dataclass
src/planner/core/adapters/fakes.py:44:    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
src/planner/core/adapters/fakes.py:64:    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
src/planner/core/adapters/fakes.py:72:    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
src/planner/core/adapters/fakes.py:86:@dataclass
src/planner/core/adapters/fakes.py:91:    def status(self) -> GatewayStatus:
src/planner/core/adapters/fakes.py:94:    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
src/planner/core/adapters/fakes.py:102:@dataclass
src/planner/core/adapters/fakes.py:104:    def status(self) -> GatewayStatus:
src/planner/core/adapters/fakes.py:107:    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
src/planner/core/adapters/real.py:7:from typing import TYPE_CHECKING
src/planner/core/adapters/real.py:18:if TYPE_CHECKING:
src/planner/core/adapters/real.py:19:    from planner.core.config import Config
src/planner/core/adapters/real.py:25:    def __init__(self, config: Config) -> None:
src/planner/core/adapters/real.py:28:    def spawn(self, request: SpawnRequest) -> SpawnResult:
src/planner/core/adapters/real.py:33:    def __init__(self, config: Config) -> None:
src/planner/core/adapters/real.py:36:    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
src/planner/core/adapters/real.py:39:    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
src/planner/core/adapters/real.py:42:    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
src/planner/core/adapters/real.py:47:    def __init__(self, config: Config) -> None:
src/planner/core/adapters/real.py:50:    def status(self) -> GatewayStatus:
src/planner/core/adapters/real.py:53:    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
src/planner/core/adapters/registry.py:7:from dataclasses import dataclass
src/planner/core/adapters/registry.py:8:from typing import TYPE_CHECKING
src/planner/core/adapters/registry.py:24:if TYPE_CHECKING:
src/planner/core/adapters/registry.py:25:    from planner.core.config import Config
src/planner/core/adapters/registry.py:28:@dataclass(frozen=True)
src/planner/core/adapters/registry.py:35:def _resolve_auto(choice: str, test_mode: bool) -> str:
src/planner/core/adapters/registry.py:41:def _select_spawn(config: Config) -> SpawnAdapter:
src/planner/core/adapters/registry.py:50:def _select_boundary(config: Config) -> BoundaryAdapter:
src/planner/core/adapters/registry.py:61:def _select_gateway(config: Config) -> GatewayAdapter:
src/planner/core/adapters/registry.py:72:def build_adapters(config: Config) -> Adapters:
src/planner/core/clock.py:8:from typing import TYPE_CHECKING, Protocol
src/planner/core/clock.py:10:if TYPE_CHECKING:
src/planner/core/clock.py:11:    from planner.core.config import Config
src/planner/core/clock.py:15:    def now(self) -> datetime: ...       # timezone-aware, LOCAL time (§6.1 math is local)
src/planner/core/clock.py:16:    def now_unix(self) -> int: ...
src/planner/core/clock.py:20:    def now(self) -> datetime:
src/planner/core/clock.py:23:    def now_unix(self) -> int:
src/planner/core/clock.py:30:    def __init__(self, start: datetime) -> None:
src/planner/core/clock.py:33:    def now(self) -> datetime:
src/planner/core/clock.py:36:    def now_unix(self) -> int:
src/planner/core/clock.py:39:    def set(self, now: datetime) -> None:
src/planner/core/clock.py:43:def parse_fake_now(raw: str) -> datetime:
src/planner/core/clock.py:51:def build_clock(config: Config) -> Clock:
src/planner/core/events.py:10:from planner.core.contracts import EventKind, EventRow, JsonDict
src/planner/core/events.py:13:def append_event(
src/planner/core/events.py:16:    kind: EventKind,
src/planner/core/events.py:29:def read_events_since(conn: sqlite3.Connection, since_id: int, limit: int) -> list[EventRow]:
src/planner/core/config.py:1:"""Configuration: every tunable lives here, loaded from config.yaml with PLAN_*
src/planner/core/config.py:9:from dataclasses import dataclass
src/planner/core/config.py:24:@dataclass(frozen=True)
src/planner/core/config.py:25:class Config:
src/planner/core/config.py:43:    title_max_chars: int
src/planner/core/config.py:46:    events_read_limit: int
src/planner/core/config.py:47:    db_busy_timeout_ms: int
src/planner/core/config.py:57:def _parse_bool(raw: object, key: str) -> bool:
src/planner/core/config.py:68:def _parse_int(raw: object, key: str) -> int:
src/planner/core/config.py:81:def _read_yaml(path: str | None) -> dict[str, object]:
src/planner/core/config.py:93:def _str_value(
src/planner/core/config.py:104:def _int_value(
src/planner/core/config.py:115:def _bool_value(
src/planner/core/config.py:126:def load_config(path: str | None = None, env: Mapping[str, str] | None = None) -> Config:
src/planner/core/config.py:135:    return Config(
src/planner/core/config.py:153:        title_max_chars=_int_value(cfg, env, "title_max_chars", "PLAN_TITLE_MAX_CHARS", 200),
src/planner/core/config.py:158:        events_read_limit=_int_value(cfg, env, "events_read_limit", "PLAN_EVENTS_READ_LIMIT", 500),
src/planner/core/config.py:159:        db_busy_timeout_ms=_int_value(
src/planner/core/config.py:160:            cfg, env, "db_busy_timeout_ms", "PLAN_DB_BUSY_TIMEOUT_MS", 5000
src/planner/core/config.py:170:def read_dispatch_enabled(path: str | None = None, env: Mapping[str, str] | None = None) -> bool:
src/planner/core/server.py:23:from planner.core.config import Config
src/planner/core/server.py:40:def http_status_for(code: ErrorCode) -> int:
src/planner/core/server.py:44:def create_app(
src/planner/core/server.py:45:    config: Config,
src/planner/core/server.py:52:    assert config.title_max_chars == 200, "title_max_chars must equal the DDL literal (200)"
src/planner/core/server.py:61:    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
src/planner/core/server.py:75:    async def meta() -> dict[str, Any]:
src/planner/core/server.py:83:    async def events_ws(websocket: WebSocket, since: int = 0) -> None:
src/planner/core/server.py:90:    async def index() -> str:
src/planner/core/server.py:96:        async def tick_boundary() -> dict[str, Any]:
src/planner/core/server.py:100:        async def tick_dispatcher() -> dict[str, Any]:
src/planner/core/server.py:104:        async def set_now(body: dict[str, Any]) -> dict[str, Any]:
src/planner/core/db.py:4:dataclass field names one-for-one."""
src/planner/core/db.py:14:CREATE TABLE IF NOT EXISTS sprints (
src/planner/core/db.py:33:  CHECK (date_start <= date_end)
src/planner/core/db.py:36:CREATE TABLE IF NOT EXISTS sprint_items (
src/planner/core/db.py:41:                      CHECK (status IN ('todo','active','done','blocked','deferred_next_sprint')),
src/planner/core/db.py:42:  priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
src/planner/core/db.py:44:  project             TEXT NOT NULL CHECK (project IN ('Vylo','Tribe','Learning','Other')),
src/planner/core/db.py:53:CREATE TABLE IF NOT EXISTS tickets (
src/planner/core/db.py:55:  title                TEXT NOT NULL CHECK (length(title) <= 200),
src/planner/core/db.py:57:                       CHECK (state IN ('needs_success','needs_approach','needs_plan',
src/planner/core/db.py:59:  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
src/planner/core/db.py:61:  project              TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- NULL when parented
src/planner/core/db.py:66:                       CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
src/planner/core/db.py:68:  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
src/planner/core/db.py:79:CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL;
src/planner/core/db.py:80:CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state);
src/planner/core/db.py:82:CREATE TABLE IF NOT EXISTS days (
src/planner/core/db.py:92:CREATE TABLE IF NOT EXISTS day_tickets (
src/planner/core/db.py:95:  position  INTEGER NOT NULL CHECK (position >= 0),  -- contiguous from 0, writer-enforced
src/planner/core/db.py:99:CREATE TABLE IF NOT EXISTS ideas (
src/planner/core/db.py:103:  project    TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- nullable (§3.5)
src/planner/core/db.py:108:CREATE TABLE IF NOT EXISTS links (                   -- §3.6, exactly the spec's three columns
src/planner/core/db.py:111:  kind    TEXT NOT NULL CHECK (kind IN ('belongs_to','parent_child','blocks','relates')),
src/planner/core/db.py:113:  CHECK (from_id <> to_id)
src/planner/core/db.py:115:CREATE UNIQUE INDEX IF NOT EXISTS idx_links_one_belongs_to
src/planner/core/db.py:117:CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id, kind);
src/planner/core/db.py:119:CREATE TABLE IF NOT EXISTS events (                  -- §3, append-only
src/planner/core/db.py:126:CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id, id);
src/planner/core/db.py:128:CREATE TABLE IF NOT EXISTS runs (                    -- §7.3
src/planner/core/db.py:132:             CHECK (status IN ('running','done','blocked','crashed','timed_out','reclaimed','spawn_failed')),
src/planner/core/db.py:139:CREATE INDEX IF NOT EXISTS idx_runs_ticket ON runs(ticket_id, started_at);
src/planner/core/db.py:141:CREATE TABLE IF NOT EXISTS boundary_runs (           -- §6.2: never twice per date
src/planner/core/db.py:144:  judgment      TEXT NOT NULL CHECK (judgment IN ('ok','skipped','failed'))
src/planner/core/db.py:149:def connect(db_path: str, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
src/planner/core/db.py:158:def create_schema(conn: sqlite3.Connection) -> None:
src/planner/seed/contracts.py:6:from dataclasses import dataclass, field
src/planner/seed/contracts.py:9:from planner.core.contracts import Priority, Project
src/planner/seed/contracts.py:29:# `Priority:` labels are literal P0..P3 in the source (verified in the snapshot) — identity map.
src/planner/seed/contracts.py:30:PRIORITY_MAP: Final[dict[str, Priority]] = {p.value: p for p in Priority}
src/planner/seed/contracts.py:33:# consulted when a Priority label is absent; empty/unknown values are ignored (P3 default applies).
src/planner/seed/contracts.py:34:URGENCY_MAP: Final[dict[str, Priority]] = {
src/planner/seed/contracts.py:35:    "critical": Priority.P0, "high": Priority.P1, "medium": Priority.P2, "low": Priority.P3,
src/planner/seed/contracts.py:38:# Project headings/labels are the enum's exact strings — identity map.
src/planner/seed/contracts.py:39:PROJECT_MAP: Final[dict[str, Project]] = {p.value: p for p in Project}
src/planner/seed/contracts.py:47:@dataclass
src/planner/seed/contracts.py:63:@dataclass
src/planner/seed/contracts.py:67:    priority: Priority
src/planner/seed/contracts.py:68:    project: Project
src/planner/seed/contracts.py:74:@dataclass
src/planner/seed/contracts.py:78:    priority: Priority
src/planner/seed/contracts.py:87:@dataclass
src/planner/seed/contracts.py:91:    project: Project | None = None
src/planner/seed/contracts.py:94:@dataclass(frozen=True)
src/planner/seed/contracts.py:102:@dataclass
src/planner/seed/api.py:14:async def seed(body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/contracts.py:2:and the ticket row. Stdlib only; Priority/Project are imported from core (shared
src/planner/tickets/contracts.py:7:from dataclasses import dataclass, field
src/planner/tickets/contracts.py:11:from planner.core.contracts import Priority, Project
src/planner/tickets/contracts.py:14:class TicketState(StrEnum):        # §4.1, exact order
src/planner/tickets/contracts.py:32:class FieldName(StrEnum):          # §4.2 — the exactly-four field keys
src/planner/tickets/contracts.py:39:class AtCap(StrEnum):              # §4.3
src/planner/tickets/contracts.py:63:@dataclass(frozen=True)
src/planner/tickets/contracts.py:70:@dataclass
src/planner/tickets/contracts.py:77:@dataclass
src/planner/tickets/contracts.py:90:@dataclass(frozen=True)
src/planner/tickets/contracts.py:96:@dataclass
src/planner/tickets/contracts.py:99:    title: str                     # <= title_max_chars (200), every write path
src/planner/tickets/contracts.py:101:    priority: Priority             # default P3
src/planner/tickets/contracts.py:103:    project: Project | None        # NULL when parented (derived)
src/planner/tickets/api.py:15:async def create_ticket(body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:20:async def list_tickets(
src/planner/tickets/api.py:30:async def get_ticket(ticket_id: str) -> dict[str, Any]:
src/planner/tickets/api.py:35:async def patch_ticket(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:40:async def propose_field(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:45:async def accept_field(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:50:async def approve_ticket(ticket_id: str) -> dict[str, Any]:
src/planner/tickets/api.py:55:async def put_notes(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:60:async def put_recap(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:65:async def grant_ticket(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:70:async def set_state(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:75:async def drop_ticket(ticket_id: str) -> dict[str, Any]:
src/planner/tickets/api.py:80:async def unblock_ticket(ticket_id: str) -> dict[str, Any]:
src/planner/tickets/api.py:85:async def ticket_events(ticket_id: str) -> dict[str, Any]:
src/planner/tickets/api.py:90:async def ticket_runs(ticket_id: str) -> dict[str, Any]:
src/planner/tickets/api.py:95:async def ticket_copy_text(ticket_id: str) -> dict[str, Any]:
src/planner/tickets/api.py:100:async def add_link(body: dict[str, Any]) -> dict[str, Any]:
src/planner/tickets/api.py:105:async def remove_link(from_id: str, to_id: str, kind: str) -> dict[str, Any]:
src/planner/tickets/api.py:110:async def board() -> dict[str, Any]:
src/planner/tickets/api.py:115:async def queues() -> dict[str, Any]:

codex
The first pass shows the broad surfaces are present. I’m drilling into the contracts and primitives now, because the likely deviations are subtle: enum ownership, dataclass fields, default/limit handling, and T01 amendment behavior.
exec
/bin/zsh -lc 'nl -ba src/planner/core/contracts.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Shared vocabulary used across every domain: cross-domain enums, the event
     2	kinds, the link kinds, and the two infrastructure shapes (event row, link row).
     3	
     4	Stdlib only. Nothing here imports another planner module."""
     5	
     6	from __future__ import annotations
     7	
     8	from dataclasses import dataclass
     9	from enum import StrEnum
    10	from typing import Any
    11	
    12	JsonDict = dict[str, Any]  # event payloads, adapter blobs
    13	UnixTime = int             # unix seconds
    14	
    15	
    16	class Project(StrEnum):                     # SPEC §3.2, exact strings (capitalised as in SPEC)
    17	    Vylo = "Vylo"
    18	    Tribe = "Tribe"
    19	    Learning = "Learning"
    20	    Other = "Other"
    21	
    22	
    23	class Priority(StrEnum):                     # SPEC §3.2/§3.3 — homed in core (shared vocabulary)
    24	    P0 = "P0"
    25	    P1 = "P1"
    26	    P2 = "P2"
    27	    P3 = "P3"
    28	
    29	
    30	class LinkKind(StrEnum):                     # SPEC §3.6, exact strings
    31	    belongs_to = "belongs_to"                # ticket -> sprint item
    32	    parent_child = "parent_child"            # ticket -> ticket
    33	    blocks = "blocks"                        # ticket -> ticket | ticket -> sprint item
    34	    relates = "relates"                      # any -> any
    35	
    36	
    37	class EventKind(StrEnum):
    38	    # --- named explicitly in SPEC ---
    39	    state_changed = "state_changed"                  # §4.4.6 {from, to, cause}
    40	    proposal_accepted = "proposal_accepted"          # §4.4.2/4 {field, body, resolved_by, edited}
    41	    proposal_superseded = "proposal_superseded"      # §4.4.1 {field, replaced_body}
    42	    day_ticket_removed = "day_ticket_removed"        # §3.4 {ticket_id}
    43	    day_closed = "day_closed"                        # §6.2 {done_count, not_done_count}
    44	    auto_blocked = "auto_blocked"                    # §7.5 {consecutive_failures}
    45	
    46	    # --- supplemental: creation, one per entity ---
    47	    ticket_created = "ticket_created"
    48	    sprint_created = "sprint_created"
    49	    sprint_item_created = "sprint_item_created"
    50	    idea_created = "idea_created"
    51	    day_created = "day_created"                      # §3.4 materialization
    52	
    53	    # --- supplemental: proposals and fields ---
    54	    proposal_filed = "proposal_filed"                # {field, body, proposed_by}
    55	    note_updated = "note_updated"                    # §4.2 notes slot {field}
    56	    recap_updated = "recap_updated"                  # §3.3
    57	    grant_changed = "grant_changed"                  # {ceiling, at_cap, cause}
    58	
    59	    # --- supplemental: plain field updates (§3.2 "event-logged" updates) ---
    60	    ticket_updated = "ticket_updated"                # {field, from, to} plain edits
    61	    item_updated = "item_updated"
    62	    sprint_updated = "sprint_updated"
    63	    day_updated = "day_updated"                      # notes/brief manual edits
    64	    item_status_changed = "item_status_changed"      # {from, to, cause} incl. agent todo<->active
    65	
    66	    # --- supplemental: day plan lifecycle ---
    67	    plan_proposed = "plan_proposed"                  # boundary judgment stored {tree}
    68	    plan_node_accepted = "plan_node_accepted"        # {node} ("root" | child position)
    69	    plan_accepted_all = "plan_accepted_all"
    70	    plan_node_invalidated = "plan_node_invalidated"  # {node}
    71	    plan_replanned = "plan_replanned"                # §6.3 {old_tree, scope: "root"|"child", node}
    72	    plan_rejected = "plan_rejected"                  # reject-all {old_tree}
    73	    day_ticket_added = "day_ticket_added"            # {ticket_id, position, cause}
    74	    boundary_failed = "boundary_failed"              # §6.2 adapter failure/timeout {error}
    75	
    76	    # --- supplemental: runs and claims ---
    77	    run_started = "run_started"                      # {run_id, pid}
    78	    run_closed = "run_closed"                        # {run_id, status, summary}
    79	    claim_heartbeat = "claim_heartbeat"              # {run_id, claim_expires}
    80	    claim_reclaimed = "claim_reclaimed"              # {run_id, reason: "expired"|"dead_pid"}
    81	    auto_block_cleared = "auto_block_cleared"        # human unblock action
    82	
    83	    # --- supplemental: freeze, links, chat ---
    84	    kickoff_frozen = "kickoff_frozen"
    85	    review_frozen = "review_frozen"
    86	    addendum_added = "addendum_added"                # {date, text}
    87	    link_added = "link_added"                        # {from_id, to_id, kind}
    88	    link_removed = "link_removed"
    89	    chat_session_created = "chat_session_created"    # {session_key}
    90	
    91	
    92	@dataclass(frozen=True)
    93	class EventRow:                              # a row read back from the append-only events table
    94	    id: int
    95	    entity_id: str
    96	    kind: str
    97	    payload: JsonDict
    98	    created_at: int
    99	
   100	
   101	@dataclass(frozen=True)
   102	class Link:                                  # SPEC §3.6 links row
   103	    from_id: str
   104	    to_id: str
   105	    kind: LinkKind

exec
/bin/zsh -lc 'nl -ba src/planner/sprints/contracts.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Sprint domain shapes: sprints, sprint items, ideas, and the item status
     2	proposal. Stdlib only; Priority/Project imported from core."""
     3	
     4	from __future__ import annotations
     5	
     6	from dataclasses import dataclass, field
     7	from enum import StrEnum
     8	from typing import Final
     9	
    10	from planner.core.contracts import Priority, Project
    11	
    12	
    13	class ItemStatus(StrEnum):         # §3.2
    14	    todo = "todo"
    15	    active = "active"
    16	    done = "done"
    17	    blocked = "blocked"
    18	    deferred_next_sprint = "deferred_next_sprint"
    19	
    20	
    21	# Agent-permitted direct transitions (§3.2): todo<->active, and -> blocked (with blockers).
    22	AGENT_ITEM_TRANSITIONS: Final[frozenset[tuple[ItemStatus, ItemStatus]]] = frozenset({
    23	    (ItemStatus.todo, ItemStatus.active),
    24	    (ItemStatus.active, ItemStatus.todo),
    25	    (ItemStatus.todo, ItemStatus.blocked),
    26	    (ItemStatus.active, ItemStatus.blocked),
    27	})
    28	
    29	# Statuses reachable only via human-accepted proposal (§3.2).
    30	PROPOSAL_ONLY_STATUSES: Final[frozenset[ItemStatus]] = frozenset({
    31	    ItemStatus.done, ItemStatus.deferred_next_sprint,
    32	})
    33	
    34	# Freeze groups (§3.1): frozen-write checks compare against these tuples.
    35	KICKOFF_FIELDS: Final[tuple[str, ...]] = ("limiting_factor", "primary_bet", "supports", "premortem")
    36	REVIEW_FIELDS: Final[tuple[str, ...]] = (
    37	    "outcomes", "solo_reflection", "joint_discussion", "updates_to_thinking", "carry_forward",
    38	)
    39	
    40	
    41	@dataclass(frozen=True)
    42	class Addendum:                    # §3.1 weekly_addenda entry
    43	    date: str                      # ISO date
    44	    text: str
    45	
    46	
    47	@dataclass(frozen=True)
    48	class ItemStatusProposal:          # §3.2 status proposal — the item's single gating field
    49	    to_status: ItemStatus          # must be in PROPOSAL_ONLY_STATUSES
    50	    note: str | None               # optional rationale shown in Review
    51	    proposed_by: str
    52	    created_at: int
    53	
    54	
    55	@dataclass
    56	class Sprint:                      # §3.1
    57	    id: str
    58	    name: str
    59	    date_start: str                # ISO, inclusive
    60	    date_end: str                  # ISO, inclusive
    61	    limiting_factor: str
    62	    primary_bet: str
    63	    supports: str
    64	    premortem: str
    65	    weekly_addenda: list[Addendum] = field(default_factory=list)   # append-only
    66	    kickoff_frozen_at: int | None = None
    67	    outcomes: str = ""
    68	    solo_reflection: str = ""
    69	    joint_discussion: str = ""
    70	    updates_to_thinking: str = ""
    71	    carry_forward: str = ""
    72	    review_frozen_at: int | None = None
    73	    created_at: int = 0
    74	    updated_at: int = 0
    75	
    76	
    77	@dataclass
    78	class SprintItem:                  # §3.2
    79	    id: str
    80	    title: str
    81	    body: str
    82	    status: ItemStatus
    83	    priority: Priority
    84	    deadline: str | None
    85	    project: Project
    86	    current_state_note: str
    87	    sprint_id: str | None          # NULL = backlog/deferred
    88	    blocked_by: list[str] = field(default_factory=list)   # ticket ids; non-empty iff blocked
    89	    status_proposal: ItemStatusProposal | None = None
    90	    created_at: int = 0
    91	    updated_at: int = 0
    92	
    93	
    94	@dataclass
    95	class Idea:                        # §3.5
    96	    id: str
    97	    title: str
    98	    body: str
    99	    project: Project | None
   100	    created_at: int
   101	    updated_at: int

exec
/bin/zsh -lc 'nl -ba src/planner/tickets/contracts.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Ticket domain shapes: the state machine order, the four fields, the grant pair,
     2	and the ticket row. Stdlib only; Priority/Project are imported from core (shared
     3	vocabulary), never redeclared."""
     4	
     5	from __future__ import annotations
     6	
     7	from dataclasses import dataclass, field
     8	from enum import StrEnum
     9	from typing import Final, Literal
    10	
    11	from planner.core.contracts import Priority, Project
    12	
    13	
    14	class TicketState(StrEnum):        # §4.1, exact order
    15	    needs_success = "needs_success"
    16	    needs_approach = "needs_approach"
    17	    needs_plan = "needs_plan"
    18	    in_progress = "in_progress"
    19	    needs_review = "needs_review"
    20	    done = "done"
    21	    dropped = "dropped"            # terminal, human-only, outside the linear order
    22	
    23	
    24	# Linear pipeline order (dropped excluded). Index comparison implements "<= ceiling".
    25	# ceiling and next_ceiling values are restricted to members of this tuple (never dropped).
    26	STATE_ORDER: Final[tuple[TicketState, ...]] = (
    27	    TicketState.needs_success, TicketState.needs_approach, TicketState.needs_plan,
    28	    TicketState.in_progress, TicketState.needs_review, TicketState.done,
    29	)
    30	
    31	
    32	class FieldName(StrEnum):          # §4.2 — the exactly-four field keys
    33	    success = "success"
    34	    approach = "approach"
    35	    plan = "plan"
    36	    result = "result"
    37	
    38	
    39	class AtCap(StrEnum):              # §4.3
    40	    stop = "stop"
    41	    propose = "propose"
    42	
    43	
    44	# §4.2 table — gating field per pre-terminal state. needs_review has none (human approve).
    45	GATING_FIELD: Final[dict[TicketState, FieldName]] = {
    46	    TicketState.needs_success: FieldName.success,
    47	    TicketState.needs_approach: FieldName.approach,
    48	    TicketState.needs_plan: FieldName.plan,
    49	    TicketState.in_progress: FieldName.result,
    50	}
    51	
    52	# §4.2 table — accepted proposal advances to. in_progress -> needs_review is the
    53	# default route; the ceiling=done special case (§4.4.5) is resolution-engine logic,
    54	# not a second table entry.
    55	ADVANCE_TARGET: Final[dict[TicketState, TicketState]] = {
    56	    TicketState.needs_success: TicketState.needs_approach,
    57	    TicketState.needs_approach: TicketState.needs_plan,
    58	    TicketState.needs_plan: TicketState.in_progress,
    59	    TicketState.in_progress: TicketState.needs_review,
    60	}
    61	
    62	
    63	@dataclass(frozen=True)
    64	class Proposal:                    # §4.2 proposal slot
    65	    body: str
    66	    proposed_by: str               # actor string: "agent", run id context, or PLAN_ACTOR
    67	    created_at: int
    68	
    69	
    70	@dataclass
    71	class FieldSlot:                   # §4.2 — one of the four field objects
    72	    value: str | None = None       # canonical; resolution engine is the only writer
    73	    proposal: Proposal | None = None
    74	    notes: str | None = None       # free guidance; result.notes = review notes (§4.2)
    75	
    76	
    77	@dataclass
    78	class TicketFields:                # tickets.fields JSON column, exactly four keys
    79	    success: FieldSlot = field(default_factory=FieldSlot)
    80	    approach: FieldSlot = field(default_factory=FieldSlot)
    81	    plan: FieldSlot = field(default_factory=FieldSlot)
    82	    result: FieldSlot = field(default_factory=FieldSlot)
    83	
    84	
    85	# --- the grant pair (§4.4.7) ---
    86	NO_FURTHER: Final = "none"                     # wire sentinel: ceiling = the newly entered state
    87	NextCeiling = TicketState | Literal["none"]    # valid TicketState values are STATE_ORDER members
    88	
    89	
    90	@dataclass(frozen=True)
    91	class GrantPair:                   # required on every human accept/edit-accept
    92	    next_ceiling: NextCeiling
    93	    at_cap: AtCap
    94	
    95	
    96	@dataclass
    97	class Ticket:                      # §3.3 — column names match exactly
    98	    id: str
    99	    title: str                     # <= title_max_chars (200), every write path
   100	    state: TicketState
   101	    priority: Priority             # default P3
   102	    deadline: str | None           # ISO date
   103	    project: Project | None        # NULL when parented (derived)
   104	    sprint_item_id: str | None
   105	    sprint_id: str | None          # writable only when sprint_item_id IS NULL
   106	    recap: str                     # writable only past needs_success
   107	    ceiling: TicketState           # default needs_success (R2); restricted to STATE_ORDER
   108	    at_cap: AtCap                  # default propose (R2)
   109	    auto_blocked: bool
   110	    consecutive_failures: int      # §7.5
   111	    chat_session_key: str | None
   112	    alias: str | None              # migration "Ticket ID:" (§12), unique when present
   113	    fields: TicketFields
   114	    claim_lock: str | None         # §7.3 claim token; NULL = unclaimed
   115	    claim_expires: int | None
   116	    created_at: int
   117	    updated_at: int

exec
/bin/zsh -lc 'nl -ba src/planner/seed/contracts.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Seed/migration shapes: the §12 mapping tables (targeting the real enums), the
     2	parsed intermediates the parser emits, and the migration report. Stdlib only."""
     3	
     4	from __future__ import annotations
     5	
     6	from dataclasses import dataclass, field
     7	from typing import Final
     8	
     9	from planner.core.contracts import Priority, Project
    10	from planner.sprints.contracts import ItemStatus
    11	from planner.tickets.contracts import TicketState
    12	
    13	# §12 mapping tables — exact source strings on the left.
    14	ITEM_STATUS_MAP: Final[dict[str, ItemStatus]] = {
    15	    "Todo": ItemStatus.todo,
    16	    "In Progress": ItemStatus.active,
    17	    "Done": ItemStatus.done,
    18	    "Blocked": ItemStatus.blocked,
    19	    "Deferred": ItemStatus.deferred_next_sprint,
    20	}
    21	
    22	READINESS_MAP: Final[dict[str, TicketState]] = {
    23	    "Concepts": TicketState.needs_success,
    24	    "Needs Shaping": TicketState.needs_approach,
    25	    "Ready": TicketState.needs_plan,
    26	    "In Progress": TicketState.in_progress,
    27	}
    28	
    29	# `Priority:` labels are literal P0..P3 in the source (verified in the snapshot) — identity map.
    30	PRIORITY_MAP: Final[dict[str, Priority]] = {p.value: p for p in Priority}
    31	
    32	# `Urgency:` is present-but-empty throughout the snapshot. Mapped as a fallback only:
    33	# consulted when a Priority label is absent; empty/unknown values are ignored (P3 default applies).
    34	URGENCY_MAP: Final[dict[str, Priority]] = {
    35	    "critical": Priority.P0, "high": Priority.P1, "medium": Priority.P2, "low": Priority.P3,
    36	}   # keys compared case-insensitively
    37	
    38	# Project headings/labels are the enum's exact strings — identity map.
    39	PROJECT_MAP: Final[dict[str, Project]] = {p.value: p for p in Project}
    40	
    41	# `Mode:` is dropped (§12); the parser records it nowhere but it is NOT a skipped
    42	# section (it is a recognised, deliberately-discarded field).
    43	
    44	
    45	# --- parsed intermediates (parser output, importer input; no DB types) ---
    46	
    47	@dataclass
    48	class ParsedSprint:
    49	    name: str
    50	    date_start: str
    51	    date_end: str
    52	    limiting_factor: str = ""
    53	    primary_bet: str = ""
    54	    supports: str = ""
    55	    premortem: str = ""
    56	    outcomes: str = ""
    57	    solo_reflection: str = ""
    58	    joint_discussion: str = ""
    59	    updates_to_thinking: str = ""
    60	    carry_forward: str = ""
    61	
    62	
    63	@dataclass
    64	class ParsedItem:                  # sprint-tracking.md item or deferred.md item
    65	    title: str
    66	    status: ItemStatus
    67	    priority: Priority
    68	    project: Project
    69	    body: str = ""
    70	    deadline: str | None = None
    71	    deferred: bool = False         # True -> sprint_id stays NULL
    72	
    73	
    74	@dataclass
    75	class ParsedTicket:                # workspace.md ticket
    76	    title: str
    77	    state: TicketState
    78	    priority: Priority
    79	    alias: str | None = None       # "Ticket ID:"
    80	    chat_session_key: str | None = None   # "Chat ID:"
    81	    body: str = ""                 # -> fields.success/approach values per §12
    82	    success: str | None = None
    83	    approach: str | None = None
    84	    item_title: str | None = None  # link target for unambiguous title match
    85	
    86	
    87	@dataclass
    88	class ParsedIdea:
    89	    title: str
    90	    body: str = ""
    91	    project: Project | None = None
    92	
    93	
    94	@dataclass(frozen=True)
    95	class SkippedSection:              # §12: silent drops forbidden — every skip is enumerated
    96	    source_file: str               # path relative to the seed source dir
    97	    heading: str | None            # nearest heading, if any
    98	    reason: str                    # human-readable why it could not be parsed
    99	    excerpt: str                   # first ~120 chars of the skipped text
   100	
   101	
   102	@dataclass
   103	class MigrationReport:             # printed, and structured under --json
   104	    sprints: int = 0
   105	    sprint_items: int = 0          # items with a sprint
   106	    deferred_items: int = 0        # items imported with sprint_id NULL
   107	    tickets: int = 0
   108	    ideas: int = 0
   109	    links: int = 0                 # belongs_to links made by title match
   110	    duplicates_skipped: int = 0    # idempotent re-run hits (alias/title match)
   111	    skipped: list[SkippedSection] = field(default_factory=list)

exec
/bin/zsh -lc 'nl -ba src/planner/dispatch/contracts.py' in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc 'nl -ba src/planner/days/contracts.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Dispatch domain shapes: run rows, run statuses, and the pure input row for the
     2	eligibility + ordering functions (§7.2). Stdlib only; enums imported from their
     3	home domains."""
     4	
     5	from __future__ import annotations
     6	
     7	from dataclasses import dataclass
     8	from enum import StrEnum
     9	from typing import Final
    10	
    11	from planner.core.contracts import Priority
    12	from planner.tickets.contracts import AtCap, TicketState
    13	
    14	
    15	class RunStatus(StrEnum):          # §7.3 six + spawn_failed (§7.5)
    16	    running = "running"
    17	    done = "done"
    18	    blocked = "blocked"
    19	    crashed = "crashed"
    20	    timed_out = "timed_out"
    21	    reclaimed = "reclaimed"
    22	    spawn_failed = "spawn_failed"
    23	
    24	
    25	# Statuses that count as failures for the circuit breaker (§7.5).
    26	FAILURE_STATUSES: Final[frozenset[RunStatus]] = frozenset({
    27	    RunStatus.crashed, RunStatus.timed_out, RunStatus.spawn_failed,
    28	})
    29	
    30	# Outcomes an agent may pass to `plan run close --outcome` (terminal, agent-reportable).
    31	AGENT_CLOSE_OUTCOMES: Final[frozenset[RunStatus]] = frozenset({
    32	    RunStatus.done, RunStatus.blocked,
    33	})
    34	
    35	
    36	@dataclass
    37	class Run:                         # §7.3 runs row — column names match
    38	    id: str                        # run_<slug>
    39	    ticket_id: str
    40	    status: RunStatus
    41	    started_at: int
    42	    ended_at: int | None
    43	    summary: str | None
    44	    error: str | None
    45	    pid: int | None
    46	
    47	
    48	@dataclass(frozen=True)
    49	class DispatchCandidate:
    50	    """Input row for the pure eligibility + ordering functions (§7.2).
    51	    Assembled by the data layer; logic never touches the DB."""
    52	
    53	    ticket_id: str
    54	    state: TicketState
    55	    priority: Priority
    56	    deadline: str | None           # ISO date, NULLs ordered last
    57	    created_at: int
    58	    ceiling: TicketState
    59	    at_cap: AtCap
    60	    auto_blocked: bool
    61	    has_active_claim: bool         # claim_lock set and unexpired
    62	    is_blocked: bool               # derived from links (§3.6)
    63	    gating_pending: bool           # current gating field has a pending proposal

 succeeded in 0ms:
     1	"""Day domain shapes: the day row, the day-plan tree (one level of children in
     2	v1), and the planning-date function signature. Stdlib only."""
     3	
     4	from __future__ import annotations
     5	
     6	from collections.abc import Callable
     7	from dataclasses import dataclass, field
     8	from datetime import date, datetime
     9	from enum import StrEnum
    10	
    11	
    12	class NodeStatus(StrEnum):         # §6.3
    13	    proposed = "proposed"
    14	    accepted = "accepted"
    15	    invalidated = "invalidated"
    16	
    17	
    18	@dataclass
    19	class PlanRoot:                    # §6.3 root node
    20	    focus: str
    21	    status: NodeStatus = NodeStatus.proposed
    22	
    23	
    24	@dataclass
    25	class PlanNode:                    # §6.3 child node
    26	    ticket_id: str | None
    27	    note: str
    28	    status: NodeStatus
    29	    position: int
    30	
    31	
    32	@dataclass
    33	class PlanTree:                    # days.plan JSON column: {root, children}
    34	    root: PlanRoot
    35	    children: list[PlanNode] = field(default_factory=list)   # one level in v1
    36	
    37	
    38	@dataclass
    39	class Day:                         # §3.4
    40	    id: str                        # day_YYYY-MM-DD (planning date)
    41	    brief: str
    42	    notes: str
    43	    plan: PlanTree | None
    44	    chat_session_key: str | None
    45	    created_at: int
    46	    updated_at: int
    47	
    48	
    49	@dataclass(frozen=True)
    50	class DayTicket:                   # day_tickets row (§3.4)
    51	    day_id: str
    52	    ticket_id: str
    53	    position: int                  # contiguous from 0
    54	
    55	
    56	# Planning-date math (§6.1): implemented in days/logic/dates.py at stage 3.
    57	# Signature is the contract: planning_date(now, boundary_hour) -> calendar date of
    58	# (now - boundary_hour hours).
    59	PlanningDateFn = Callable[[datetime, int], date]

exec
/bin/zsh -lc 'nl -ba src/planner/chat/contracts.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Chat domain shapes: messages, the gateway send result, and the availability
     2	signal for the panel (§11). Stdlib only."""
     3	
     4	from __future__ import annotations
     5	
     6	from dataclasses import dataclass
     7	
     8	
     9	@dataclass(frozen=True)
    10	class ChatMessage:
    11	    role: str                      # "user" | "assistant"
    12	    text: str
    13	    created_at: int
    14	
    15	
    16	@dataclass(frozen=True)
    17	class ChatSendResult:              # what the gateway adapter returns per send
    18	    reply_text: str
    19	    session_key: str               # persisted onto the entity's chat_session_key
    20	
    21	
    22	@dataclass(frozen=True)
    23	class GatewayStatus:               # availability signal for the panel (§11)
    24	    available: bool
    25	    detail: str | None = None      # e.g. "connection refused" — logged, not shown

codex
The contract files look close to the amended plan so far, including core-owned `Priority`/`Project` and stdlib-only contracts. I’m checking config loading, DB creation, event limits, server startup assertions, and CLI body semantics next.
exec
/bin/zsh -lc 'nl -ba src/planner/core/config.py' in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc 'nl -ba src/planner/core/errors.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Structured errors. Every domain rejection raises PlannerError with a stable
     2	code; the API renders it as {"error": {code, message, detail}} and the CLI keys
     3	its exit code off the presence of that envelope."""
     4	
     5	from __future__ import annotations
     6	
     7	from enum import StrEnum
     8	
     9	from planner.core.contracts import JsonDict
    10	
    11	
    12	class ErrorCode(StrEnum):
    13	    frozen_write = "frozen_write"                  # §5 kickoff/review writes after freeze
    14	    at_cap_stop = "at_cap_stop"                    # §4.3 agent proposal at ceiling with stop
    15	    grant_missing = "grant_missing"                # §4.4.7 accept without the full pair
    16	    grant_invalid = "grant_invalid"                # next_ceiling before the new state / unknown
    17	    stale_claim = "stale_claim"                    # §7.6 stale/foreign claim; detail names it
    18	    recap_too_early = "recap_too_early"            # §3.3 recap write at needs_success
    19	    title_too_long = "title_too_long"              # §3.3 > title_max_chars
    20	    sprint_overlap = "sprint_overlap"              # §3.1 overlapping date ranges
    21	    link_cycle = "link_cycle"                      # §3.6 blocks/parent_child transitive cycle
    22	    link_invalid = "link_invalid"                  # self-link, second belongs_to, bad endpoints
    23	    sprint_derived = "sprint_derived"              # §3.3 sprint_id write on a parented ticket
    24	    item_transition_forbidden = "item_transition_forbidden"  # §3.2 agent direct done/deferred
    25	    agent_forbidden = "agent_forbidden"            # claim/agent request hits a human-only action
    26	    gateway_offline = "gateway_offline"            # §11
    27	    db_not_empty = "db_not_empty"                  # §12 seed --demo on a non-empty DB
    28	    not_found = "not_found"
    29	    validation = "validation"                      # generic input validation
    30	
    31	
    32	class PlannerError(Exception):
    33	    def __init__(self, code: ErrorCode, message: str, detail: JsonDict | None = None) -> None:
    34	        super().__init__(message)
    35	        self.code = code
    36	        self.message = message
    37	        self.detail: JsonDict = detail if detail is not None else {}
    38	
    39	    def to_payload(self) -> JsonDict:
    40	        return {"error": {"code": self.code.value, "message": self.message, "detail": self.detail}}

 succeeded in 0ms:
     1	"""Configuration: every tunable lives here, loaded from config.yaml with PLAN_*
     2	environment overrides. Test-mode flags are environment-only and never in the
     3	checked-in yaml. Nothing in the system reads a timing/limit/path inline."""
     4	
     5	from __future__ import annotations
     6	
     7	import os
     8	from collections.abc import Mapping
     9	from dataclasses import dataclass
    10	from pathlib import Path
    11	from typing import Final
    12	
    13	import yaml
    14	
    15	from planner.core.errors import ErrorCode, PlannerError
    16	
    17	HOST: Final = "127.0.0.1"                    # §2: the bind is fixed, not tunable
    18	DEFAULT_CONFIG_PATH: Final = "config.yaml"
    19	
    20	_TRUE: Final = frozenset({"1", "true", "yes", "on"})
    21	_FALSE: Final = frozenset({"0", "false", "no", "off"})
    22	
    23	
    24	@dataclass(frozen=True)
    25	class Config:
    26	    # §13 named keys
    27	    db_path: str
    28	    port: int
    29	    boundary_hour: int
    30	    tick_seconds: int
    31	    claim_ttl_seconds: int
    32	    max_runs: int
    33	    failure_limit: int
    34	    dispatch_enabled: bool
    35	    hermes_bin: str
    36	    hermes_profile: str
    37	    worker_skill: str
    38	    # other tunables named across SPEC
    39	    ws_poll_ms: int
    40	    ui_debounce_ms: int
    41	    run_max_seconds: int
    42	    boundary_timeout_seconds: int
    43	    title_max_chars: int
    44	    dispatcher_lock_path: str
    45	    logs_dir: str
    46	    events_read_limit: int
    47	    db_busy_timeout_ms: int
    48	    # adapter selection (registry §9.4)
    49	    spawn_adapter: str
    50	    boundary_adapter: str
    51	    gateway_adapter: str
    52	    # test mode — ENV ONLY, never in config.yaml
    53	    test_mode: bool
    54	    fake_now: str | None
    55	
    56	
    57	def _parse_bool(raw: object, key: str) -> bool:
    58	    if isinstance(raw, bool):
    59	        return raw
    60	    text = str(raw).strip().lower()
    61	    if text in _TRUE:
    62	        return True
    63	    if text in _FALSE:
    64	        return False
    65	    raise PlannerError(ErrorCode.validation, f"invalid boolean for {key}: {raw!r}")
    66	
    67	
    68	def _parse_int(raw: object, key: str) -> int:
    69	    if isinstance(raw, bool):
    70	        raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}")
    71	    if isinstance(raw, int):
    72	        return raw
    73	    if isinstance(raw, str):
    74	        try:
    75	            return int(raw.strip())
    76	        except ValueError as exc:
    77	            raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}") from exc
    78	    raise PlannerError(ErrorCode.validation, f"invalid integer for {key}: {raw!r}")
    79	
    80	
    81	def _read_yaml(path: str | None) -> dict[str, object]:
    82	    resolved = Path(path) if path is not None else Path(DEFAULT_CONFIG_PATH)
    83	    if not resolved.exists():
    84	        return {}
    85	    loaded = yaml.safe_load(resolved.read_text())
    86	    if loaded is None:
    87	        return {}
    88	    if not isinstance(loaded, dict):
    89	        raise PlannerError(ErrorCode.validation, f"config root must be a mapping: {resolved}")
    90	    return loaded
    91	
    92	
    93	def _str_value(
    94	    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str, default: str
    95	) -> str:
    96	    value: object = default
    97	    if key in file_cfg:
    98	        value = file_cfg[key]
    99	    if env_var in env:
   100	        value = env[env_var]
   101	    return str(value)
   102	
   103	
   104	def _int_value(
   105	    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str, default: int
   106	) -> int:
   107	    value: object = default
   108	    if key in file_cfg:
   109	        value = file_cfg[key]
   110	    if env_var in env:
   111	        value = env[env_var]
   112	    return _parse_int(value, key)
   113	
   114	
   115	def _bool_value(
   116	    file_cfg: Mapping[str, object], env: Mapping[str, str], key: str, env_var: str, default: bool
   117	) -> bool:
   118	    value: object = default
   119	    if key in file_cfg:
   120	        value = file_cfg[key]
   121	    if env_var in env:
   122	        value = env[env_var]
   123	    return _parse_bool(value, key)
   124	
   125	
   126	def load_config(path: str | None = None, env: Mapping[str, str] | None = None) -> Config:
   127	    env = os.environ if env is None else env
   128	    cfg = _read_yaml(path)
   129	
   130	    test_mode = _parse_bool(env.get("PLAN_TEST_MODE", "0"), "test_mode")
   131	    fake_now: str | None = env.get("PLAN_FAKE_NOW") or None
   132	    if not test_mode:
   133	        fake_now = None  # §13/item 21: PLAN_FAKE_NOW is ignored when test mode is off
   134	
   135	    return Config(
   136	        db_path=_str_value(cfg, env, "db_path", "PLAN_DB_PATH", "data/planning.db"),
   137	        port=_int_value(cfg, env, "port", "PLAN_PORT", 8767),
   138	        boundary_hour=_int_value(cfg, env, "boundary_hour", "PLAN_BOUNDARY_HOUR", 5),
   139	        tick_seconds=_int_value(cfg, env, "tick_seconds", "PLAN_TICK_SECONDS", 60),
   140	        claim_ttl_seconds=_int_value(cfg, env, "claim_ttl_seconds", "PLAN_CLAIM_TTL_SECONDS", 900),
   141	        max_runs=_int_value(cfg, env, "max_runs", "PLAN_MAX_RUNS", 2),
   142	        failure_limit=_int_value(cfg, env, "failure_limit", "PLAN_FAILURE_LIMIT", 2),
   143	        dispatch_enabled=_bool_value(cfg, env, "dispatch_enabled", "PLAN_DISPATCH_ENABLED", True),
   144	        hermes_bin=_str_value(cfg, env, "hermes_bin", "PLAN_HERMES_BIN", "hermes"),
   145	        hermes_profile=_str_value(cfg, env, "hermes_profile", "PLAN_HERMES_PROFILE", "default"),
   146	        worker_skill=_str_value(cfg, env, "worker_skill", "PLAN_WORKER_SKILL", "planning-worker"),
   147	        ws_poll_ms=_int_value(cfg, env, "ws_poll_ms", "PLAN_WS_POLL_MS", 300),
   148	        ui_debounce_ms=_int_value(cfg, env, "ui_debounce_ms", "PLAN_UI_DEBOUNCE_MS", 250),
   149	        run_max_seconds=_int_value(cfg, env, "run_max_seconds", "PLAN_RUN_MAX_SECONDS", 1800),
   150	        boundary_timeout_seconds=_int_value(
   151	            cfg, env, "boundary_timeout_seconds", "PLAN_BOUNDARY_TIMEOUT_SECONDS", 60
   152	        ),
   153	        title_max_chars=_int_value(cfg, env, "title_max_chars", "PLAN_TITLE_MAX_CHARS", 200),
   154	        dispatcher_lock_path=_str_value(
   155	            cfg, env, "dispatcher_lock_path", "PLAN_DISPATCHER_LOCK_PATH", "data/dispatcher.lock"
   156	        ),
   157	        logs_dir=_str_value(cfg, env, "logs_dir", "PLAN_LOGS_DIR", "data/logs"),
   158	        events_read_limit=_int_value(cfg, env, "events_read_limit", "PLAN_EVENTS_READ_LIMIT", 500),
   159	        db_busy_timeout_ms=_int_value(
   160	            cfg, env, "db_busy_timeout_ms", "PLAN_DB_BUSY_TIMEOUT_MS", 5000
   161	        ),
   162	        spawn_adapter=_str_value(cfg, env, "spawn_adapter", "PLAN_SPAWN_ADAPTER", "auto"),
   163	        boundary_adapter=_str_value(cfg, env, "boundary_adapter", "PLAN_BOUNDARY_ADAPTER", "auto"),
   164	        gateway_adapter=_str_value(cfg, env, "gateway_adapter", "PLAN_GATEWAY_ADAPTER", "auto"),
   165	        test_mode=test_mode,
   166	        fake_now=fake_now,
   167	    )
   168	
   169	
   170	def read_dispatch_enabled(path: str | None = None, env: Mapping[str, str] | None = None) -> bool:
   171	    """§7.1 fail-safe: the dispatcher re-reads this every tick. Any read/parse
   172	    failure returns False (a missing config file is not a failure — defaults apply)."""
   173	    try:
   174	        env = os.environ if env is None else env
   175	        return _bool_value(_read_yaml(path), env, "dispatch_enabled", "PLAN_DISPATCH_ENABLED", True)
   176	    except Exception:
   177	        return False

exec
/bin/zsh -lc 'nl -ba src/planner/core/server.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""FastAPI app factory shell. Mounts the domain routers under /api, serves the
     2	static assets and the app shell, exposes GET /api/meta and the event WebSocket,
     3	and — only in test mode — the /api/test/* control endpoints. The resolution
     4	engine, dispatcher, and real route bodies land in later stages; here the surface
     5	and the wiring are fixed.
     6	
     7	This module and the domain api.py files are the only places FastAPI/pydantic
     8	appear."""
     9	
    10	from __future__ import annotations
    11	
    12	import sqlite3
    13	from collections.abc import Callable
    14	from typing import Any
    15	
    16	from fastapi import FastAPI, Request, WebSocket
    17	from fastapi.responses import HTMLResponse, JSONResponse
    18	from fastapi.staticfiles import StaticFiles
    19	
    20	from planner.chat.api import router as chat_router
    21	from planner.core.adapters.registry import Adapters
    22	from planner.core.clock import Clock
    23	from planner.core.config import Config
    24	from planner.core.errors import ErrorCode, PlannerError
    25	from planner.days.api import router as days_router
    26	from planner.dispatch.api import router as dispatch_router
    27	from planner.seed.api import router as seed_router
    28	from planner.sprints.api import router as sprints_router
    29	from planner.tickets.api import router as tickets_router
    30	
    31	_STATUS_BY_CODE: dict[ErrorCode, int] = {
    32	    ErrorCode.not_found: 404,
    33	    ErrorCode.stale_claim: 409,
    34	    ErrorCode.gateway_offline: 503,
    35	}
    36	
    37	_SHELL = "<!doctype html><meta charset=utf-8><title>planner</title><div id=app></div>"
    38	
    39	
    40	def http_status_for(code: ErrorCode) -> int:
    41	    return _STATUS_BY_CODE.get(code, 400)
    42	
    43	
    44	def create_app(
    45	    config: Config,
    46	    clock: Clock,
    47	    adapters: Adapters,
    48	    conn_factory: Callable[[], sqlite3.Connection],
    49	) -> FastAPI:
    50	    # The DDL carries the literal 200 title cap; the config value is what write
    51	    # paths enforce. Guard that they agree (amendment 11).
    52	    assert config.title_max_chars == 200, "title_max_chars must equal the DDL literal (200)"
    53	
    54	    app = FastAPI(title="planner", version="2.0.0")
    55	    app.state.config = config
    56	    app.state.clock = clock
    57	    app.state.adapters = adapters
    58	    app.state.conn_factory = conn_factory
    59	
    60	    @app.exception_handler(PlannerError)
    61	    async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
    62	        return JSONResponse(status_code=http_status_for(exc.code), content=exc.to_payload())
    63	
    64	    for domain_router in (
    65	        tickets_router,
    66	        sprints_router,
    67	        days_router,
    68	        dispatch_router,
    69	        seed_router,
    70	        chat_router,
    71	    ):
    72	        app.include_router(domain_router, prefix="/api")
    73	
    74	    @app.get("/api/meta")
    75	    async def meta() -> dict[str, Any]:
    76	        return {
    77	            "ui_debounce_ms": config.ui_debounce_ms,
    78	            "ws_poll_ms": config.ws_poll_ms,
    79	            "test_mode": config.test_mode,
    80	        }
    81	
    82	    @app.websocket("/api/events")
    83	    async def events_ws(websocket: WebSocket, since: int = 0) -> None:
    84	        # The event tailer lands in stage 4; the path and `since` query are the
    85	        # contract. Accept and close immediately for now.
    86	        await websocket.accept()
    87	        await websocket.close()
    88	
    89	    @app.get("/", response_class=HTMLResponse)
    90	    async def index() -> str:
    91	        return _SHELL
    92	
    93	    if config.test_mode:
    94	
    95	        @app.post("/api/test/tick-boundary")
    96	        async def tick_boundary() -> dict[str, Any]:
    97	            raise NotImplementedError
    98	
    99	        @app.post("/api/test/tick-dispatcher")
   100	        async def tick_dispatcher() -> dict[str, Any]:
   101	            raise NotImplementedError
   102	
   103	        @app.post("/api/test/set-now")
   104	        async def set_now(body: dict[str, Any]) -> dict[str, Any]:
   105	            raise NotImplementedError
   106	
   107	    app.mount("/assets", StaticFiles(directory="assets"), name="assets")
   108	    return app

exec
/bin/zsh -lc 'nl -ba src/planner/core/db.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Database connection and schema. WAL mode, foreign keys on, one canonical DDL.
     2	Booleans are INTEGER 0/1; JSON is TEXT holding canonical JSON; all times are
     3	INTEGER unix seconds; all dates are TEXT ISO. Column names match the contract
     4	dataclass field names one-for-one."""
     5	
     6	from __future__ import annotations
     7	
     8	import sqlite3
     9	from typing import Final
    10	
    11	SCHEMA_VERSION: Final = 1
    12	
    13	DDL: Final = """
    14	CREATE TABLE IF NOT EXISTS sprints (
    15	  id                  TEXT PRIMARY KEY,              -- sp_<slug>
    16	  name                TEXT NOT NULL,
    17	  date_start          TEXT NOT NULL,                 -- ISO date, inclusive
    18	  date_end            TEXT NOT NULL,                 -- ISO date, inclusive
    19	  limiting_factor     TEXT NOT NULL DEFAULT '',
    20	  primary_bet         TEXT NOT NULL DEFAULT '',
    21	  supports            TEXT NOT NULL DEFAULT '',
    22	  premortem           TEXT NOT NULL DEFAULT '',
    23	  weekly_addenda      TEXT NOT NULL DEFAULT '[]',    -- JSON list[Addendum], append-only
    24	  kickoff_frozen_at   INTEGER,
    25	  outcomes            TEXT NOT NULL DEFAULT '',
    26	  solo_reflection     TEXT NOT NULL DEFAULT '',
    27	  joint_discussion    TEXT NOT NULL DEFAULT '',
    28	  updates_to_thinking TEXT NOT NULL DEFAULT '',
    29	  carry_forward       TEXT NOT NULL DEFAULT '',
    30	  review_frozen_at    INTEGER,
    31	  created_at          INTEGER NOT NULL,
    32	  updated_at          INTEGER NOT NULL,
    33	  CHECK (date_start <= date_end)
    34	);
    35	
    36	CREATE TABLE IF NOT EXISTS sprint_items (
    37	  id                  TEXT PRIMARY KEY,              -- si_<slug>
    38	  title               TEXT NOT NULL,
    39	  body                TEXT NOT NULL DEFAULT '',
    40	  status              TEXT NOT NULL DEFAULT 'todo'
    41	                      CHECK (status IN ('todo','active','done','blocked','deferred_next_sprint')),
    42	  priority            TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
    43	  deadline            TEXT,
    44	  project             TEXT NOT NULL CHECK (project IN ('Vylo','Tribe','Learning','Other')),
    45	  current_state_note  TEXT NOT NULL DEFAULT '',
    46	  sprint_id           TEXT REFERENCES sprints(id),   -- NULL = backlog/deferred
    47	  blocked_by          TEXT NOT NULL DEFAULT '[]',    -- JSON list[str] of ticket ids
    48	  status_proposal     TEXT,                          -- JSON ItemStatusProposal | NULL
    49	  created_at          INTEGER NOT NULL,
    50	  updated_at          INTEGER NOT NULL
    51	);
    52	
    53	CREATE TABLE IF NOT EXISTS tickets (
    54	  id                   TEXT PRIMARY KEY,             -- t_<slug>
    55	  title                TEXT NOT NULL CHECK (length(title) <= 200),
    56	  state                TEXT NOT NULL DEFAULT 'needs_success'
    57	                       CHECK (state IN ('needs_success','needs_approach','needs_plan',
    58	                                        'in_progress','needs_review','done','dropped')),
    59	  priority             TEXT NOT NULL DEFAULT 'P3' CHECK (priority IN ('P0','P1','P2','P3')),
    60	  deadline             TEXT,
    61	  project              TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- NULL when parented
    62	  sprint_item_id       TEXT REFERENCES sprint_items(id),
    63	  sprint_id            TEXT REFERENCES sprints(id),  -- writable only when sprint_item_id IS NULL
    64	  recap                TEXT NOT NULL DEFAULT '',
    65	  ceiling              TEXT NOT NULL DEFAULT 'needs_success'
    66	                       CHECK (ceiling IN ('needs_success','needs_approach','needs_plan',
    67	                                          'in_progress','needs_review','done')),
    68	  at_cap               TEXT NOT NULL DEFAULT 'propose' CHECK (at_cap IN ('stop','propose')),
    69	  auto_blocked         INTEGER NOT NULL DEFAULT 0,
    70	  consecutive_failures INTEGER NOT NULL DEFAULT 0,   -- §7.5
    71	  chat_session_key     TEXT,
    72	  alias                TEXT,                         -- migration "Ticket ID:" (§12)
    73	  fields               TEXT NOT NULL DEFAULT '{"success":{"value":null,"proposal":null,"notes":null},"approach":{"value":null,"proposal":null,"notes":null},"plan":{"value":null,"proposal":null,"notes":null},"result":{"value":null,"proposal":null,"notes":null}}',
    74	  claim_lock           TEXT,                         -- §7.3 claim token; NULL = unclaimed
    75	  claim_expires        INTEGER,
    76	  created_at           INTEGER NOT NULL,
    77	  updated_at           INTEGER NOT NULL
    78	);
    79	CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_alias ON tickets(alias) WHERE alias IS NOT NULL;
    80	CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state);
    81	
    82	CREATE TABLE IF NOT EXISTS days (
    83	  id               TEXT PRIMARY KEY,                 -- day_YYYY-MM-DD (planning date, §3.4)
    84	  brief            TEXT NOT NULL DEFAULT '',
    85	  notes            TEXT NOT NULL DEFAULT '',
    86	  plan             TEXT,                             -- JSON PlanTree | NULL
    87	  chat_session_key TEXT,
    88	  created_at       INTEGER NOT NULL,
    89	  updated_at       INTEGER NOT NULL
    90	);
    91	
    92	CREATE TABLE IF NOT EXISTS day_tickets (
    93	  day_id    TEXT NOT NULL REFERENCES days(id),
    94	  ticket_id TEXT NOT NULL REFERENCES tickets(id),
    95	  position  INTEGER NOT NULL CHECK (position >= 0),  -- contiguous from 0, writer-enforced
    96	  PRIMARY KEY (day_id, ticket_id)
    97	);
    98	
    99	CREATE TABLE IF NOT EXISTS ideas (
   100	  id         TEXT PRIMARY KEY,                       -- idea_<slug>
   101	  title      TEXT NOT NULL,
   102	  body       TEXT NOT NULL DEFAULT '',
   103	  project    TEXT CHECK (project IN ('Vylo','Tribe','Learning','Other')),  -- nullable (§3.5)
   104	  created_at INTEGER NOT NULL,
   105	  updated_at INTEGER NOT NULL
   106	);
   107	
   108	CREATE TABLE IF NOT EXISTS links (                   -- §3.6, exactly the spec's three columns
   109	  from_id TEXT NOT NULL,
   110	  to_id   TEXT NOT NULL,
   111	  kind    TEXT NOT NULL CHECK (kind IN ('belongs_to','parent_child','blocks','relates')),
   112	  PRIMARY KEY (from_id, to_id, kind),
   113	  CHECK (from_id <> to_id)
   114	);
   115	CREATE UNIQUE INDEX IF NOT EXISTS idx_links_one_belongs_to
   116	  ON links(from_id) WHERE kind = 'belongs_to';       -- at most one belongs_to per ticket
   117	CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_id, kind);
   118	
   119	CREATE TABLE IF NOT EXISTS events (                  -- §3, append-only
   120	  id         INTEGER PRIMARY KEY AUTOINCREMENT,
   121	  entity_id  TEXT NOT NULL,
   122	  kind       TEXT NOT NULL,
   123	  payload    TEXT NOT NULL DEFAULT '{}',             -- JSON
   124	  created_at INTEGER NOT NULL
   125	);
   126	CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id, id);
   127	
   128	CREATE TABLE IF NOT EXISTS runs (                    -- §7.3
   129	  id         TEXT PRIMARY KEY,                       -- run_<slug>
   130	  ticket_id  TEXT NOT NULL REFERENCES tickets(id),
   131	  status     TEXT NOT NULL
   132	             CHECK (status IN ('running','done','blocked','crashed','timed_out','reclaimed','spawn_failed')),
   133	  started_at INTEGER NOT NULL,
   134	  ended_at   INTEGER,
   135	  summary    TEXT,
   136	  error      TEXT,
   137	  pid        INTEGER
   138	);
   139	CREATE INDEX IF NOT EXISTS idx_runs_ticket ON runs(ticket_id, started_at);
   140	
   141	CREATE TABLE IF NOT EXISTS boundary_runs (           -- §6.2: never twice per date
   142	  planning_date TEXT PRIMARY KEY,                    -- ISO date
   143	  ran_at        INTEGER NOT NULL,
   144	  judgment      TEXT NOT NULL CHECK (judgment IN ('ok','skipped','failed'))
   145	);
   146	"""
   147	
   148	
   149	def connect(db_path: str, busy_timeout_ms: int = 5000) -> sqlite3.Connection:
   150	    conn = sqlite3.connect(db_path, isolation_level=None)
   151	    conn.row_factory = sqlite3.Row
   152	    conn.execute("PRAGMA journal_mode=WAL")
   153	    conn.execute("PRAGMA foreign_keys=ON")
   154	    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
   155	    return conn
   156	
   157	
   158	def create_schema(conn: sqlite3.Connection) -> None:
   159	    conn.executescript(DDL)
   160	    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

exec
/bin/zsh -lc 'nl -ba src/planner/core/ids.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Id generation: prefixed random slugs for entities, calendar day ids, and
     2	claim tokens (distinct from run ids so §7.6 validation checks two values)."""
     3	
     4	from __future__ import annotations
     5	
     6	import secrets
     7	from datetime import date
     8	from typing import Final
     9	
    10	ID_PREFIXES: Final[dict[str, str]] = {
    11	    "sprint": "sp",
    12	    "sprint_item": "si",
    13	    "ticket": "t",
    14	    "idea": "idea",
    15	    "run": "run",
    16	}
    17	SLUG_ALPHABET: Final = "0123456789abcdefghjkmnpqrstuvwxyz"  # lowercase, no i/l/o
    18	SLUG_LEN: Final = 8
    19	CLAIM_LEN: Final = 12  # claim tokens are longer than entity slugs
    20	
    21	
    22	def _slug(length: int) -> str:
    23	    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(length))
    24	
    25	
    26	def new_id(prefix: str) -> str:
    27	    return f"{prefix}_{_slug(SLUG_LEN)}"
    28	
    29	
    30	def day_id(planning_date: date) -> str:
    31	    return f"day_{planning_date.isoformat()}"
    32	
    33	
    34	def new_claim() -> str:
    35	    return f"claim_{_slug(CLAIM_LEN)}"

exec
/bin/zsh -lc 'nl -ba src/planner/core/clock.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The clock. Real time in production; a mutable fake in test mode so planning-date
     2	math (§6.1) and the set-now test endpoint (D5) are deterministic. The choice is
     3	made once at startup from config."""
     4	
     5	from __future__ import annotations
     6	
     7	from datetime import datetime
     8	from typing import TYPE_CHECKING, Protocol
     9	
    10	if TYPE_CHECKING:
    11	    from planner.core.config import Config
    12	
    13	
    14	class Clock(Protocol):
    15	    def now(self) -> datetime: ...       # timezone-aware, LOCAL time (§6.1 math is local)
    16	    def now_unix(self) -> int: ...
    17	
    18	
    19	class RealClock:
    20	    def now(self) -> datetime:
    21	        return datetime.now().astimezone()
    22	
    23	    def now_unix(self) -> int:
    24	        return int(self.now().timestamp())
    25	
    26	
    27	class TestClock:
    28	    """Mutable clock for PLAN_FAKE_NOW and POST /api/test/set-now (D5)."""
    29	
    30	    def __init__(self, start: datetime) -> None:
    31	        self._now = start
    32	
    33	    def now(self) -> datetime:
    34	        return self._now
    35	
    36	    def now_unix(self) -> int:
    37	        return int(self._now.timestamp())
    38	
    39	    def set(self, now: datetime) -> None:
    40	        self._now = now
    41	
    42	
    43	def parse_fake_now(raw: str) -> datetime:
    44	    """PLAN_FAKE_NOW is ISO; a naive value is interpreted as local time."""
    45	    parsed = datetime.fromisoformat(raw)
    46	    if parsed.tzinfo is None:
    47	        parsed = parsed.astimezone()
    48	    return parsed
    49	
    50	
    51	def build_clock(config: Config) -> Clock:
    52	    if config.test_mode and config.fake_now:
    53	        return TestClock(parse_fake_now(config.fake_now))
    54	    return RealClock()

exec
/bin/zsh -lc 'nl -ba src/planner/core/events.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The only module that writes or reads the events table. There is no update or
     2	delete path anywhere: events are append-only (§3). The read limit is always
     3	passed in by the caller (from config); this module stays config-free."""
     4	
     5	from __future__ import annotations
     6	
     7	import json
     8	import sqlite3
     9	
    10	from planner.core.contracts import EventKind, EventRow, JsonDict
    11	
    12	
    13	def append_event(
    14	    conn: sqlite3.Connection,
    15	    entity_id: str,
    16	    kind: EventKind,
    17	    payload: JsonDict,
    18	    created_at: int,
    19	) -> int:
    20	    cursor = conn.execute(
    21	        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
    22	        (entity_id, kind.value, json.dumps(payload), created_at),
    23	    )
    24	    inserted = cursor.lastrowid
    25	    assert inserted is not None
    26	    return inserted
    27	
    28	
    29	def read_events_since(conn: sqlite3.Connection, since_id: int, limit: int) -> list[EventRow]:
    30	    rows = conn.execute(
    31	        "SELECT id, entity_id, kind, payload, created_at "
    32	        "FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
    33	        (since_id, limit),
    34	    ).fetchall()
    35	    return [
    36	        EventRow(
    37	            id=row["id"],
    38	            entity_id=row["entity_id"],
    39	            kind=row["kind"],
    40	            payload=json.loads(row["payload"]),
    41	            created_at=row["created_at"],
    42	        )
    43	        for row in rows
    44	    ]

exec
/bin/zsh -lc 'nl -ba src/planner/core/adapters/base.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Adapter protocols and their request/result shapes: the three external
     2	boundaries (spawn subprocess, boundary/replan agent, chat gateway). Stdlib only.
     3	The dependency arrow is core-adapters -> domain-contracts, never the reverse."""
     4	
     5	from __future__ import annotations
     6	
     7	from dataclasses import dataclass
     8	from typing import Any, Protocol
     9	
    10	from planner.chat.contracts import ChatSendResult, GatewayStatus
    11	from planner.days.contracts import PlanNode, PlanTree
    12	
    13	
    14	@dataclass(frozen=True)
    15	class SpawnRequest:                # §7.4 — everything the subprocess boundary needs
    16	    ticket_id: str
    17	    run_id: str
    18	    claim: str
    19	    server_url: str
    20	    log_path: str                  # per-run log file under logs_dir
    21	    hermes_bin: str
    22	    profile: str                   # R3: "default"
    23	    skill: str                     # R3: "planning-worker"
    24	
    25	
    26	@dataclass(frozen=True)
    27	class SpawnResult:
    28	    ok: bool
    29	    pid: int | None = None         # set when ok
    30	    error: str | None = None       # set when not ok -> run status spawn_failed
    31	
    32	
    33	class SpawnAdapter(Protocol):
    34	    def spawn(self, request: SpawnRequest) -> SpawnResult: ...
    35	
    36	
    37	@dataclass(frozen=True)
    38	class BoundaryInputs:              # §6.2 deterministic-pass outputs, DB-internal only (R4)
    39	    planning_date: str             # ISO
    40	    carryover: list[dict[str, Any]]      # ticket digests: {id, title, state, priority}
    41	    overdue: list[dict[str, Any]]        # same digest shape, tickets and items
    42	    approvals_digest: list[dict[str, Any]]   # {entity_id, kind, waiting_since}
    43	
    44	
    45	@dataclass(frozen=True)
    46	class BoundaryJudgment:
    47	    brief_markdown: str
    48	    plan_tree: PlanTree
    49	
    50	
    51	class BoundaryAdapter(Protocol):
    52	    # Timeout (boundary_timeout_seconds, 60s) is owned by the CALLER — the boundary
    53	    # scheduler wraps calls; adapters just do the work or raise.
    54	    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment: ...
    55	    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree: ...
    56	    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode: ...
    57	
    58	
    59	class GatewayAdapter(Protocol):
    60	    def status(self) -> GatewayStatus: ...
    61	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult: ...

exec
/bin/zsh -lc 'nl -ba src/planner/core/adapters/fakes.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""In-memory fakes for the three adapters. Tests use these; they never touch the
     2	OS or the network. Each records its calls and behaves deterministically."""
     3	
     4	from __future__ import annotations
     5	
     6	from dataclasses import dataclass, field
     7	
     8	from planner.chat.contracts import ChatSendResult, GatewayStatus
     9	from planner.core.adapters.base import (
    10	    BoundaryInputs,
    11	    BoundaryJudgment,
    12	    SpawnRequest,
    13	    SpawnResult,
    14	)
    15	from planner.core.errors import ErrorCode, PlannerError
    16	from planner.days.contracts import NodeStatus, PlanNode, PlanRoot, PlanTree
    17	
    18	_FIRST_FAKE_PID = 90001
    19	
    20	
    21	@dataclass
    22	class FakeSpawnAdapter:
    23	    calls: list[SpawnRequest] = field(default_factory=list)
    24	    results: list[SpawnResult] = field(default_factory=list)  # scripted, popped FIFO
    25	    next_pid: int = _FIRST_FAKE_PID
    26	
    27	    def spawn(self, request: SpawnRequest) -> SpawnResult:
    28	        self.calls.append(request)
    29	        if self.results:
    30	            return self.results.pop(0)
    31	        pid = self.next_pid
    32	        self.next_pid += 1
    33	        return SpawnResult(ok=True, pid=pid)
    34	
    35	
    36	@dataclass
    37	class FakeBoundaryAdapter:
    38	    judgment_result: BoundaryJudgment | None = None
    39	    replan_tree: PlanTree | None = None
    40	    replan_child_node: PlanNode | None = None
    41	    fail: bool = False
    42	    calls: list[str] = field(default_factory=list)  # method names, in order
    43	
    44	    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
    45	        self.calls.append("judgment")
    46	        if self.fail:
    47	            raise RuntimeError("fake boundary failure")
    48	        if self.judgment_result is not None:
    49	            return self.judgment_result
    50	        children = [
    51	            PlanNode(
    52	                ticket_id=entry.get("id"),
    53	                note=str(entry.get("title", "")),
    54	                status=NodeStatus.proposed,
    55	                position=index,
    56	            )
    57	            for index, entry in enumerate(inputs.carryover)
    58	        ]
    59	        tree = PlanTree(root=PlanRoot(focus="Fake focus"), children=children)
    60	        return BoundaryJudgment(
    61	            brief_markdown=f"# Brief for {inputs.planning_date}", plan_tree=tree
    62	        )
    63	
    64	    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
    65	        self.calls.append("replan_root")
    66	        if self.fail:
    67	            raise RuntimeError("fake boundary failure")
    68	        if self.replan_tree is not None:
    69	            return self.replan_tree
    70	        return PlanTree(root=PlanRoot(focus="Fake replanned focus"), children=[])
    71	
    72	    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
    73	        self.calls.append("replan_child")
    74	        if self.fail:
    75	            raise RuntimeError("fake boundary failure")
    76	        if self.replan_child_node is not None:
    77	            return self.replan_child_node
    78	        return PlanNode(
    79	            ticket_id=child.ticket_id,
    80	            note="Fake replanned child",
    81	            status=NodeStatus.proposed,
    82	            position=child.position,
    83	        )
    84	
    85	
    86	@dataclass
    87	class EchoGatewayAdapter:
    88	    calls: list[tuple[str | None, str, str]] = field(default_factory=list)
    89	    next_session: int = 1
    90	
    91	    def status(self) -> GatewayStatus:
    92	        return GatewayStatus(available=True)
    93	
    94	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
    95	        self.calls.append((session_key, entity_id, text))
    96	        if session_key is None:
    97	            session_key = f"fake-sess-{self.next_session}"
    98	            self.next_session += 1
    99	        return ChatSendResult(reply_text=f"echo: {text}", session_key=session_key)
   100	
   101	
   102	@dataclass
   103	class OfflineGatewayAdapter:
   104	    def status(self) -> GatewayStatus:
   105	        return GatewayStatus(available=False, detail="gateway offline")
   106	
   107	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
   108	        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

exec
/bin/zsh -lc 'nl -ba src/planner/core/adapters/real.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Real adapter stubs. They exist so the registry, config keys, and types are
     2	final now; the subprocess / tui_gateway.ws logic is wired in stage 4 without
     3	touching any signature."""
     4	
     5	from __future__ import annotations
     6	
     7	from typing import TYPE_CHECKING
     8	
     9	from planner.chat.contracts import ChatSendResult, GatewayStatus
    10	from planner.core.adapters.base import (
    11	    BoundaryInputs,
    12	    BoundaryJudgment,
    13	    SpawnRequest,
    14	    SpawnResult,
    15	)
    16	from planner.days.contracts import PlanNode, PlanTree
    17	
    18	if TYPE_CHECKING:
    19	    from planner.core.config import Config
    20	
    21	_STAGE = "wired in stage 4"
    22	
    23	
    24	class RealSpawnAdapter:
    25	    def __init__(self, config: Config) -> None:
    26	        self._config = config
    27	
    28	    def spawn(self, request: SpawnRequest) -> SpawnResult:
    29	        raise NotImplementedError(_STAGE)
    30	
    31	
    32	class RealBoundaryAdapter:
    33	    def __init__(self, config: Config) -> None:
    34	        self._config = config
    35	
    36	    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
    37	        raise NotImplementedError(_STAGE)
    38	
    39	    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
    40	        raise NotImplementedError(_STAGE)
    41	
    42	    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
    43	        raise NotImplementedError(_STAGE)
    44	
    45	
    46	class RealGatewayAdapter:
    47	    def __init__(self, config: Config) -> None:
    48	        self._config = config
    49	
    50	    def status(self) -> GatewayStatus:
    51	        raise NotImplementedError(_STAGE)
    52	
    53	    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
    54	        raise NotImplementedError(_STAGE)

exec
/bin/zsh -lc 'nl -ba src/planner/__main__.py' in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc 'nl -ba src/planner/core/adapters/__init__.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
 succeeded in 0ms:
     1	from planner.cli.main import main
     2	
     3	if __name__ == "__main__":
     4	    main()

exec
/bin/zsh -lc 'nl -ba src/planner/core/adapters/registry.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Adapter registry (§9.4). Selects real vs fake (vs offline gateway) per adapter
     2	from config keys. `auto` resolves to fake under test mode and real otherwise;
     3	explicit values always win. An unknown value fails loudly at startup."""
     4	
     5	from __future__ import annotations
     6	
     7	from dataclasses import dataclass
     8	from typing import TYPE_CHECKING
     9	
    10	from planner.core.adapters.base import BoundaryAdapter, GatewayAdapter, SpawnAdapter
    11	from planner.core.adapters.fakes import (
    12	    EchoGatewayAdapter,
    13	    FakeBoundaryAdapter,
    14	    FakeSpawnAdapter,
    15	    OfflineGatewayAdapter,
    16	)
    17	from planner.core.adapters.real import (
    18	    RealBoundaryAdapter,
    19	    RealGatewayAdapter,
    20	    RealSpawnAdapter,
    21	)
    22	from planner.core.errors import ErrorCode, PlannerError
    23	
    24	if TYPE_CHECKING:
    25	    from planner.core.config import Config
    26	
    27	
    28	@dataclass(frozen=True)
    29	class Adapters:
    30	    spawn: SpawnAdapter
    31	    boundary: BoundaryAdapter
    32	    gateway: GatewayAdapter
    33	
    34	
    35	def _resolve_auto(choice: str, test_mode: bool) -> str:
    36	    if choice == "auto":
    37	        return "fake" if test_mode else "real"
    38	    return choice
    39	
    40	
    41	def _select_spawn(config: Config) -> SpawnAdapter:
    42	    choice = _resolve_auto(config.spawn_adapter, config.test_mode)
    43	    if choice == "fake":
    44	        return FakeSpawnAdapter()
    45	    if choice == "real":
    46	        return RealSpawnAdapter(config)
    47	    raise PlannerError(ErrorCode.validation, f"unknown spawn_adapter: {config.spawn_adapter!r}")
    48	
    49	
    50	def _select_boundary(config: Config) -> BoundaryAdapter:
    51	    choice = _resolve_auto(config.boundary_adapter, config.test_mode)
    52	    if choice == "fake":
    53	        return FakeBoundaryAdapter()
    54	    if choice == "real":
    55	        return RealBoundaryAdapter(config)
    56	    raise PlannerError(
    57	        ErrorCode.validation, f"unknown boundary_adapter: {config.boundary_adapter!r}"
    58	    )
    59	
    60	
    61	def _select_gateway(config: Config) -> GatewayAdapter:
    62	    choice = _resolve_auto(config.gateway_adapter, config.test_mode)
    63	    if choice == "fake":
    64	        return EchoGatewayAdapter()
    65	    if choice == "offline":
    66	        return OfflineGatewayAdapter()
    67	    if choice == "real":
    68	        return RealGatewayAdapter(config)
    69	    raise PlannerError(ErrorCode.validation, f"unknown gateway_adapter: {config.gateway_adapter!r}")
    70	
    71	
    72	def build_adapters(config: Config) -> Adapters:
    73	    return Adapters(
    74	        spawn=_select_spawn(config),
    75	        boundary=_select_boundary(config),
    76	        gateway=_select_gateway(config),
    77	    )

exec
/bin/zsh -lc 'nl -ba src/planner/__init__.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	__version__ = "2.0.0"

codex
The primitives and adapter layer also match the main contract shape, with `events_read_limit` staying out of `core/events.py` as required. I’m confirming each API route table entry and then the exact Click verb tree against section 13 and 14.
exec
/bin/zsh -lc 'nl -ba src/planner/tickets/api.py' in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc 'nl -ba src/planner/dispatch/api.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Run routes (§9): heartbeat and close. Both require the claim headers, checked
     2	server-side in stage 4. Stubs for now."""
     3	
     4	from __future__ import annotations
     5	
     6	from typing import Any
     7	
     8	from fastapi import APIRouter
     9	
    10	router = APIRouter()
    11	
    12	
    13	@router.post("/runs/{run_id}/heartbeat")
    14	async def heartbeat(run_id: str) -> dict[str, Any]:
    15	    raise NotImplementedError
    16	
    17	
    18	@router.post("/runs/{run_id}/close")
    19	async def close_run(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    20	    raise NotImplementedError

 succeeded in 0ms:
     1	"""Ticket routes (§9), plus the ticket-anchored links and the ticket-centric
     2	derived views (board, queues). Handlers are stubs until the engine lands in
     3	stage 4; the method + path is the contract now."""
     4	
     5	from __future__ import annotations
     6	
     7	from typing import Any
     8	
     9	from fastapi import APIRouter
    10	
    11	router = APIRouter()
    12	
    13	
    14	@router.post("/tickets")
    15	async def create_ticket(body: dict[str, Any]) -> dict[str, Any]:
    16	    raise NotImplementedError
    17	
    18	
    19	@router.get("/tickets")
    20	async def list_tickets(
    21	    state: str | None = None,
    22	    project: str | None = None,
    23	    sprint_id: str | None = None,
    24	    sprint_item_id: str | None = None,
    25	) -> dict[str, Any]:
    26	    raise NotImplementedError
    27	
    28	
    29	@router.get("/tickets/{ticket_id}")
    30	async def get_ticket(ticket_id: str) -> dict[str, Any]:
    31	    raise NotImplementedError
    32	
    33	
    34	@router.patch("/tickets/{ticket_id}")
    35	async def patch_ticket(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    36	    raise NotImplementedError
    37	
    38	
    39	@router.post("/tickets/{ticket_id}/propose/{field}")
    40	async def propose_field(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
    41	    raise NotImplementedError
    42	
    43	
    44	@router.post("/tickets/{ticket_id}/accept/{field}")
    45	async def accept_field(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
    46	    raise NotImplementedError
    47	
    48	
    49	@router.post("/tickets/{ticket_id}/approve")
    50	async def approve_ticket(ticket_id: str) -> dict[str, Any]:
    51	    raise NotImplementedError
    52	
    53	
    54	@router.put("/tickets/{ticket_id}/notes/{field}")
    55	async def put_notes(ticket_id: str, field: str, body: dict[str, Any]) -> dict[str, Any]:
    56	    raise NotImplementedError
    57	
    58	
    59	@router.put("/tickets/{ticket_id}/recap")
    60	async def put_recap(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    61	    raise NotImplementedError
    62	
    63	
    64	@router.post("/tickets/{ticket_id}/grant")
    65	async def grant_ticket(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    66	    raise NotImplementedError
    67	
    68	
    69	@router.post("/tickets/{ticket_id}/state")
    70	async def set_state(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    71	    raise NotImplementedError
    72	
    73	
    74	@router.post("/tickets/{ticket_id}/drop")
    75	async def drop_ticket(ticket_id: str) -> dict[str, Any]:
    76	    raise NotImplementedError
    77	
    78	
    79	@router.post("/tickets/{ticket_id}/unblock")
    80	async def unblock_ticket(ticket_id: str) -> dict[str, Any]:
    81	    raise NotImplementedError
    82	
    83	
    84	@router.get("/tickets/{ticket_id}/events")
    85	async def ticket_events(ticket_id: str) -> dict[str, Any]:
    86	    raise NotImplementedError
    87	
    88	
    89	@router.get("/tickets/{ticket_id}/runs")
    90	async def ticket_runs(ticket_id: str) -> dict[str, Any]:
    91	    raise NotImplementedError
    92	
    93	
    94	@router.get("/tickets/{ticket_id}/copy-text")
    95	async def ticket_copy_text(ticket_id: str) -> dict[str, Any]:
    96	    raise NotImplementedError
    97	
    98	
    99	@router.post("/links")
   100	async def add_link(body: dict[str, Any]) -> dict[str, Any]:
   101	    raise NotImplementedError
   102	
   103	
   104	@router.delete("/links")
   105	async def remove_link(from_id: str, to_id: str, kind: str) -> dict[str, Any]:
   106	    raise NotImplementedError
   107	
   108	
   109	@router.get("/board")
   110	async def board() -> dict[str, Any]:
   111	    raise NotImplementedError
   112	
   113	
   114	@router.get("/queues")
   115	async def queues() -> dict[str, Any]:
   116	    raise NotImplementedError

exec
/bin/zsh -lc 'nl -ba src/planner/seed/api.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Seed route (§9): POST /api/seed runs a migration and returns a MigrationReport.
     2	Stub for now."""
     3	
     4	from __future__ import annotations
     5	
     6	from typing import Any
     7	
     8	from fastapi import APIRouter
     9	
    10	router = APIRouter()
    11	
    12	
    13	@router.post("/seed")
    14	async def seed(body: dict[str, Any]) -> dict[str, Any]:
    15	    raise NotImplementedError

exec
/bin/zsh -lc 'nl -ba src/planner/chat/api.py' in /Users/khushaljagota/.hermes/planning-v2
exec
/bin/zsh -lc 'nl -ba src/planner/sprints/api.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Chat routes (§9): send a message, read gateway availability. Stubs for now."""
     2	
     3	from __future__ import annotations
     4	
     5	from typing import Any
     6	
     7	from fastapi import APIRouter
     8	
     9	router = APIRouter()
    10	
    11	
    12	@router.post("/chat/{entity_id}/send")
    13	async def send_message(entity_id: str, body: dict[str, Any]) -> dict[str, Any]:
    14	    raise NotImplementedError
    15	
    16	
    17	@router.get("/chat/{entity_id}/status")
    18	async def gateway_status(entity_id: str) -> dict[str, Any]:
    19	    raise NotImplementedError

 succeeded in 0ms:
     1	"""Sprint-item, sprint, current-sprint view, and idea routes (§9). Ideas are
     2	homed here because the Idea shape lives in this domain's contracts. Stubs until
     3	stage 4."""
     4	
     5	from __future__ import annotations
     6	
     7	from typing import Any
     8	
     9	from fastapi import APIRouter
    10	
    11	router = APIRouter()
    12	
    13	
    14	@router.post("/items")
    15	async def create_item(body: dict[str, Any]) -> dict[str, Any]:
    16	    raise NotImplementedError
    17	
    18	
    19	@router.get("/items")
    20	async def list_items(
    21	    status: str | None = None,
    22	    project: str | None = None,
    23	    sprint_id: str | None = None,
    24	) -> dict[str, Any]:
    25	    raise NotImplementedError
    26	
    27	
    28	@router.get("/items/{item_id}")
    29	async def get_item(item_id: str) -> dict[str, Any]:
    30	    raise NotImplementedError
    31	
    32	
    33	@router.patch("/items/{item_id}")
    34	async def patch_item(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    35	    raise NotImplementedError
    36	
    37	
    38	@router.post("/items/{item_id}/propose-status")
    39	async def propose_item_status(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    40	    raise NotImplementedError
    41	
    42	
    43	@router.post("/items/{item_id}/accept-status")
    44	async def accept_item_status(item_id: str, body: dict[str, Any]) -> dict[str, Any]:
    45	    raise NotImplementedError
    46	
    47	
    48	@router.post("/sprints")
    49	async def create_sprint(body: dict[str, Any]) -> dict[str, Any]:
    50	    raise NotImplementedError
    51	
    52	
    53	@router.get("/sprints")
    54	async def list_sprints() -> dict[str, Any]:
    55	    raise NotImplementedError
    56	
    57	
    58	@router.get("/sprints/{sprint_id}")
    59	async def get_sprint(sprint_id: str) -> dict[str, Any]:
    60	    raise NotImplementedError
    61	
    62	
    63	@router.patch("/sprints/{sprint_id}")
    64	async def patch_sprint(sprint_id: str, body: dict[str, Any]) -> dict[str, Any]:
    65	    raise NotImplementedError
    66	
    67	
    68	@router.post("/sprints/{sprint_id}/freeze-kickoff")
    69	async def freeze_kickoff(sprint_id: str) -> dict[str, Any]:
    70	    raise NotImplementedError
    71	
    72	
    73	@router.post("/sprints/{sprint_id}/freeze-review")
    74	async def freeze_review(sprint_id: str) -> dict[str, Any]:
    75	    raise NotImplementedError
    76	
    77	
    78	@router.post("/sprints/{sprint_id}/addenda")
    79	async def add_addendum(sprint_id: str, body: dict[str, Any]) -> dict[str, Any]:
    80	    raise NotImplementedError
    81	
    82	
    83	@router.get("/sprint/current")
    84	async def current_sprint() -> dict[str, Any]:
    85	    raise NotImplementedError
    86	
    87	
    88	@router.post("/ideas")
    89	async def create_idea(body: dict[str, Any]) -> dict[str, Any]:
    90	    raise NotImplementedError
    91	
    92	
    93	@router.get("/ideas")
    94	async def list_ideas() -> dict[str, Any]:
    95	    raise NotImplementedError

exec
/bin/zsh -lc 'nl -ba src/planner/days/api.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""Day routes (§9). The {date} path segment accepts the literal `today` (the
     2	server resolves it via the planning date). Stubs until stage 4."""
     3	
     4	from __future__ import annotations
     5	
     6	from typing import Any
     7	
     8	from fastapi import APIRouter
     9	
    10	router = APIRouter()
    11	
    12	
    13	@router.get("/day/{date}")
    14	async def get_day(date: str) -> dict[str, Any]:
    15	    raise NotImplementedError
    16	
    17	
    18	@router.patch("/day/{date}")
    19	async def patch_day(date: str, body: dict[str, Any]) -> dict[str, Any]:
    20	    raise NotImplementedError
    21	
    22	
    23	@router.post("/day/{date}/tickets")
    24	async def add_day_ticket(date: str, body: dict[str, Any]) -> dict[str, Any]:
    25	    raise NotImplementedError
    26	
    27	
    28	@router.delete("/day/{date}/tickets/{ticket_id}")
    29	async def remove_day_ticket(date: str, ticket_id: str) -> dict[str, Any]:
    30	    raise NotImplementedError
    31	
    32	
    33	@router.post("/day/{date}/plan/accept")
    34	async def plan_accept(date: str, body: dict[str, Any]) -> dict[str, Any]:
    35	    raise NotImplementedError
    36	
    37	
    38	@router.post("/day/{date}/plan/accept-all")
    39	async def plan_accept_all(date: str) -> dict[str, Any]:
    40	    raise NotImplementedError
    41	
    42	
    43	@router.post("/day/{date}/plan/invalidate")
    44	async def plan_invalidate(date: str, body: dict[str, Any]) -> dict[str, Any]:
    45	    raise NotImplementedError
    46	
    47	
    48	@router.post("/day/{date}/plan/reject-all")
    49	async def plan_reject_all(date: str) -> dict[str, Any]:
    50	    raise NotImplementedError

exec
/bin/zsh -lc 'nl -ba src/planner/cli/main.py' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
     1	"""The `plan` CLI (§8). A single entry point speaking HTTP to the server. The CLI
     2	is for agents and developer debugging; the human operates through the UI. No
     3	resolution verbs exist here (accept/approve/grant/unblock/day-plan are UI/API
     4	only).
     5	
     6	Every verb supports --json (machine output; exit codes 0 success, 1
     7	validation/domain error, 2 connection error). Long text arrives via
     8	`--body-file <path>` where `-` reads stdin — body text is never an inline
     9	argument. In T01 every handler except `serve` raises NotImplementedError after
    10	argument parsing, so the full verb tree renders under --help; the HTTP calls land
    11	in stage 4."""
    12	
    13	from __future__ import annotations
    14	
    15	import sqlite3
    16	from collections.abc import Callable
    17	from typing import Any
    18	
    19	import click
    20	
    21	_PRIORITIES = ["P0", "P1", "P2", "P3"]
    22	_FIELDS = ["success", "approach", "plan", "result"]
    23	_LINK_KINDS = ["belongs_to", "parent_child", "blocks", "relates"]
    24	_TICKET_ID_ENV = "PLAN_TICKET_ID"
    25	
    26	
    27	def json_option(func: Callable[..., Any]) -> Callable[..., Any]:
    28	    return click.option(
    29	        "--json",
    30	        "as_json",
    31	        is_flag=True,
    32	        default=False,
    33	        help="Emit machine-readable JSON (errors as JSON on stderr).",
    34	    )(func)
    35	
    36	
    37	@click.group()
    38	def main() -> None:
    39	    """plan — the planner CLI for agents and developer debugging."""
    40	
    41	
    42	# --- serve (the one handler that actually works in T01) ---
    43	
    44	
    45	@main.command("serve")
    46	@json_option
    47	def serve(as_json: bool) -> None:
    48	    """Run the server (foreground)."""
    49	    import os
    50	
    51	    import uvicorn
    52	
    53	    from planner.core.adapters.registry import build_adapters
    54	    from planner.core.clock import build_clock
    55	    from planner.core.config import HOST, load_config
    56	    from planner.core.db import connect, create_schema
    57	    from planner.core.server import create_app
    58	
    59	    config = load_config()
    60	    os.makedirs(os.path.dirname(config.db_path) or ".", exist_ok=True)
    61	    os.makedirs(config.logs_dir, exist_ok=True)
    62	    with connect(config.db_path, config.db_busy_timeout_ms) as bootstrap:
    63	        create_schema(bootstrap)
    64	
    65	    clock = build_clock(config)
    66	    adapters = build_adapters(config)
    67	
    68	    def conn_factory() -> sqlite3.Connection:
    69	        return connect(config.db_path, config.db_busy_timeout_ms)
    70	
    71	    app = create_app(config, clock, adapters, conn_factory)
    72	    uvicorn.run(app, host=HOST, port=config.port)
    73	
    74	
    75	# --- seed ---
    76	
    77	
    78	@main.command("seed")
    79	@click.option("--source", default=None, help="Path to a markdown planning directory.")
    80	@click.option("--demo", is_flag=True, default=False, help="Create the deterministic demo dataset.")
    81	@json_option
    82	def seed(source: str | None, demo: bool, as_json: bool) -> None:
    83	    """Import a markdown planning directory, or create a demo dataset (§12)."""
    84	    raise NotImplementedError
    85	
    86	
    87	# --- top-level proposal/recap/note verbs ---
    88	
    89	
    90	@main.command("propose")
    91	@click.argument("field", type=click.Choice(_FIELDS))
    92	@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
    93	@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
    94	@json_option
    95	def propose(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
    96	    """File or replace a proposal on a field (agent path)."""
    97	    raise NotImplementedError
    98	
    99	
   100	@main.command("recap")
   101	@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
   102	@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
   103	@json_option
   104	def recap(ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
   105	    """Write a ticket recap (rejected before needs_approach)."""
   106	    raise NotImplementedError
   107	
   108	
   109	@main.command("note")
   110	@click.argument("field", type=click.Choice(_FIELDS))
   111	@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
   112	@click.option("--body-file", default=None, help="Path to the body, or - for stdin.")
   113	@json_option
   114	def note(field: str, ticket_id: str | None, body_file: str | None, as_json: bool) -> None:
   115	    """Write a field's notes slot (human or agent, any time)."""
   116	    raise NotImplementedError
   117	
   118	
   119	# --- ticket group ---
   120	
   121	
   122	@main.group("ticket")
   123	def ticket() -> None:
   124	    """Ticket verbs."""
   125	
   126	
   127	@ticket.command("create")
   128	@click.option("--title", required=True, help="Ticket title (<= 200 chars).")
   129	@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
   130	@click.option("--deadline", default=None, help="ISO date.")
   131	@click.option("--project", default=None, help="Vylo | Tribe | Learning | Other.")
   132	@click.option("--sprint", default=None, help="Sprint id (standalone tickets only).")
   133	@click.option("--item", default=None, help="Sprint item id to parent under.")
   134	@json_option
   135	def ticket_create(
   136	    title: str,
   137	    priority: str | None,
   138	    deadline: str | None,
   139	    project: str | None,
   140	    sprint: str | None,
   141	    item: str | None,
   142	    as_json: bool,
   143	) -> None:
   144	    """Create a ticket."""
   145	    raise NotImplementedError
   146	
   147	
   148	@ticket.command("show")
   149	@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
   150	@json_option
   151	def ticket_show(ticket_id: str | None, as_json: bool) -> None:
   152	    """Show a ticket (defaults to $PLAN_TICKET_ID)."""
   153	    raise NotImplementedError
   154	
   155	
   156	@ticket.command("list")
   157	@click.option("--state", default=None)
   158	@click.option("--project", default=None)
   159	@click.option("--sprint", default=None)
   160	@json_option
   161	def ticket_list(state: str | None, project: str | None, sprint: str | None, as_json: bool) -> None:
   162	    """List tickets."""
   163	    raise NotImplementedError
   164	
   165	
   166	@ticket.command("set")
   167	@click.argument("ticket_id", required=False, envvar=_TICKET_ID_ENV)
   168	@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
   169	@click.option("--deadline", default=None, help="ISO date, or 'none' to clear.")
   170	@click.option("--day", default=None, help="ISO date or 'today' to assign to a day.")
   171	@click.option("--sprint", default=None, help="Sprint id, or 'none' to clear.")
   172	@json_option
   173	def ticket_set(
   174	    ticket_id: str | None,
   175	    priority: str | None,
   176	    deadline: str | None,
   177	    day: str | None,
   178	    sprint: str | None,
   179	    as_json: bool,
   180	) -> None:
   181	    """Set priority / deadline / day / sprint (no ceiling or at_cap — grants are human)."""
   182	    raise NotImplementedError
   183	
   184	
   185	# --- item group ---
   186	
   187	
   188	@main.group("item")
   189	def item() -> None:
   190	    """Sprint-item verbs."""
   191	
   192	
   193	@item.command("create")
   194	@click.option("--title", required=True)
   195	@click.option("--project", required=True, help="Vylo | Tribe | Learning | Other.")
   196	@click.option("--priority", type=click.Choice(_PRIORITIES), default=None)
   197	@click.option("--deadline", default=None, help="ISO date.")
   198	@click.option("--sprint", default=None, help="Sprint id.")
   199	@json_option
   200	def item_create(
   201	    title: str,
   202	    project: str,
   203	    priority: str | None,
   204	    deadline: str | None,
   205	    sprint: str | None,
   206	    as_json: bool,
   207	) -> None:
   208	    """Create a sprint item."""
   209	    raise NotImplementedError
   210	
   211	
   212	@item.command("show")
   213	@click.argument("item_id")
   214	@json_option
   215	def item_show(item_id: str, as_json: bool) -> None:
   216	    """Show a sprint item."""
   217	    raise NotImplementedError
   218	
   219	
   220	@item.command("list")
   221	@click.option("--status", default=None)
   222	@click.option("--project", default=None)
   223	@click.option("--backlog", is_flag=True, default=False, help="Items with no sprint (backlog).")
   224	@json_option
   225	def item_list(status: str | None, project: str | None, backlog: bool, as_json: bool) -> None:
   226	    """List sprint items."""
   227	    raise NotImplementedError
   228	
   229	
   230	@item.command("set")
   231	@click.argument("item_id")
   232	@click.option("--status", type=click.Choice(["todo", "active", "blocked"]), default=None)
   233	@click.option("--blocked-by", default=None, help="Comma-separated blocker ticket ids.")
   234	@json_option
   235	def item_set(item_id: str, status: str | None, blocked_by: str | None, as_json: bool) -> None:
   236	    """Set an agent-permitted item transition (§3.2)."""
   237	    raise NotImplementedError
   238	
   239	
   240	@item.command("propose-status")
   241	@click.argument("item_id")
   242	@click.option(
   243	    "--to", "to_status", type=click.Choice(["done", "deferred_next_sprint"]), required=True
   244	)
   245	@click.option("--body-file", default=None, help="Optional rationale body, or - for stdin.")
   246	@json_option
   247	def item_propose_status(item_id: str, to_status: str, body_file: str | None, as_json: bool) -> None:
   248	    """Propose a done / deferred_next_sprint status for human acceptance."""
   249	    raise NotImplementedError
   250	
   251	
   252	# --- sprint group ---
   253	
   254	
   255	@main.group("sprint")
   256	def sprint() -> None:
   257	    """Sprint verbs."""
   258	
   259	
   260	@sprint.command("show")
   261	@json_option
   262	def sprint_show(as_json: bool) -> None:
   263	    """Show the current sprint view."""
   264	    raise NotImplementedError
   265	
   266	
   267	# --- idea group ---
   268	
   269	
   270	@main.group("idea")
   271	def idea() -> None:
   272	    """Idea verbs."""
   273	
   274	
   275	@idea.command("create")
   276	@click.option("--title", required=True)
   277	@click.option("--project", default=None, help="Vylo | Tribe | Learning | Other.")
   278	@click.option("--body-file", default=None, help="Optional body, or - for stdin.")
   279	@json_option
   280	def idea_create(title: str, project: str | None, body_file: str | None, as_json: bool) -> None:
   281	    """Create an idea."""
   282	    raise NotImplementedError
   283	
   284	
   285	@idea.command("list")
   286	@json_option
   287	def idea_list(as_json: bool) -> None:
   288	    """List ideas."""
   289	    raise NotImplementedError
   290	
   291	
   292	# --- day group ---
   293	
   294	
   295	@main.group("day")
   296	def day() -> None:
   297	    """Day verbs."""
   298	
   299	
   300	@day.command("show")
   301	@click.argument("date", required=False)
   302	@json_option
   303	def day_show(date: str | None, as_json: bool) -> None:
   304	    """Show a day (defaults to the current planning date; accepts 'today')."""
   305	    raise NotImplementedError
   306	
   307	
   308	@day.command("add-ticket")
   309	@click.argument("ticket_id")
   310	@click.argument("date", required=False)
   311	@json_option
   312	def day_add_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
   313	    """Add a ticket to a day (accepts 'today')."""
   314	    raise NotImplementedError
   315	
   316	
   317	@day.command("remove-ticket")
   318	@click.argument("ticket_id")
   319	@click.argument("date", required=False)
   320	@json_option
   321	def day_remove_ticket(ticket_id: str, date: str | None, as_json: bool) -> None:
   322	    """Remove a ticket from a day (deferring; ticket state untouched)."""
   323	    raise NotImplementedError
   324	
   325	
   326	# --- link group ---
   327	
   328	
   329	@main.group("link")
   330	def link() -> None:
   331	    """Link verbs."""
   332	
   333	
   334	@link.command("add")
   335	@click.argument("from_id")
   336	@click.argument("to_id")
   337	@click.option("--kind", type=click.Choice(_LINK_KINDS), required=True)
   338	@json_option
   339	def link_add(from_id: str, to_id: str, kind: str, as_json: bool) -> None:
   340	    """Add a link."""
   341	    raise NotImplementedError
   342	
   343	
   344	@link.command("rm")
   345	@click.argument("from_id")
   346	@click.argument("to_id")
   347	@click.option("--kind", type=click.Choice(_LINK_KINDS), required=True)
   348	@json_option
   349	def link_rm(from_id: str, to_id: str, kind: str, as_json: bool) -> None:
   350	    """Remove a link."""
   351	    raise NotImplementedError
   352	
   353	
   354	# --- run group ---
   355	
   356	
   357	@main.group("run")
   358	def run() -> None:
   359	    """Run verbs (claim env from $PLAN_RUN_ID / $PLAN_CLAIM)."""
   360	
   361	
   362	@run.command("heartbeat")
   363	@json_option
   364	def run_heartbeat(as_json: bool) -> None:
   365	    """Extend the claim by one TTL."""
   366	    raise NotImplementedError
   367	
   368	
   369	@run.command("close")
   370	@click.option("--outcome", type=click.Choice(["done", "blocked"]), required=True)
   371	@click.option("--summary", default=None, help="Path to the summary body, or - for stdin.")
   372	@json_option
   373	def run_close(outcome: str, summary: str | None, as_json: bool) -> None:
   374	    """Close the run with a terminal outcome."""
   375	    raise NotImplementedError
   376	
   377	
   378	# --- queue group ---
   379	
   380	
   381	@main.group("queue")
   382	def queue() -> None:
   383	    """Derived read-only views (§4.5)."""
   384	
   385	
   386	@queue.command("approvals")
   387	@json_option
   388	def queue_approvals(as_json: bool) -> None:
   389	    """Pending gating-field / status proposals and needs_review, oldest first."""
   390	    raise NotImplementedError
   391	
   392	
   393	@queue.command("pickup")
   394	@json_option
   395	def queue_pickup(as_json: bool) -> None:
   396	    """Dispatcher-eligible tickets (§7.2)."""
   397	    raise NotImplementedError
   398	
   399	
   400	@queue.command("overdue")
   401	@json_option
   402	def queue_overdue(as_json: bool) -> None:
   403	    """Tickets/items past deadline and not done/dropped."""
   404	    raise NotImplementedError
   405	
   406	
   407	if __name__ == "__main__":
   408	    main()

codex
At this point I haven’t found missing enum members, missing tables, or route gaps. I’m doing a mechanical cross-check of the SQLite schema against dataclass fields to catch column-level omissions before I conclude.
codex
The sandbox blocks shell here-doc temp files, so I’m switching to an inline Python check with bytecode writes disabled. No source changes are being made.
exec
/bin/zsh -lc "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -c 'import sqlite3; from dataclasses import fields; from planner.core.db import create_schema; from planner.tickets.contracts import Ticket; from planner.sprints.contracts import Sprint,SprintItem,Idea; from planner.days.contracts import Day; from planner.dispatch.contracts import Run; conn=sqlite3.connect(\":memory:\"); conn.row_factory=sqlite3.Row; create_schema(conn); pairs=[(\"sprints\",Sprint),(\"sprint_items\",SprintItem),(\"ideas\",Idea),(\"tickets\",Ticket),(\"days\",Day),(\"runs\",Run)]; [print(table,\"missing=\",[f.name for f in fields(cls) if f.name not in [r[\"name\"] for r in conn.execute(f\"PRAGMA table_info({table})\")]],\"extra=\",[r[\"name\"] for r in conn.execute(f\"PRAGMA table_info({table})\") if r[\"name\"] not in [f.name for f in fields(cls)]]) for table,cls in pairs]; print(\"tables=\",[r[\"name\"] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\" ORDER BY name\")])'" in /Users/khushaljagota/.hermes/planning-v2
 exited 127 in 0ms:
zsh:1: command not found: python

codex
`python` is not on PATH in this environment; I’m retrying the same read-only schema check with `python3`.
exec
/bin/zsh -lc "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -c 'import sqlite3; from dataclasses import fields; from planner.core.db import create_schema; from planner.tickets.contracts import Ticket; from planner.sprints.contracts import Sprint,SprintItem,Idea; from planner.days.contracts import Day; from planner.dispatch.contracts import Run; conn=sqlite3.connect(\":memory:\"); conn.row_factory=sqlite3.Row; create_schema(conn); pairs=[(\"sprints\",Sprint),(\"sprint_items\",SprintItem),(\"ideas\",Idea),(\"tickets\",Ticket),(\"days\",Day),(\"runs\",Run)]; [print(table,\"missing=\",[f.name for f in fields(cls) if f.name not in [r[\"name\"] for r in conn.execute(f\"PRAGMA table_info({table})\")]],\"extra=\",[r[\"name\"] for r in conn.execute(f\"PRAGMA table_info({table})\") if r[\"name\"] not in [f.name for f in fields(cls)]]) for table,cls in pairs]; print(\"tables=\",[r[\"name\"] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\" ORDER BY name\")])'" in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
sprints missing= [] extra= []
sprint_items missing= [] extra= []
ideas missing= [] extra= []
tickets missing= [] extra= []
days missing= [] extra= []
runs missing= [] extra= []
tables= ['boundary_runs', 'day_tickets', 'days', 'events', 'ideas', 'links', 'runs', 'sprint_items', 'sprints', 'sqlite_sequence', 'tickets']

codex
One binding amendment concerns `decisions.md`, which is outside the source list but inside the T01 amendment list. I’m checking that single file as well so amendment 9 is not left unverified.
exec
/bin/zsh -lc 'rg -n "D4|click|httpx|layer|layering|FastAPI|pydantic" decisions.md' in /Users/khushaljagota/.hermes/planning-v2
 succeeded in 0ms:
17:## D4 — CLI lives at `src/planner/cli/`
19:SPEC §2 names core/ plus six domains and doesn't place the CLI. The CLI is a surface over the HTTP API, not a domain; it gets its own folder beside the domains (non-trivial things get folders). Amended after T01 plan review: it imports contracts, click, httpx, and stdlib — never domain logic or data layers. The layering is the rule; click/httpx are pinned dependencies chosen for it.

codex
NO VIOLATIONS

Sharpening suggestions:

1. Add a small CLI helper stub for `read_body(...)` now, even before behavior lands, to make amendment 6 harder to regress.
2. Add a schema smoke test in T02 that compares dataclass fields to `PRAGMA table_info`; I manually verified no missing columns.
3. Add a route-table assertion test against FastAPI’s registered routes so section 13 stays locked as implementations fill in.
tokens used
113,474
NO VIOLATIONS

Sharpening suggestions:

1. Add a small CLI helper stub for `read_body(...)` now, even before behavior lands, to make amendment 6 harder to regress.
2. Add a schema smoke test in T02 that compares dataclass fields to `PRAGMA table_info`; I manually verified no missing columns.
3. Add a route-table assertion test against FastAPI’s registered routes so section 13 stays locked as implementations fill in.
