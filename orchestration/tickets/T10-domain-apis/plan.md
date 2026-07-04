# T10 implementation plan — domain APIs over the canonical writers + derived views

Contract sources: T01 plan §13 route table + route-ownership paragraph + amendments (esp. 10: day
routes accept literal `today`); SPEC §3, §4 (incl. §4.5), §5, §6.1/§6.3, §7.2/§7.3/§7.6, §9, §10.3/§10.4.
Law on disk: `tickets/data.py`, `sprints/data.py`, `days/data.py`, `dispatch/data.py`, `core/links.py`,
`core/events.py`, `core/authctx.py` (T09), `days/scheduler.py` (T11 seam — `submit_replan` only).
Routes are parse → auth → writer → serialize. No route appends events (audited below — none needed).
No route re-implements a domain rule; enum/format marshalling and existence guards are the only
route-side checks, and every rejection is a `PlannerError` (rendered by T09's app-level handler).

Owned files (the implementer touches nothing else):

- `src/planner/tickets/api.py` (replace stubs)
- `src/planner/sprints/api.py` (replace stubs)
- `src/planner/days/api.py` (replace stubs)
- `src/planner/dispatch/api.py` (replace stubs)
- `src/planner/tickets/views.py` (new)
- `src/planner/sprints/views.py` (new)
- `orchestration/tickets/T10-domain-apis/smoke.py` (new)

---

## 0. Global conventions (bind every section below)

**Handlers are `async def`.** `db.connect()` leaves sqlite3's `check_same_thread=True` in place. Sync
(`def`) handlers run in Starlette's threadpool, and a sync dependency may run on a *different*
threadpool thread than the endpoint — a connection would cross threads and raise. Async handlers and
async dependencies both run on the event-loop thread, so a per-request connection stays on one
thread. Blocking the loop with sqlite calls is acceptable: single user, localhost, and T09's
`testmode.py` tick endpoints already do exactly this.

**Connection lifecycle.** One connection per request via an async-generator dependency (Section 5),
opened from `request.app.state.conn_factory`, closed in `finally`. Never cached, never shared.

**Transaction discipline** (inspected from the data layers):

- `tickets/data.py` writers and `sprints/data.py` writers each open their own `BEGIN IMMEDIATE`
  (`_txn`/`_tx`) and commit/rollback internally. Routes call them bare. **Never wrap them in a
  route-level transaction** — sqlite raises `cannot start a transaction within a transaction`.
- `days/data.py`, `dispatch/data.py` (`heartbeat`, `close_run`, `clear_auto_block`) and
  `core/links.remove_link` do **not** transact — each statement autocommits (`isolation_level=None`).
  Routes wrap each such *mutating* call in the shared `txn(conn)` context manager (Section 5) so a
  `PlannerError` mid-sequence rolls back cleanly and no half-commit survives. `core/links.add_link`
  checks `conn.in_transaction` and joins the outer transaction — wrapping it is safe.
- Reads (including `days/data.read_day`'s materializing INSERT, which is spec'd single-statement-safe
  behavior per §3.4) run bare.
- On `PlannerError` the T09 handler renders `{"error": {code, message, detail}}` with
  `http_status_for` (404 not_found, 409 stale_claim, 503 gateway_offline, else 400). Routes never
  catch `PlannerError`.

**Enum/format marshalling rule (route-side, uniform):** converting an HTTP string into a contract
enum happens in the route via `parse_enum` (Section 5). Failure raises
`PlannerError(ErrorCode.validation, "invalid <what>", {"<what>": raw})` — **except** the grant
vocabulary (`next_ceiling`, `at_cap`, `ceiling` on grant routes), where an unknown value raises
`ErrorCode.grant_invalid` to match the engine's own vocabulary. Absence is never route-checked where
an engine check exists: a missing grant pair is passed through as `None` so
`machine.resolve_grant` raises the canonical `grant_missing`.

**Actor plumbing (§7.6),** consumed exactly from `core/authctx.py`:

- `(H)` routes call `reject_agents(ctx)` as the **first statement** of the handler body. The complete
  (H) set (14 routes — codex should audit this list against §9/route table):
  1. `POST /api/tickets/{id}/accept/{field}`
  2. `POST /api/tickets/{id}/approve`
  3. `POST /api/tickets/{id}/grant`
  4. `POST /api/tickets/{id}/state`
  5. `POST /api/tickets/{id}/drop`
  6. `POST /api/tickets/{id}/unblock`
  7. `POST /api/items/{id}/accept-status`
  8. `POST /api/sprints`
  9. `POST /api/sprints/{id}/freeze-kickoff`
  10. `POST /api/sprints/{id}/freeze-review`
  11. `POST /api/day/{date}/plan/accept`
  12. `POST /api/day/{date}/plan/accept-all`
  13. `POST /api/day/{date}/plan/invalidate`
  14. `POST /api/day/{date}/plan/reject-all`
- Claim-carrying ticket writes: `propose/{field}`, `notes/{field}`, `recap` call
  `require_claim(conn, ctx, ticket_id, clock.now_unix())` **iff `ctx.is_claimed_agent`** (any claim
  header present), then pass `ctx.actor` to the writer. Plain-agent (actor header only) and human
  requests pass `ctx.actor` straight through — §7.6's non-dispatched-agent path.
- Run routes (`heartbeat`, `close`) call `require_claim` **unconditionally** ("claim headers
  required"): a request with no claim headers fails inside `require_claim` with
  `stale_claim/missing_header`. `require_claim` takes a **ticket_id** — run routes first resolve
  `run_id → ticket_id` via `tickets/views.get_run` (Section 4; `None` → `not_found`), then validate.
- Item writes never call `require_claim` (claims are per-ticket; items carry none). A dispatched
  agent's claim headers on an item route are ignored except for `ctx.actor` /
  `by_agent = not ctx.is_human`. Noted as a decision.
- Unmarked routes (creates, lists, reads, ticket/item/sprint PATCH, day GET/PATCH/tickets, addenda,
  links, ideas, propose-status): no auth step beyond the actor plumbing stated per route.

**Response conventions.** Every mutation returns the fresh entity JSON (re-read after the writer, or
the writer's own return). Lists are wrapped: `{"tickets": [...]}`, `{"items": [...]}`,
`{"sprints": [...]}`, `{"ideas": [...]}`, `{"runs": [...]}`, `{"events": [...]}`. Status code 200
everywhere (uniform; FastAPI default). `copy-text` returns `PlainTextResponse`.

---

## 1. File-by-file blueprint

### 1.1 `src/planner/tickets/api.py`

Module layout: shared helpers (Section 5) → pydantic models (Section 2) → grant/enum marshallers →
private writers (deviations D2) → route handlers. Imports: `fastapi` (`APIRouter`, `Depends`,
`Request`), `fastapi.responses.PlainTextResponse`, `pydantic.BaseModel`, `sqlite3`, `json`,
`contextlib`, `typing.cast`, `planner.core.authctx` (`RequestContext`, `request_context`,
`require_claim`, `reject_agents`), `planner.core` (`config.Config`, `clock.Clock`,
`contracts.{JsonDict, Priority, Project, LinkKind, EventKind}`, `errors`, `events.append_event`,
`links as core_links`), `planner.tickets.contracts`
(`AtCap`, `FieldName`, `NextCeiling`, `NO_FURTHER`, `Ticket`, `TicketState`),
`planner.tickets.data as tickets_data`, `planner.tickets.logic.admission`,
`planner.dispatch.data as dispatch_data`, `planner.tickets import views as tickets_views`,
`planner.sprints import views as sprints_views`, `planner.days.logic.dates.planning_date`.
(`append_event`/`EventKind` are imported **only** for the D2 private writers, which mirror
`set_priority`'s writer pattern — no route handler appends events.)

Handler-by-handler (each row: signature → auth → calls → response):

1. `POST /tickets` — `async def create_ticket(body: CreateTicketBody, conn, ctx, cfg, clk)`
   → no auth step → marshal `priority` (`Priority`, default `Priority.P3` when body field is None),
   `project` (`Project | None`) → `tickets_data.create_ticket(conn, title=body.title,
   actor=ctx.actor, now=clk.now_unix(), title_max_chars=cfg.title_max_chars, project=…,
   priority=…, deadline=body.deadline, sprint_id=body.sprint_id,
   sprint_item_id=body.sprint_item_id)` → `tickets_views.ticket_json(t, clk.now_unix())`.
2. `GET /tickets` — `async def list_tickets(conn, clk, state: str | None = None, project: str |
   None = None, sprint_id: str | None = None, sprint_item_id: str | None = None)` → marshal
   `state`→`TicketState`, `project`→`Project` when present (invalid → validation) →
   `{"tickets": tickets_views.list_tickets(conn, now, state=…, project=…, sprint_id=…,
   sprint_item_id=…)}`.
3. `GET /tickets/{ticket_id}` — → `tickets_views.ticket_detail(conn, ticket_id, clk.now_unix())`
   (raises not_found inside via `read_ticket`).
4. `PATCH /tickets/{ticket_id}` — body is raw `dict[str, Any]` (key-driven dispatch; unknown keys
   must produce the structured envelope, not a 422). Recognized keys, applied in this fixed order:
   `title`, `priority`, `deadline`, `project`, `sprint_id`. First, reject any unrecognized key with
   `PlannerError(validation, "unknown ticket field", {"field": k})`; empty body → validation error.
   Then per key: `title` → private `_set_title` (D2); `priority` → `parse_enum(Priority, …)` +
   `tickets_data.set_priority`; `deadline` (str | None) → `tickets_data.set_deadline`; `project`
   (str | None → `Project | None`) → private `_set_project` (D2); `sprint_id` (str | None) →
   `tickets_data.set_sprint`. Each writer self-commits; a failure mid-sequence leaves earlier keys
   applied (non-atomic across keys — concern C3). Auth: none (plain field updates; §3.3 rules are in
   the writers/private writers). Response: `ticket_json` of the final read.
5. `POST /tickets/{ticket_id}/propose/{field}` — `field` → `parse_enum(FieldName, …)`; if
   `ctx.is_claimed_agent`: `require_claim(conn, ctx, ticket_id, now)` →
   `tickets_data.file_proposal(conn, ticket_id, field=…, body=body.body, actor=ctx.actor, now=now)`
   → `ticket_json`. (Engine enforces at-cap/at_cap_stop/terminal — no route checks. Empty body
   reaches the engine's `validate_body` → its validation error.)
6. `POST /tickets/{ticket_id}/accept/{field}` — **(H) `reject_agents(ctx)` first** → `field` →
   `FieldName` → grant marshalling: `next_ceiling = _parse_next_ceiling(body.next_ceiling)`,
   `at_cap = _parse_grant_at_cap(body.at_cap)` (both pass `None` through untouched — absence must
   reach the engine) → `tickets_data.accept_proposal(conn, ticket_id, field=…, actor=ctx.actor,
   now=now, edited_body=body.edited_body, next_ceiling=…, at_cap=…)` → `ticket_json`. No
   route-level defaults, no pair-completeness check: `machine.resolve_grant` is the door
   (`grant_missing` / `grant_invalid`).
7. `POST /tickets/{ticket_id}/approve` — **(H)** → `tickets_data.approve_review(conn, ticket_id,
   actor=ctx.actor, now=now)` → `ticket_json`. No grant pair by design (§4.4.7).
8. `PUT /tickets/{ticket_id}/notes/{field}` — `field` → `FieldName`; if `ctx.is_claimed_agent`:
   `require_claim` → `tickets_data.set_note(conn, ticket_id, field=…, note=body.note,
   actor=ctx.actor, now=now)` → `ticket_json`. (`note: null` clears the slot — writer accepts
   `str | None`.)
9. `PUT /tickets/{ticket_id}/recap` — if `ctx.is_claimed_agent`: `require_claim` →
   `tickets_data.write_recap(conn, ticket_id, body=body.body, actor=ctx.actor, now=now)` →
   `ticket_json`. (`recap_too_early` comes from the writer.)
10. `POST /tickets/{ticket_id}/grant` — **(H)** → marshal `ceiling`: `None` →
    `PlannerError(grant_missing, "grant requires ceiling and at_cap", {"missing": [names]})`
    (mirrors the engine's missing-pair shape for the standalone grant edge, which
    `change_grant`'s signature cannot express); else `TicketState(raw)` with ValueError →
    `grant_invalid`; same presence/parse treatment for `at_cap` → `tickets_data.change_grant(conn,
    ticket_id, ceiling=…, at_cap=…, actor=ctx.actor, now=now)` (engine's `validate_ceiling`
    rejects `dropped`) → `ticket_json`.
11. `POST /tickets/{ticket_id}/state` — **(H)** → `to` → `parse_enum(TicketState, body.to,
    "state")` → `tickets_data.set_state(conn, ticket_id, new_state=…, actor=ctx.actor, now=now)`
    → `ticket_json`. (Engine rejects `to=dropped` → "use the drop action".)
12. `POST /tickets/{ticket_id}/drop` — **(H)**, no body → `tickets_data.drop_ticket` →
    `ticket_json`.
13. `POST /tickets/{ticket_id}/unblock` — **(H)**, no body → `with txn(conn):
    dispatch_data.clear_auto_block(conn, ticket_id, now)` (§7.5 human clear; emits its own event)
    → `ticket_json(tickets_data.read_ticket(conn, ticket_id), now)`.
14. `GET /tickets/{ticket_id}/events` — `tickets_data.read_ticket` (not_found guard) →
    `{"events": tickets_views.list_events_for_entity(conn, ticket_id, cfg.events_read_limit)}`.
15. `GET /tickets/{ticket_id}/runs` — `read_ticket` guard →
    `{"runs": tickets_views.runs_for_ticket(conn, ticket_id)}`.
16. `GET /tickets/{ticket_id}/copy-text` — `response_class=PlainTextResponse`; returns
    `tickets_views.copy_text(conn, ticket_id)` (raises not_found inside).
17. `POST /links` — `kind` → `parse_enum(LinkKind, …)` → `with txn(conn):
    core_links.add_link(conn, body.from_id, body.to_id, kind, now)` (add_link joins the outer
    txn via its `in_transaction` check) → `{"from_id": …, "to_id": …, "kind": kind.value}` (the
    `Link` contract shape).
18. `DELETE /links` — query params `from_id: str`, `to_id: str`, `kind: str` → `LinkKind` →
    `with txn(conn): core_links.remove_link(...)` (DELETE + event made atomic by the wrapper) →
    `{"ok": True}`.
19. `GET /board` — `tickets_views.board_view(conn, clk.now_unix())`.
20. `GET /queues` — compose: `now = clk.now_unix()`; `today_iso =
    planning_date(clk.now(), cfg.boundary_hour).isoformat()`; item rows from
    `sprints_views.approval_item_rows(conn)` and `sprints_views.overdue_item_rows(conn)` (the
    "sprints read helpers" of the T01 route-ownership note — passed in as parameters so
    `tickets/views` never imports `sprints/views`, breaking the would-be cycle) →
    `tickets_views.queues_view(conn, now, today_iso, item_approval_rows, item_overdue_rows)`.

Private writers (deviation D2, full text in Section 7):

- `def _set_title(conn, ticket_id: str, title: str, *, title_max_chars: int, now: int) -> Ticket` —
  `admission.validate_title(title, title_max_chars)`; `BEGIN IMMEDIATE` (own contextmanager use of
  `txn`); `read` prior title; `UPDATE tickets SET title=?, updated_at=? WHERE id=?`;
  `append_event(conn, ticket_id, EventKind.ticket_updated, {"field": "title", "from": prev,
  "to": title}, now)`; return `tickets_data.read_ticket`. Missing ticket → not_found via the prior
  read.
- `def _set_project(conn, ticket_id: str, project: Project | None, *, now: int) -> Ticket` — load
  ticket; if `ticket.sprint_item_id is not None` raise `PlannerError(validation, "project is
  derived when parented")` (the exact message `create_ticket` uses); UPDATE + `ticket_updated
  {"field": "project", "from", "to"}` event inside `txn`; return fresh read.

### 1.2 `src/planner/sprints/api.py`

Imports: fastapi/pydantic, `planner.tickets.api` shared helpers (`DbConn`, `Ctx`, `Cfg`, `Clk`,
`txn`, `parse_enum` — import direction: sprints/api → tickets/api, never reverse),
`planner.sprints.data as sprints_data`, `planner.sprints import views as sprints_views`,
`planner.sprints.contracts` (`ItemStatus`), `planner.core.contracts` (`Priority`, `Project`,
`EventKind`), `planner.core.ids` (`ID_PREFIXES`, `new_id`), `planner.core.events.append_event`
(D1 writer only), `planner.core.errors`, `planner.days.logic.dates.planning_date`.

1. `POST /items` — body `CreateItemBody`; `project` required: `None` →
   `PlannerError(validation, "project is required")`, else `Project(raw)`; `priority` →
   `Priority` (default P3 when absent) → `sprints_data.create_item(conn, title=body.title,
   project=…, body=body.body, priority=…, deadline=body.deadline,
   current_state_note=body.current_state_note, sprint_id=body.sprint_id, clock=clk)` →
   `sprints_views.item_json(item)` + `"blockers_cleared": False` (fresh item has no blockers).
   Note the signature difference: **sprint writers take `clock: Clock`**, not `now: int`.
2. `GET /items` — filters `status`, `project`, `sprint_id`; literal `sprint_id="null"` means
   `sprint_id IS NULL` (backlog, per route table); marshal enums when present →
   `{"items": sprints_views.list_items(conn, status=…, project=…, sprint_id_filter=…)}`.
3. `GET /items/{item_id}` — `sprints_views.item_detail(conn, item_id)` (item + `blockers_cleared`
   via `sprints_data.read_item` + `rollup` via `sprints_views.item_rollup`).
4. `PATCH /items/{item_id}` — raw `dict[str, Any]`. Recognized keys: plain fields
   `title, body, priority, deadline, project, current_state_note` → one
   `sprints_data.update_item_field(conn, item_id, field, value, clock=clk)` call each (writer
   validates priority/project values itself); `sprint_id` (str | None) →
   `sprints_data.assign_item_sprint`; `status` (+ optional `blocked_by: list[str]`) →
   `sprints_data.transition_item_status(conn, item_id, parse_enum(ItemStatus, …), clock=clk,
   by_agent=not ctx.is_human, blocked_by=body.get("blocked_by"))`. Unknown keys → validation
   error before any write; `blocked_by` present without `status` → validation error. Fixed apply
   order: plain fields (listed order), then `sprint_id`, then `status`. Response:
   `item_detail(conn, item_id)`. Agent-permitted transitions and proposal-only rejections come
   from `classify_agent_transition` inside the writer — no route policy.
5. `POST /items/{item_id}/propose-status` — body `ProposeStatusBody`; `to` →
   `parse_enum(ItemStatus, …)` → `sprints_data.propose_item_status(conn, item_id, to_status,
   note=body.note, proposed_by=ctx.actor, clock=clk)` → `item_detail`. (Writer enforces
   done/deferred-only and terminal-status rejection.)
6. `POST /items/{item_id}/accept-status` — **(H)** → `sprints_data.accept_item_status(conn,
   item_id, clock=clk, resolved_by="human")` → `item_detail`. No onward grant by design (§4.4.7).
7. `POST /sprints` — **(H)** → body `CreateSprintBody`; ISO-format marshal `date_start`/`date_end`
   via `date.fromisoformat` (ValueError → validation; lexical range/overlap logic downstream
   depends on well-formed ISO) → `sprints_data.create_sprint(conn, name=…, date_start=…,
   date_end=…, limiting_factor=…, primary_bet=…, supports=…, premortem=…, clock=clk)` →
   `sprints_views.sprint_json`. (Overlap → writer's `sprint_overlap`.)
8. `GET /sprints` — `{"sprints": sprints_views.list_sprints(conn)}` ordered `date_start DESC`.
9. `GET /sprints/{sprint_id}` — `sprint_json(sprints_data.read_sprint(conn, sprint_id))`.
10. `PATCH /sprints/{sprint_id}` — raw `dict[str, Any]`. Recognized keys: `name` + the nine
    kickoff/review text fields → `sprints_data.update_sprint_field(conn, sprint_id, field, value,
    clock=clk)` each (freeze rejection is the writer's `frozen_write`); `date_start`/`date_end` →
    private `_set_sprint_dates` (deviation D3). Unknown keys → validation error upfront. Fixed
    order: text fields in body-listed recognized order, dates last (one combined call). Response:
    `sprint_json` fresh read.
11. `POST /sprints/{sprint_id}/freeze-kickoff` — **(H)** → `sprints_data.freeze_kickoff(conn,
    sprint_id, clock=clk)` → `sprint_json`. Idempotent by writer design (second call: no event).
12. `POST /sprints/{sprint_id}/freeze-review` — **(H)** → `sprints_data.freeze_review` →
    `sprint_json`.
13. `POST /sprints/{sprint_id}/addenda` — body `AddendumBody` → `sprints_data.add_addendum(conn,
    sprint_id, date=body.date, text=body.text, clock=clk)` → `sprint_json`. No auth step
    (allowed post-freeze; not (H) in the route table).
14. `GET /sprint/current` — `today_iso = planning_date(clk.now(), cfg.boundary_hour).isoformat()`
    → `sprints_views.sprint_current_view(conn, today_iso, clk.now_unix())`.
15. `POST /ideas` — body `CreateIdeaBody` → private `_create_idea` (deviation D1) →
    `sprints_views.idea_json` shape.
16. `GET /ideas` — `{"ideas": sprints_views.list_ideas(conn)}` ordered `created_at DESC, id`.

Private writers:

- `def _create_idea(conn, *, title: str, body: str, project: Project | None, now: int) -> JsonDict`
  (deviation D1 — no data-layer writer exists; mirrors `seed/importer.py::_import_ideas` lines
  274–288 exactly): empty title → `PlannerError(validation, "idea title is required")` (mirrors
  `create_item`'s check); `idea_id = new_id(ID_PREFIXES["idea"])`; inside `with txn(conn):`
  `INSERT INTO ideas (id, title, body, project, created_at, updated_at) VALUES (?,?,?,?,?,?)` with
  `project.value if project else None`; `append_event(conn, idea_id, EventKind.idea_created,
  {"title": title, "source": "api"}, now)` (same payload keys as the importer, `source` value
  distinguishes the door); return the read-back row as `idea_json`.
- `def _set_sprint_dates(conn, sprint_id: str, *, date_start: str | None, date_end: str | None,
  clock: Clock) -> None` (deviation D3): load sprint (not_found guard); resolve effective new
  range (provided value or current); ISO-marshal; `start > end` → validation (mirror
  `create_sprint`'s message); `find_overlap(new_start, new_end, [ranges of all OTHER sprints])`
  → `sprint_overlap` on conflict (reuses `planner.sprints.logic.find_overlap` — the rule is not
  re-implemented, self-exclusion is the only new logic); inside `with txn(conn):` UPDATE both
  columns + `updated_at`, one `sprint_updated {"field", "from", "to"}` event per changed column
  (mirroring `update_sprint_field`'s event shape).

### 1.3 `src/planner/days/api.py`

Imports: fastapi/pydantic, shared helpers from `planner.tickets.api`, `planner.days.data as
days_data`, `planner.days.logic.tree as plan_tree`, `planner.days.logic.dates.planning_date`,
`planner.days.scheduler.submit_replan` (the pinned T11 seam — never anything else from scheduler),
`planner.core.ids.day_id`, `planner.tickets.data.read_ticket` (existence guard),
`planner.tickets.views.ticket_json`, `datetime.date`, errors/contracts.

Shared day helpers (module-level in this file):

- `def resolve_day_id(date_seg: str, clock: Clock, config: Config) -> str` — amendment 10:
  `date_seg == "today"` → `day_id(planning_date(clock.now(), config.boundary_hour))`; else
  `date.fromisoformat(date_seg)` (ValueError → `PlannerError(validation, "invalid date",
  {"date": date_seg})`) → `f"day_{date_seg}"`. Every one of the eight routes resolves through this
  helper first.
- `def _day_view(conn, did: str, now: int) -> JsonDict` — the GET/mutation response assembler:
  `day = days_data.read_day(conn, did, now)` (materializes per §3.4); `dts =
  days_data.list_day_tickets(conn, did)`; tickets list = `[ticket_json(read_ticket(conn,
  dt.ticket_id), now) for dt in dts]` (position order = list order); returns
  `{"id", "brief", "notes", "plan": plan_tree.tree_to_dict(day.plan) if day.plan else None,
  "chat_session_key", "created_at", "updated_at", "tickets": [...]}`.
- `def _parse_node(raw: object) -> str | int` — `"root"` → `"root"`; `isinstance(raw, int)` and
  not `bool` → the int; anything else → `PlannerError(validation, "node must be 'root' or a child
  position", {"node": raw})`. Matches `tree.NodeRef` and the `{node}` event payload contract.
- `def _load_plan_or_error(conn, did: str) -> PlanTree` — `days_data.load_plan`; `None` →
  `PlannerError(validation, "day has no plan", {"day_id": did})`.
- `def _require_child(tree: PlanTree, position: int) -> None` — no child at `position` →
  `PlannerError(validation, "no such plan node", {"node": position})`. Needed because
  `tree.accept_node`/`invalidate_child` on an unknown position would silently emit an event for a
  nonexistent node (signature surprise — see Section 8).

Routes (all resolve `did = resolve_day_id(date, clk, cfg)` first; `now = clk.now_unix()`):

1. `GET /day/{date}` — `_day_view(conn, did, now)`. Materialize-on-read is the spec'd behavior;
   the INSERT autocommits.
2. `PATCH /day/{date}` — body `DayPatchBody`; both fields absent → validation error; per present
   field `with txn(conn): days_data.set_brief(...)` / `set_notes(...)` (each emits its own
   `day_updated` event; the txn wrapper makes UPDATE+event atomic) → `_day_view`. Not (H).
3. `POST /day/{date}/tickets` — body `AddDayTicketBody`; `read_ticket(conn, body.ticket_id)`
   existence guard (prevents a raw FK IntegrityError → 500 on unknown ticket; a read guard, not a
   rule) → `with txn(conn): days_data.add_day_ticket(conn, did, ticket_id, now)` (cause defaults
   `"manual"`; idempotent False-return still answers 200) → `_day_view`.
4. `DELETE /day/{date}/tickets/{ticket_id}` — `with txn(conn): days_data.remove_day_ticket(conn,
   did, ticket_id, now)` (§3.4 remove = defer; no-op when absent) → `_day_view`.
5. `POST /day/{date}/plan/accept` — **(H)** → `node = _parse_node(body.node)`; `tree =
   _load_plan_or_error`; int node → `_require_child` → `new_tree, effects =
   plan_tree.accept_node(tree, node)` → `with txn(conn): days_data.apply_plan_effects(conn, did,
   new_tree, effects, now)` (returns `[]` replans here) → `_day_view`.
6. `POST /day/{date}/plan/accept-all` — **(H)**, no body → `_load_plan_or_error` →
   `plan_tree.accept_all(tree)` → `apply_plan_effects` in `txn` (child tickets join the day list
   via the `AddTicketToDay` effects, idempotently, inside the data layer) → `_day_view`.
7. `POST /day/{date}/plan/invalidate` — **(H)** → `_parse_node`; `_load_plan_or_error`; int →
   `_require_child` → `invalidate_root(tree)` / `invalidate_child(tree, pos)` → `with txn(conn):
   replans = days_data.apply_plan_effects(...)` → **after commit**: `for r in replans:
   submit_replan(did, r)` (exactly one; submitted post-commit so the R5 consumer only ever sees
   committed state; execution/serialization is T11's, this route only enqueues) → `_day_view`.
8. `POST /day/{date}/plan/reject-all` — **(H)**, no body → `_load_plan_or_error` (already-null plan
   → the same "day has no plan" validation error, consistent with accept) →
   `plan_tree.reject_all(tree)` → `apply_plan_effects(conn, did, None, effects, now)` in `txn`
   (data layer writes `plan = NULL`; old tree preserved in the `plan_rejected` payload by the
   transform) → `_day_view`.

### 1.4 `src/planner/dispatch/api.py`

Imports: fastapi/pydantic, shared helpers from `planner.tickets.api`, `planner.core.authctx`
(`require_claim`), `planner.dispatch.data as dispatch_data`, `planner.dispatch.contracts`
(`RunStatus`, `AGENT_CLOSE_OUTCOMES`), `planner.tickets.views` (`get_run`), errors.

1. `POST /runs/{run_id}/heartbeat` — no body. `run = tickets_views.get_run(conn, run_id)`;
   `None` → `PlannerError(not_found, "run not found", {"run_id": run_id})`;
   `require_claim(conn, ctx, run["ticket_id"], now)` (unconditional — this is how "claim headers
   required" is enforced; missing headers → `stale_claim/missing_header`) → `with txn(conn):
   new_expires = dispatch_data.heartbeat(conn, run_id, now, cfg.claim_ttl_seconds)` (emits
   `claim_heartbeat` itself) → `{"run": tickets_views.get_run(conn, run_id),
   "claim_expires": new_expires}`.
2. `POST /runs/{run_id}/close` — body `CloseRunBody`. `outcome` → `parse_enum(RunStatus, …)` then
   `if status not in AGENT_CLOSE_OUTCOMES: PlannerError(validation, "outcome must be done or
   blocked", {"outcome": raw})` — this reuses the contract constant (§8's agent vocabulary), it
   does not duplicate `close_run`'s `_CLOSABLE` check, which deliberately also admits the failure
   statuses reserved for T11's runtime path. Resolve `ticket_id` via `get_run` (not_found guard) →
   `require_claim(conn, ctx, ticket_id, now)` → `with txn(conn): dispatch_data.close_run(conn,
   run_id, status, now, cfg.failure_limit, summary=body.summary)` (emits `run_closed` and, on a
   breaker trip, `auto_blocked`) → `{"run": tickets_views.get_run(conn, run_id)}`.

### 1.5 `src/planner/tickets/views.py` — see Section 4.
### 1.6 `src/planner/sprints/views.py` — see Section 4.
### 1.7 `orchestration/tickets/T10-domain-apis/smoke.py` — see Section 6.

---

## 2. Pydantic request models (per api.py, field-for-field)

All models: plain `BaseModel`, default (non-strict) config, extra keys **ignored** (pydantic
default) — unknown-key *rejection* exists only on the three PATCH routes, which take raw dicts
precisely so the rejection is a `PlannerError` envelope. Fields default to `None`/`""` wherever a
downstream engine/writer owns the absence check, so absence produces the *engine's* structured
error, never a FastAPI 422. (Residual 422 exposure on wrong JSON types is concern C2.)

`tickets/api.py`:

```python
class CreateTicketBody(BaseModel):
    title: str = ""                    # writer's validate_title rejects empty
    priority: str | None = None        # None -> Priority.P3 (writer default, applied at marshal)
    deadline: str | None = None        # writer's validate_deadline checks ISO
    project: str | None = None
    sprint_id: str | None = None
    sprint_item_id: str | None = None

class ProposeBody(BaseModel):
    body: str = ""                     # empty reaches engine -> its validation error

class AcceptBody(BaseModel):           # grant-pair passthrough: EVERYTHING optional, NO defaults
    edited_body: str | None = None     # beyond None; absence flows to resolve_grant ->
    next_ceiling: str | None = None    # grant_missing lists the absent half/halves
    at_cap: str | None = None

class NoteBody(BaseModel):
    note: str | None = None            # null clears the notes slot

class RecapBody(BaseModel):
    body: str = ""

class GrantBody(BaseModel):
    ceiling: str | None = None         # None -> route raises grant_missing (see handler 10)
    at_cap: str | None = None

class StateBody(BaseModel):
    to: str = ""                       # "" -> parse_enum -> validation

class LinkBody(BaseModel):
    from_id: str = ""
    to_id: str = ""
    kind: str = ""
```

`PATCH /tickets/{id}` body: `dict[str, Any]` (no model).

`sprints/api.py`:

```python
class CreateItemBody(BaseModel):
    title: str = ""
    project: str | None = None         # required: route raises validation when None
    body: str = ""
    priority: str | None = None        # None -> Priority.P3
    deadline: str | None = None
    current_state_note: str = ""
    sprint_id: str | None = None

class ProposeStatusBody(BaseModel):
    to: str = ""
    note: str | None = None

class CreateSprintBody(BaseModel):
    name: str = ""                     # writer rejects empty
    date_start: str = ""               # route ISO-marshal rejects malformed/empty
    date_end: str = ""
    limiting_factor: str = ""
    primary_bet: str = ""
    supports: str = ""
    premortem: str = ""

class AddendumBody(BaseModel):
    date: str = ""                     # writer rejects empty date/text
    text: str = ""

class CreateIdeaBody(BaseModel):
    title: str = ""
    body: str = ""
    project: str | None = None
```

`PATCH /items/{id}` and `PATCH /sprints/{id}` bodies: `dict[str, Any]`.

`days/api.py`:

```python
class DayPatchBody(BaseModel):
    brief: str | None = None
    notes: str | None = None

class AddDayTicketBody(BaseModel):
    ticket_id: str = ""

class PlanNodeBody(BaseModel):
    node: int | str | None = None      # _parse_node enforces "root" | int
```

`dispatch/api.py`:

```python
class CloseRunBody(BaseModel):
    outcome: str = ""
    summary: str | None = None
```

Grant marshallers (`tickets/api.py`, used only by handler 6; handler 10 has its own
presence-then-parse sequence):

```python
def _parse_next_ceiling(raw: str | None) -> NextCeiling | None:
    if raw is None: return None                      # absence -> engine's grant_missing
    if raw == NO_FURTHER: return NO_FURTHER          # "none" literal (§4.4.7)
    try: return TicketState(raw)
    except ValueError: raise PlannerError(ErrorCode.grant_invalid, "unknown next_ceiling",
                                          {"next_ceiling": raw})

def _parse_grant_at_cap(raw: str | None) -> AtCap | None:
    if raw is None: return None
    try: return AtCap(raw)
    except ValueError: raise PlannerError(ErrorCode.grant_invalid, "unknown at_cap",
                                          {"at_cap": raw})
```

---

## 3. Serializers — names, locations, exact output keys

One strategy everywhere: dataclass → plain `JsonDict` by hand-listing fields (no `asdict` — enums
must serialize as `.value` strings, nested shapes must match the contracts exactly). `None` → JSON
`null`. Ints (unix times) pass through. The frontend consumes exactly these keys.

In `tickets/views.py`:

- `ticket_json(ticket: Ticket, now: int) -> JsonDict` — keys, in contract order:
  `id, title, state, priority, deadline, project, sprint_item_id, sprint_id, recap, ceiling,
  at_cap, auto_blocked (bool), consecutive_failures, chat_session_key, alias, fields,
  claim_expires, created_at, updated_at`, plus derived `claim_active: bool` =
  `dispatch.logic.claims.has_active_claim(ticket.claim_lock, ticket.claim_expires, now)`.
  **`claim_lock` is deliberately omitted** — authctx's discipline is that the stored token is never
  echoed (deviation D6). `fields` = `json.loads(fields_codec.fields_to_json(ticket.fields))` —
  reuses the canonical codec through its public function, yielding exactly
  `{success|approach|plan|result: {value, proposal: {body, proposed_by, created_at} | null,
  notes}}`.
- `run_json(row) -> JsonDict` — `id, ticket_id, status, started_at, ended_at, summary, error, pid`
  (the `Run` contract, straight from the runs row).
- `event_json(row) -> JsonDict` — `id, entity_id, kind, payload (json.loads), created_at`
  (the `EventRow` contract).

In `sprints/views.py`:

- `item_json(item: SprintItem) -> JsonDict` — `id, title, body, status, priority, deadline,
  project, current_state_note, sprint_id, blocked_by (list), status_proposal
  ({to_status, note, proposed_by, created_at} | null), created_at, updated_at`.
- `sprint_json(sprint: Sprint) -> JsonDict` — `id, name, date_start, date_end, limiting_factor,
  primary_bet, supports, premortem, weekly_addenda ([{date, text}]), kickoff_frozen_at, outcomes,
  solo_reflection, joint_discussion, updates_to_thinking, carry_forward, review_frozen_at,
  created_at, updated_at`.
- `idea_json(row) -> JsonDict` — `id, title, body, project, created_at, updated_at`.

In `days/api.py`: the day view shape is assembled by `_day_view` (1.3) — `id, brief, notes,
plan (tree_to_dict | null), chat_session_key, created_at, updated_at, tickets ([ticket_json] in
position order)`. `tree_to_dict` is the plan serializer (days/logic/tree.py) — not re-implemented.

---

## 4. Views functions and query/reuse strategy

### 4.1 `src/planner/tickets/views.py`

Pure read assembly: `sqlite3`, `json`, contracts, `tickets_data` (for `read_ticket` /
`get_effective_sprint_id`), `dispatch.data.load_candidates`, `dispatch.logic`
(`is_eligible`, `ordering_key`, `has_active_claim`, `gating_field_pending`), `core.links`
(`is_blocked`), `days.logic.carryover` (`approvals_digest`, `overdue_list`). **No FastAPI, no
pydantic, no writes, no imports of any api module or of `sprints/views`** (item rows arrive as
parameters — the api layer wires the two views modules; this is what keeps the import graph
acyclic: `sprints/views → tickets/views` is the only cross-views edge).

- `list_tickets(conn, now, *, state: TicketState | None, project: Project | None,
  sprint_id: str | None, sprint_item_id: str | None) -> list[JsonDict]` — one SELECT of ids with
  a dynamically ANDed WHERE (each filter `col = ?`; parameters bound, never interpolated), ordered
  `created_at ASC, id`; then `ticket_json(read_ticket(conn, id), now)` per row (reuses the
  canonical row→Ticket mapping instead of duplicating `_row_to_ticket`; N+1 accepted at this
  scale — decision noted).
- `ticket_detail(conn, ticket_id, now) -> JsonDict` — the §9 "full ticket":
  `ticket_json` ∪ `{"blocked": core_links.is_blocked(conn, ticket_id),
  "effective_sprint_id": tickets_data.get_effective_sprint_id(conn, ticket_id),
  "links": [{"from_id","to_id","kind"} for rows of SELECT from_id,to_id,kind FROM links WHERE
  from_id=? OR to_id=? ORDER BY kind, from_id, to_id],
  "day_ids": [SELECT day_id FROM day_tickets WHERE ticket_id=? ORDER BY day_id ASC],
  "run_summary": {"total": COUNT(runs), "running": EXISTS(status='running'),
  "latest": run_json of ORDER BY started_at DESC, id DESC LIMIT 1 | null}}`.
- `runs_for_ticket(conn, ticket_id) -> list[JsonDict]` — `SELECT * FROM runs WHERE ticket_id=?
  ORDER BY started_at DESC, id DESC` → `run_json` each.
- `get_run(conn, run_id) -> JsonDict | None` — `SELECT * FROM runs WHERE id=?` → `run_json` or
  None. (Runs have no data-layer reader; homing these two here gives dispatch/api and
  ticket_detail one shared shape — deviation D5.)
- `list_events_for_entity(conn, entity_id, limit) -> list[JsonDict]` — `SELECT id, entity_id,
  kind, payload, created_at FROM events WHERE entity_id = ? ORDER BY id ASC LIMIT ?` →
  `event_json` each. (Entity-filtered read; `core/events.py` only offers the global since-cursor
  read — deviation D4.)
- `copy_text(conn, ticket_id) -> str` — §10.4 block, exactly (values `(none)` when null/empty;
  links section from the same query as `ticket_detail`, one line per link, `(none)` when empty):

  ```
  <title>
  state: <state>
  priority: <priority>

  success:
  <success.value | (none)>

  approach:
  <approach.value | (none)>

  plan:
  <plan.value | (none)>

  result:
  <result.value | (none)>

  recap:
  <recap | (none)>

  links:
  - <kind>: <from_id> -> <to_id>
  ```

- `board_view(conn, now) -> JsonDict` — §10.3. One `SELECT id, title, state, priority, deadline,
  project, fields, claim_lock, claim_expires, created_at FROM tickets WHERE state != 'dropped'`.
  Response `{"columns": [{"state": s.value, "cards": [...]} for s in STATE_ORDER]}` — six columns
  `needs_success…done`, `dropped` hidden by the WHERE. Card keys **exactly**: `id, title,
  priority, deadline, project, has_pending_proposal, has_running_claim` where
  `has_pending_proposal = gating_field_pending(TicketState(state), json.loads(fields))` (pending
  proposal on the *current gating field* — the same function dispatch eligibility uses; states
  without a gating field are always False) and `has_running_claim = has_active_claim(claim_lock,
  claim_expires, now)` (active *unexpired* claim — the single lease-liveness definition from
  `dispatch/logic/claims.py`). Within a column, cards sort by the §7.2 triple: priority rank
  (P0 first), deadline ascending NULLs last, `created_at` ascending (decision: reuse the one
  ordering vocabulary the spec defines rather than invent a second).
- `queues_view(conn, now, today_iso, item_approval_rows, item_overdue_rows) -> JsonDict` —
  `{"approvals": [...], "pickup": [...], "overdue": [...]}` (§4.5):
  - **approvals** — reuses `days.logic.carryover.approvals_digest` (the one §4.5 implementation)
    fed with: ticket rows `{id, state, fields: json.loads(fields), updated_at}` from
    `SELECT id, title, state, fields, updated_at FROM tickets WHERE state NOT IN
    ('done','dropped') ORDER BY id`, and `item_approval_rows` (from `sprints/views`, shape
    `{id, title, status_proposal: parsed dict}`). Pending age, precisely (as the digest defines
    it): gating-field proposal → `proposal.created_at`; sprint-item status proposal →
    `status_proposal.created_at`; `needs_review` ticket → the ticket's `updated_at` (the digest's
    documented DB-internal proxy for review-entry time — carried over unchanged so the system has
    exactly one definition of pending age; its wrinkle is concern C4). Interleaving: one ascending
    stable sort on `waiting_since` across all three kinds; ties preserve scan order (tickets by
    id, then items). Each digest entry `{entity_id, kind, waiting_since}` is enriched to
    `{entity_id, entity_type: "ticket" | "item" (id prefix "t"/"si"), kind
    (success|approach|plan|result|review|status), title, waiting_since}` via id→title maps built
    from the same two row sets.
  - **pickup** — reuse only: `candidates = dispatch_data.load_candidates(conn, now)`;
    `eligible = [c for c in candidates if is_eligible(c)]`; `eligible.sort(key=ordering_key)`
    (§7.2 ordering verbatim); titles via one `SELECT id, title FROM tickets WHERE id IN (...)`.
    Entries: `{ticket_id, title, state, priority, deadline}`. No eligibility or ordering rule is
    restated anywhere.
  - **overdue** — reuse `days.logic.carryover.overdue_list(ticket_rows, item_rows, today_iso)`
    (the rule: `deadline < today` strict, tickets not done/dropped, items not
    done/deferred_next_sprint — the disk's item interpretation, see concern C5) fed with ticket
    rows `{id, title, state, priority, deadline}` from `SELECT … WHERE deadline IS NOT NULL ORDER
    BY deadline ASC, id` and `item_overdue_rows` (same shape with `status`). Each digest entry is
    enriched with `entity_type` and `deadline` (from the source-row maps):
    `{id, entity_type, title, state, priority, deadline}`. Order: tickets (deadline asc) then
    items (deadline asc) — `overdue_list` preserves input order; inputs are ordered as stated.
  - `today_iso` is the **current planning date** (computed by the route from
    `planning_date(clock.now(), config.boundary_hour)`) — never the calendar date.

### 4.2 `src/planner/sprints/views.py`

Imports: `sqlite3`, `json`, sprints contracts/data, `sprints.logic.ranges`
(`DateRange`, `current_sprint_id`), core contracts, and `tickets/views` (`ticket_json`) +
`tickets/data` (`read_ticket`) — the single allowed cross-views import direction. No FastAPI, no
writes.

- `item_rollup(conn, item_id) -> dict[str, int]` — `SELECT state, COUNT(*) FROM tickets WHERE
  sprint_item_id = ? GROUP BY state`, zero-filled over all seven `TicketState` values (stable
  shape). Parentage = the `tickets.sprint_item_id` column (disk law; `belongs_to` links coexist —
  concern C6).
- `list_items(conn, *, status, project, sprint_id_filter) -> list[JsonDict]` —
  `sprint_id_filter` is `("null", None)`-aware: the literal string `"null"` → `sprint_id IS
  NULL`; `None` → no filter; else `sprint_id = ?`. Ordered priority rank (P0 first) then
  `created_at ASC, id`. Each entry: `{**item_json, "blockers_cleared": read_item(...).blockers_cleared}`
  (reuses the canonical derivation; N+1 accepted).
- `item_detail(conn, item_id) -> JsonDict` — `sprints_data.read_item` →
  `{**item_json(item), "blockers_cleared": cleared, "rollup": item_rollup(conn, item_id)}`.
- `list_sprints(conn) -> list[JsonDict]` — ids ordered `date_start DESC` → `sprint_json(read_sprint)`.
- `list_ideas(conn) -> list[JsonDict]` — `SELECT * FROM ideas ORDER BY created_at DESC, id` →
  `idea_json`.
- `approval_item_rows(conn) -> list[JsonDict]` — `SELECT id, title, status_proposal FROM
  sprint_items WHERE status_proposal IS NOT NULL ORDER BY id` →
  `{id, title, status_proposal: json.loads(...)}` (exactly the row shape
  `approvals_digest` consumes, plus `title` for enrichment).
- `overdue_item_rows(conn) -> list[JsonDict]` — `SELECT id, title, status, priority, deadline
  FROM sprint_items WHERE deadline IS NOT NULL ORDER BY deadline ASC, id` (predicate application
  stays in `overdue_list`).
- `sprint_current_view(conn, today_iso, now) -> JsonDict` — §5: ranges from `SELECT id,
  date_start, date_end FROM sprints` → `sid = current_sprint_id(today_iso, ranges)` (the §6.1
  "current" rule, reused). **No current sprint** → the pinned empty shape
  `{"sprint": null, "groups": {"todo": [], "active": [], "done": [], "blocked": [],
  "deferred_next_sprint": []}, "loose_tickets": []}`. Otherwise: `"sprint": sprint_json`;
  `"groups"`: items `WHERE sprint_id = ?` ordered priority-rank/`created_at`, grouped by
  `status.value`, each entry `{**item_json, "blockers_cleared", "rollup"}` (per-item reuse of
  `read_item` + `item_rollup`); `"loose_tickets"`: `SELECT id FROM tickets WHERE sprint_id = ?
  AND sprint_item_id IS NULL ORDER BY created_at, id` → `ticket_json(read_ticket(...), now)` each
  (§5's exact loose definition).

---

## 5. Shared helpers — home, import direction, conn lifecycle

All shared plumbing lives at the top of `src/planner/tickets/api.py` (owned file; the prompt's
"_shared section in one api.py"). Import direction is strictly one-way:
`sprints/api.py`, `days/api.py`, `dispatch/api.py` → `planner.tickets.api`. `tickets/api.py`
imports no other api module. Views modules import no api module. Public (non-underscore) names so
cross-module imports are ruff-clean:

```python
def get_config(request: Request) -> Config:
    return cast(Config, request.app.state.config)

def get_clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)

async def db_conn(request: Request) -> AsyncIterator[sqlite3.Connection]:
    conn = cast(Callable[[], sqlite3.Connection], request.app.state.conn_factory)()
    try:
        yield conn
    finally:
        conn.close()

DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]     # T09's dependency, as-is
Cfg = Annotated[Config, Depends(get_config)]
Clk = Annotated[Clock, Depends(get_clock)]

@contextmanager
def txn(conn: sqlite3.Connection) -> Iterator[None]:          # for NON-self-transacting calls only
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

def parse_enum[E: StrEnum](enum_cls: type[E], raw: str, what: str) -> E:
    try:
        return enum_cls(raw)
    except ValueError:
        raise PlannerError(ErrorCode.validation, f"invalid {what}", {what: raw}) from None
```

(`adapters` is never needed by T10 routes — replan execution is T11's; no accessor for it.)
The day-date resolver lives in `days/api.py` (only day routes need it) — Section 1.3.
`casts` are required because Starlette's `app.state` is untyped; this is the one sanctioned `Any`
boundary, converted immediately.

---

## 6. Smoke script — `orchestration/tickets/T10-domain-apis/smoke.py`

Same style as T09's: straight-line, `ok NN` prints, try/finally kills the server and removes the
temp dir, exits non-zero on first failed assert, final `SMOKE PASS (N checks)`. Boot: temp dir;
free port (reuse T09's `free_or`); subprocess `[REPO/".venv/bin/plan", "serve"]` with env
`PLAN_TEST_MODE=1`, `PLAN_FAKE_NOW="2026-07-04T12:00:00"` (planning date 2026-07-04),
`PLAN_DB_PATH=<tmp>/planning.db`, `PLAN_PORT`, `PLAN_LOGS_DIR=<tmp>/logs`,
`PLAN_DISPATCHER_LOCK_PATH=<tmp>/dispatcher.lock` (adapters resolve `auto`→fake under test mode);
`wait_ready` on `/api/meta`. Headers: HUMAN = `{}`; AGENT = `{"X-Plan-Actor": "planner-main"}`;
CLAIMED = `{"X-Plan-Run-Id": "run_x", "X-Plan-Claim": "claim_x"}`. All calls `httpx`, `--json`
bodies as noted; every step asserts status code AND the named fields.

1. **Create ticket** — POST `/api/tickets` `{title: "Golden path", priority: "P1"}` HUMAN → 200;
   assert `state == "needs_success"`, `ceiling == "needs_success"`, `at_cap == "propose"` (R2
   defaults), `priority == "P1"`, id starts `t_`, `claim_active is False`, `"claim_lock" not in
   body`. Save `t1`.
2. **Plain-agent propose parks at cap** — POST `/api/tickets/{t1}/propose/success`
   `{body: "S-cond"}` AGENT → 200; `state == "needs_success"` (at ceiling, propose → parks);
   `fields.success.proposal.body == "S-cond"`; `fields.success.proposal.proposed_by ==
   "planner-main"` (§7.6 actor passthrough).
3. **Approvals queue sees it** — GET `/api/queues` → approvals contains an entry
   `entity_id == t1, kind == "success", entity_type == "ticket"`.
4. **Accept WITHOUT pair → engine error** — POST `/api/tickets/{t1}/accept/success` `{}` HUMAN →
   400; `error.code == "grant_missing"`; `error.detail.missing == ["next_ceiling", "at_cap"]`
   (the engine's structured shape, proving no route pre-validation/defaulting).
5. **Accept with grant pair advances** — POST accept/success
   `{next_ceiling: "needs_plan", at_cap: "propose"}` HUMAN → 200; `state == "needs_approach"`,
   `ceiling == "needs_plan"`, `at_cap == "propose"`, `fields.success.value == "S-cond"`,
   `fields.success.proposal is None`. GET `/api/queues` → no approvals entry for `t1` anymore.
6. **Auto-accept below ceiling** — POST propose/approach `{body: "A"}` AGENT → 200;
   `state == "needs_plan"` (target ≤ ceiling → auto), `fields.approach.value == "A"`, ceiling and
   at_cap unchanged (`"needs_plan"`, `"propose"` — auto-accepts change neither, §4.4.7).
7. **(H) route with claim headers → agent_forbidden** — POST `/api/tickets/{t1}/state`
   `{to: "in_progress"}` CLAIMED → 400; `error.code == "agent_forbidden"`. Repeat with AGENT
   headers on POST `/api/tickets/{t1}/drop` → same code (both agent classes rejected).
8. **Edit-accept with "none" ceiling** — POST propose/plan `{body: "P-draft"}` AGENT → 200 parks
   (at ceiling). POST accept/plan `{edited_body: "P-final", next_ceiling: "none", at_cap: "stop"}`
   HUMAN → 200; `state == "in_progress"`, `fields.plan.value == "P-final"` (edited text stored
   exactly), `ceiling == "in_progress"` ("none" → newly entered state), `at_cap == "stop"`.
9. **Recap write** — PUT `/api/tickets/{t1}/recap` `{body: "recap v1"}` AGENT → 200;
   `recap == "recap v1"` (state past needs_success).
10. **Item lifecycle** — POST `/api/items` `{title: "Item A", project: "Vylo"}` HUMAN → 200
    (`status == "todo"`, save `i1`); PATCH `/api/items/{i1}` `{status: "active"}` AGENT → 200
    `status == "active"` (agent-permitted §3.2); POST `/api/items/{i1}/propose-status`
    `{to: "done"}` AGENT → 200 `status_proposal.to_status == "done"`; POST
    `/api/items/{i1}/accept-status` `{}` CLAIMED → 400 `agent_forbidden` ((H) coverage for items);
    POST accept-status `{}` HUMAN → 200 `status == "done"`, `status_proposal is None`.
11. **Sprint + current view** — POST `/api/sprints` `{name: "S1", date_start: "2026-07-01",
    date_end: "2026-07-12"}` HUMAN → 200 (save `sp1`); PATCH `/api/items/{i1}`
    `{sprint_id: sp1}` HUMAN → 200; PATCH `/api/tickets/{t1}` `{sprint_id: sp1}` HUMAN → 200;
    GET `/api/sprint/current` → `sprint.id == sp1` (2026-07-04 ∈ range),
    `groups.done[0].id == i1` with `rollup` dict present, `loose_tickets` contains `t1`
    (sprint_id set, no parent item).
12. **Day add/remove with `today`** — POST `/api/day/today/tickets` `{ticket_id: t1}` HUMAN → 200;
    `id == "day_2026-07-04"` (amendment 10 resolution through the fake clock), `tickets[0].id ==
    t1`. GET `/api/day/2026-07-04` → identical ticket list (literal date path). DELETE
    `/api/day/today/tickets/{t1}` → 200, `tickets == []`. GET again → still `[]` (§3.4 defer,
    association only).
13. **Links add + cycle reject** — POST `/api/tickets` `{title: "Blocker"}` HUMAN → save `t2`.
    POST `/api/links` `{from_id: t2, to_id: t1, kind: "blocks"}` HUMAN → 200 echo of the link.
    POST `/api/links` `{from_id: t1, to_id: t2, kind: "blocks"}` → 400,
    `error.code == "link_cycle"`.
14. **Full ticket read** — GET `/api/tickets/{t1}` → `blocked is True` (t2 open blocks it),
    `links` contains `{from_id: t2, to_id: t1, kind: "blocks"}`, `effective_sprint_id == sp1`,
    `run_summary.total == 0`, `day_ids == []`.
15. **Pickup queue** — GET `/api/queues` → pickup contains `t2` (needs_success at R2 default
    ceiling with propose → eligible branch (b)) and does NOT contain `t1` (blocked, and at
    ceiling with stop).
16. **Overdue** — PATCH `/api/tickets/{t2}` `{deadline: "2026-07-01"}` → 200; GET `/api/queues`
    → overdue contains `{id: t2, entity_type: "ticket", deadline: "2026-07-01"}` (07-01 <
    planning date 07-04).
17. **Board** — GET `/api/board` → 6 columns in STATE_ORDER, no `dropped` column; the
    `in_progress` column has a card `id == t1` whose key set is exactly
    `{id, title, priority, deadline, project, has_pending_proposal, has_running_claim}`, with
    `has_pending_proposal is False` and `has_running_claim is False`.
18. **Copy-text** — GET `/api/tickets/{t1}/copy-text` → 200, `content-type` startswith
    `text/plain`; body contains `"Golden path"`, `"state: in_progress"`, `"P-final"`, and a
    links line for `t2 -> t1`.
19. **Events** — GET `/api/tickets/{t1}/events` → kinds include `ticket_created`,
    `proposal_filed`, `proposal_accepted`, `state_changed`; ids strictly ascending.
20. **Ideas** — POST `/api/ideas` `{title: "An idea"}` HUMAN → 200 id starts `idea_`; GET
    `/api/ideas` → contains it, newest first.
21. **Day-plan (H) guard fires before plan checks** — POST `/api/day/today/plan/reject-all` `{}`
    CLAIMED → 400 `agent_forbidden` (proves reject_agents precedes the "day has no plan"
    validation). Then HUMAN → 400 `error.code == "validation"` (no plan exists yet — the
    validation ordering itself).
22. **Shutdown** — SIGINT the server; `returncode == 0`.

(Day-plan accept/invalidate against a real tree is not smoke-reachable without the boundary
adapter producing a plan — that path is exercised by e2e items 28/29 in later tickets; T10's
smoke covers the (H) gate and the no-plan validation edge, and the tree transforms themselves are
already unit-proven at stage 3.)

---

## 7. Deviations and concerns (orchestrator to record in decisions.md)

**D1 — ideas writer.** `POST /api/ideas` has no data-layer writer (only `seed/importer.py` inserts
ideas, inline) and `sprints/data.py` is outside T10's owned files. The INSERT + `idea_created`
event live in a private `_create_idea` inside `sprints/api.py`, mirroring the importer's INSERT
column list and event payload keys exactly (`{"title", "source"}`, `source="api"`). Trade-off:
the one-writer-per-edge rule is honored in spirit (exactly one API-side writer; the importer
remains the only other door, both inserting identical shapes), but the writer is homed in an api
module. Recommend relocating into `sprints/data.py` at integration (orchestrator glue).

**D2 — ticket title/project writers.** The route table pins `PATCH /api/tickets/{id}` fields
`title, priority, deadline, project, sprint_id`, but `tickets/data.py` has writers only for
priority/deadline/sprint. `_set_title`/`_set_project` live as private writers in `tickets/api.py`,
mirroring `set_priority`'s pattern (single-column UPDATE + `ticket_updated {field, from, to}`
event in one transaction), with `validate_title(config.title_max_chars)` and the
parented-project rejection mirroring `create_ticket`'s exact check. Same relocation
recommendation as D1.

**D3 — sprint date edits.** Route table says PATCH sprints covers "name/dates";
`update_sprint_field` whitelists only name + kickoff/review text. `_set_sprint_dates` (private,
`sprints/api.py`) reuses `sprints.logic.find_overlap` with self-exclusion and mirrors the
`sprint_updated` event shape. Same relocation recommendation.

**D4 — entity-filtered events read.** `core/events.py` declares itself the only reader of the
events table but offers only the global since-cursor read; `core/events.py` is not T10-touchable.
`list_events_for_entity` in `tickets/views.py` performs the read-only SELECT, mirroring
`read_events_since`'s row mapping. Flagged, not silently done.

**D5 — run reads.** No run reader exists anywhere; `get_run`/`runs_for_ticket`/`run_json` are
homed in `tickets/views.py` (ticket screen is the primary consumer; `dispatch/api.py` imports
them) — one shape definition, cross-domain read-only SQL on the runs table.

**D6 — `claim_lock` never serialized.** `ticket_json` omits `claim_lock` (authctx's "stored token
is never echoed" discipline extended to reads) and adds derived `claim_active`. A deliberate
divergence from a naive field-for-field dump of the `Ticket` contract.

**C1 — no route-level events needed.** Audited every route against its writer's events: creates,
proposals/accepts, state/grant/drop, notes/recap, item transitions/proposals/accepts, sprint
create/update/freeze/addenda, day materialize/brief/notes/add/remove/plan effects, links,
heartbeat/close/unblock — every mutation's events are appended by the writer or effect
application. The D1–D3 private writers append their own (they are writers, not routes). No route
appends events; no missing-event case found.

**C2 — pydantic 422s bypass the error envelope.** A wrong-JSON-type body (e.g. `title: 123`)
yields FastAPI's 422 `{"detail": [...]}`, not `{"error": ...}` — an app-level
RequestValidationError handler would live in `core/server.py`, which T10 must not touch.
Mitigated by defaulting every model field so *absence* always reaches engine/route `PlannerError`
paths; PATCH routes take raw dicts for the same reason. Residual exposure flagged for T12 (CLI
must exit 1 on any non-2xx, envelope or not) and for the orchestrator (optional T09 follow-up).

**C3 — PATCH multi-key non-atomicity.** Ticket/item/sprint PATCH with several keys applies one
self-committing writer per key (fixed documented order, key-set validated upfront); a mid-sequence
rejection leaves earlier keys applied. Honest per-writer atomicity; the UI edits one field at a
time. Flagged rather than papered over with a route-level mega-transaction (impossible anyway —
the writers self-BEGIN).

**C4 — needs_review pending age.** The approvals queue reuses the digest's `updated_at` proxy for
review-entry time (`days/logic/carryover.py`, documented there). Any later write to a
needs_review ticket (e.g. a result-notes edit) refreshes `updated_at` and re-ages the entry.
Consistent with the only existing definition; noted, not fixed here (a precise definition would
query the last `state_changed→needs_review` event — a candidate improvement, but it would fork
the system's definition of pending age across two call sites).

**C5 — overdue item statuses.** §4.5 says "not done/dropped" uniformly; items have no `dropped` —
the disk (`overdue_list`, stage-3 unit-tested) excludes `done` and `deferred_next_sprint`. Disk
wins; flagged as the interpretation in force.

**C6 — item parentage source.** Rollups/loose-tickets read `tickets.sprint_item_id` (the column),
not `belongs_to` links. Both exist in the model; the column is what `create_ticket` and
`get_effective_sprint_id` use. Flagged so the seed/links story stays coherent.

**C7 — run close outcome vocabulary.** The HTTP route restricts `outcome` to
`AGENT_CLOSE_OUTCOMES` (`done|blocked`, §8); `close_run` itself also accepts failure statuses
because T11's runtime closes runs through the same writer. Restriction implemented by membership
test against the imported contract constant — no duplicated rule text.

---

## 8. Ordering, risks, signature surprises

**Build order** (each step leaves imports resolvable): (1) `tickets/api.py` shared section +
models + marshallers; (2) `tickets/views.py` (serializers first — everything depends on
`ticket_json`); (3) `sprints/views.py`; (4) `tickets/api.py` handlers (queues last — needs
sprints/views helpers at the call site); (5) `sprints/api.py`; (6) `days/api.py`;
(7) `dispatch/api.py`; (8) ruff + mypy + unit suite green; (9) `smoke.py`. Gate: `./verify` runs
ruff, mypy strict, unit suite, compileall — T10 ships no committed tests; the smoke script plus
green gates are the bar.

**Signature surprises found on disk (bind the implementer):**

- `sprints/data.py` writers take `clock: Clock` (keyword); `tickets/data.py`, `days/data.py`,
  `dispatch/data.py`, `core/links.py` take `now: int`. Do not mix them up.
- `tickets_data.create_ticket` requires `title_max_chars` (from config) — the DDL's literal 200 is
  not the enforcement point.
- Ticket/sprint writers self-transact (`BEGIN IMMEDIATE`); days/dispatch/links-remove do not.
  Wrapping a self-transacting writer in `txn(conn)` raises `sqlite3.OperationalError` at runtime
  and no static check catches it — the per-route discipline in Section 1 is exact and must be
  followed literally.
- `dispatch_data.heartbeat` returns the new expiry `int`, not a Run; `close_run` returns `None` —
  both responses re-read the run row.
- `tree.accept_node`/`invalidate_child` with a nonexistent position silently emit an event naming
  the missing node — hence `_require_child` in the route.
- `add_day_ticket` returns `bool` (False = already present) and is idempotent; the route ignores
  the flag and returns the day view either way.
- `freeze_kickoff`/`freeze_review` are idempotent latches (second call returns the sprint, emits
  nothing) — the route treats 200-on-repeat as correct.
- `require_claim` needs a **ticket id**; run routes resolve `run → ticket_id` first (Section 1.4).
- `submit_replan` is enqueue-only (latest-wins slot); call it after the commit, never inside.

**mypy strict hazards:** `app.state` attributes are untyped → `cast` at the accessors (Section 5)
and nowhere else; `sqlite3.Row` indexing returns `Any` → coerce immediately (`str(...)`,
`int(...)`, `json.loads(str(...))`) exactly as the data layers do; the async-generator dependency
is typed `AsyncIterator[sqlite3.Connection]`; PEP-695 generics (`parse_enum[E: StrEnum]`) are
already used on disk (`scheduler._call_with_timeout[T]`); handlers returning `JsonDict` satisfy
FastAPI's response typing; `PlainTextResponse` route returns `str` with
`response_class=PlainTextResponse`.

**Concurrency/runtime risks:** all handlers `async def` (Section 0 — sqlite thread affinity);
per-request connections only; `BEGIN IMMEDIATE` contention resolves via the connection's
`busy_timeout` (set in `db.connect`); GET `/day/{date}` writes (materialization) are spec-mandated
and autocommit-safe.
