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
