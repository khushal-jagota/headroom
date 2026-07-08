# Plan — system-friction cleanup, tickets 1–4

## Objective

Clean up four system-level boundary problems without changing product behavior:

1. canonical writer ownership,
2. retired runtime config,
3. dispatcher switch semantics,
4. Hermes run primitive ownership.

This is a boundary cleanup wave, not a feature wave. The goal is that a cold reader
can trust where writes live, which runtime knobs are real, when dispatch is enabled,
and which Hermes path production uses.

## Explicit Non-Goals

- **Do not solve the two blocking shapes here.** Ticket blockers
  (`links.kind='blocks'`) and sprint-item `blocked_by` are important enough to
  discuss separately with the owner before any unification plan.
- Do not redesign the frontend.
- Do not change ticket state-machine behavior, event payloads, readiness behavior,
  Hermes session semantics, or chat behavior.
- Do not remove current historical orchestration notes just because they mention
  older runtime designs. This cleanup updates live docs and live code. Old ticket
  artifacts can stay as history unless they actively mislead current docs.

## Source Of Truth

Current live code and tests are the contract:

- `src/planner/sprints/api.py`, `src/planner/sprints/data.py`
- `src/planner/tickets/api.py`, `src/planner/tickets/data.py`
- `src/planner/core/config.py`, `config.yaml`
- `src/planner/core/loops.py`, `src/planner/runtime/system_a.py`,
  `src/planner/runtime/system_b.py`
- `src/planner/minds/shared_gateway.py`, `src/planner/minds/runner.py`,
  `src/planner/minds/__init__.py`
- `tests/unit/test_sprints.py`, `tests/unit/test_tickets_engine.py`,
  `tests/unit/test_authctx_routes.py`, `tests/unit/test_system_a.py`,
  `tests/unit/test_system_b.py`, `tests/unit/test_minds.py`
- `docs/systems.md`, `docs/systems.html`, `docs/employee-runtime.md`,
  `docs/chat.md`, `docs/cli.md`

`SPEC.md` is retired. Do not revive old spec promises such as claims, run rows,
breakers, or per-run wall-clock failure.

## Integration Rule

Implement as four serial tickets. They are small enough that parallelism is not
worth the integration risk. Run `./verify` after each landed ticket. Use Codex CLI
read-only review after Ticket 1 and Ticket 4 because those change architectural
boundaries; use it for Ticket 2 or 3 if the implementation touches more than the
named files.

## Ticket SF1 — Move Obvious Writers To Canonical Writer Modules

### Purpose

Make the writer boundary true for the obvious cases: API modules should parse,
authorize, and call writers; they should not own domain mutation logic.

### Scope

Move these API-local writers:

- `src/planner/sprints/api.py::_create_idea`
- `src/planner/sprints/api.py::_set_sprint_dates`
- `src/planner/tickets/api.py::_set_title`
- `src/planner/tickets/api.py::_set_project`

Target homes:

- `src/planner/sprints/data.py`
  - `create_idea(conn, *, title, body, project, now) -> sqlite3.Row | JsonDict-compatible object`
  - `set_sprint_dates(conn, sprint_id, *, date_start, date_end, clock) -> Sprint`
- `src/planner/tickets/data.py`
  - `set_title(conn, ticket_id, *, title, title_max_chars, now) -> Ticket`
  - `set_project(conn, ticket_id, *, project, now) -> Ticket`

The final function names should describe what they do. Avoid underscore-private
names in the data modules unless the function is only a helper.

### Out Of Scope

- Do not move `chat_session_key` writes out of `src/planner/chat/service.py`.
  They coordinate with the gateway and need a separate design if they move.
- Do not change API route shapes.
- Do not change event kinds or payloads.
- Do not merge ticket priority/deadline/sprint writers with title/project; they
  already live in `tickets/data.py`.

### Implementation Notes

- Preserve the current transaction boundaries:
  - moved sprint writers should use `sprints.data._tx`, not `tickets.api.txn`;
  - moved ticket writers should use `tickets.data._txn`.
- Preserve existing validation:
  - title uses `admission.validate_title`;
  - project rejects parented tickets with the existing public error shape:
    `ErrorCode.validation` and message `"project is derived when parented"`;
  - sprint dates validate ISO dates, order, and overlap excluding the edited sprint.
- Preserve exact event payloads:
  - `idea_created`: `{"title": title, "source": "api"}`
  - `sprint_updated`: field/from/to for date changes only when changed
  - `ticket_updated`: field/from/to for title and project

### Tests

Add or update focused unit tests:

- `tests/unit/test_sprints.py`
  - direct `sprints.data.create_idea` writes the idea row and one
    `idea_created` event;
  - direct `sprints.data.set_sprint_dates` rejects overlapping dates and appends
    the same `sprint_updated` event payloads for successful changes.
- `tests/unit/test_tickets_engine.py`
  - direct `tickets.data.set_title` updates title and appends the same
    `ticket_updated` event;
  - direct `tickets.data.set_project` rejects a parented ticket and updates an
    unparented ticket with the same `ticket_updated` event;
  - the parented-ticket rejection asserts the exact current error code and
    message above, so it does not drift to `ErrorCode.sprint_derived`.
- `tests/unit/test_authctx_routes.py`
  - successful human `PATCH /api/tickets/{id}` for `title` returns the renamed
    ticket and writes the value;
  - successful human `PATCH /api/tickets/{id}` for `project` returns the updated
    project and writes the value.

Do not add broad e2e tests for this ticket. The missing surface is route
marshalling for the moved writers, so focused route tests are enough.

### Acceptance

- API modules no longer contain these mutation helpers.
- Moved writers are covered directly.
- `./verify` passes.
- Codex CLI review reports no event drift, transaction drift, or new import cycles.

## Ticket SF2 — Remove Retired Runtime Config Knobs

### Purpose

Make config describe the live runtime, not the retired claim/run/breaker model.

### Scope

Remove these live config fields and environment overrides:

- `claim_ttl_seconds` / `PLAN_CLAIM_TTL_SECONDS`
- `max_runs` / `PLAN_MAX_RUNS`
- `failure_limit` / `PLAN_FAILURE_LIMIT`
- `run_max_seconds` / `PLAN_RUN_MAX_SECONDS`

Files likely touched:

- `config.yaml`
- `src/planner/core/config.py`
- tests that instantiate or assert `Config`
- docs that describe live config/runtime behavior
- `docs/systems.md` and `docs/systems.html` friction text after the cleanup lands

### Compatibility Choice

Prefer true removal from `Config`. If unknown keys in `config.yaml` are already
ignored, no compatibility shim is needed. Do not keep these fields as inert
attributes; that is the current problem.

If a downstream local config may still contain these keys, the loader can ignore
them naturally because it only reads named keys. Do not surface them in the
dataclass, docs, or checked-in yaml.

### Adjacent Audit

Audit `hermes_bin` and `hermes_profile` during this ticket, but do not remove them
unless the implementation proves they are also unused in live code and tests.
The required cleanup target is the four retired runtime knobs above.

### Tests

Add direct config tests because current coverage is indirect through fixtures:

- `load_config(path=None, env={})` exposes current live defaults and no retired
  attributes.
- A temporary yaml containing the retired keys still loads, but the returned
  `Config` has no retired fields.
- Retired environment variables do not create attributes and do not affect live
  config.

The tests can live in a new `tests/unit/test_config.py`.

### Docs

Update:

- `config.yaml` comments so `dispatch_enabled` no longer says "re-read every tick"
  if Ticket SF3 has not landed yet. If SF3 is separate, this ticket can leave that
  one comment for SF3 but must remove the four retired knobs.
- `docs/systems.md` and `docs/systems.html` so the retired-config friction item is
  either removed or changed to a short note in a completed cleanup list.

### Acceptance

- `Config` has no claim, max-run, failure-limit, or run-timeout fields.
- `rg` over `src tests docs config.yaml` finds no live references to the retired
  names, except in old orchestration history if intentionally left alone.
- `./verify` passes.

## Ticket SF3 — Make `dispatch_enabled` Startup-Only

### Purpose

Make the dispatcher switch match live behavior: it decides whether System A starts.
It is not a live per-tick kill switch.

### Scope

Files likely touched:

- `src/planner/core/config.py`
- `src/planner/core/loops.py`
- `config.yaml`
- `docs/employee-runtime.md`
- `docs/systems.md`, `docs/systems.html`
- direct tests for config or loops

### Implementation

- Delete `read_dispatch_enabled` from `src/planner/core/config.py` if no live code
  imports it.
- Update comments:
  - `config.yaml`: "System A startup switch" or equivalent.
  - `core/loops.py`: guarded at startup, not dynamically re-read.
- Keep behavior unchanged:
  - `start_background_loops` starts System A only when `config.dispatch_enabled`
    is true and the machine lock is won;
  - when false, server still starts without System A.

### Tests

Add or update a focused test:

- With `Config.dispatch_enabled=False`, `start_background_loops(...)` returns a
  handle whose `system_a is None` and does not acquire the machine lock.

Use a fake or monkeypatched lock/gateway if needed. Keep this hermetic; do not
spawn Hermes or start a real server.

If Ticket SF2 added `tests/unit/test_config.py`, include a config-load assertion
there for `PLAN_DISPATCH_ENABLED` and yaml `dispatch_enabled`.

### Docs

Update current docs so no live document implies per-tick re-reading. Old
orchestration history can remain as history.

### Acceptance

- No live code or current docs mention per-tick `dispatch_enabled` re-read.
- `read_dispatch_enabled` is gone unless a real live caller remains.
- Disabled-start behavior is tested.
- `./verify` passes.

## Ticket SF4 — Make Hermes Production Path And Runner Types Explicit

### Purpose

Make it obvious that production execution uses `SharedGateway`, while preserving
the shared result/event types that production currently imports from
`minds.runner`.

### Current Facts

- `src/planner/runtime/system_b.py` uses `SharedGateway`.
- `src/planner/core/server.py` installs a `SharedGateway` owner in production.
- `src/planner/minds/shared_gateway.py` lazily spawns the gateway child.
- `src/planner/minds/shared_gateway.py` imports `OnEvent` and `RunResult` from
  `src/planner/minds/runner.py`; those types are part of the live production
  path today.
- `src/planner/minds/runner.py` still exposes the standalone `run_step`
  primitive, and `src/planner/minds/__init__.py` re-exports it.
- `tests/unit/test_minds.py` directly tests `run_step`.

### Decision Path

Start by tracing current imports:

- Treat `OnEvent`, `RunResult`, and `RunStatus` separately from `run_step`.
  Production uses those shared protocol types through `SharedGateway`.
- If only tests and `minds/__init__.py` import `run_step`, keep `run_step` as an
  explicit smoke/test primitive or remove only that function after proving its
  coverage is duplicate.
- If any live production code imports `run_step`, stop and re-plan; this ticket's
  premise would be wrong.

### Preferred Outcome

Keep shared Hermes protocol types in a production-named home and make the
standalone primitive's role explicit:

- Move or split `OnEvent`, `RunResult`, and `RunStatus` into a neutral module
  such as `planner.minds.contracts`, then update `SharedGateway`, System B
  tests, and `run_step` to import them from there.
- Change the `runner.py` module docstring to say `run_step` is not the
  production runtime path; `SharedGateway` is.
- Stop re-exporting `run_step` from `planner.minds.__init__` unless a live
  caller needs the package-level import. Keep or re-export shared contract types
  only if that package-level import is still useful and clearly documented.
- Update tests to import `run_step` directly from `planner.minds.runner`.
- Update docs to say production path is `SharedGateway`; `run_step` is a
  low-level smoke/test primitive if retained.

Retire only `run_step` if the review finds its tests duplicate `SharedGateway`
coverage and no useful smoke contract remains. Do not retire `runner.py` as a
module while production still imports shared types from it; move those types
first or keep the module with accurate documentation.

### Out Of Scope

- Do not rewrite `SharedGateway`.
- Do not change chat, command, history, stream, or System B session semantics.
- Do not change Hermes home/provisioning behavior.

### Tests

If retained:

- Existing `tests/unit/test_minds.py` `run_step` tests still pass after direct
  imports.
- `tests/unit/test_system_b.py` and any other production-path tests import
  `RunResult` / `OnEvent` from the neutral contracts module if the types move.
- Add one small test or assertion only if needed to pin the "not production path"
  export boundary.

If retired:

- Remove obsolete `run_step` tests, then ensure `SharedGateway` tests still cover
  production behavior and shared result typing.

### Acceptance

- A reader sees one production Hermes path: `SharedGateway`.
- Any remaining `run_step` path is explicitly labeled test/smoke-only.
- Package exports do not make the standalone primitive look like the default
  public path.
- Shared result/event types are not mislabeled as test-only while production uses
  them.
- `./verify` passes.
- Codex CLI review reports no accidental production path change.

## Implementation Order

1. SF1 writer relocation.
2. SF2 retired config removal.
3. SF3 dispatch startup-only semantics.
4. SF4 Hermes primitive ownership.

SF2 and SF3 are related, but keep them separate: SF2 removes dead knobs; SF3
clarifies the one live switch. Combining them would make review less precise.

## Review Prompts

### SF1 Codex Review

Ask Codex to compare the diff against current behavior:

> Review the writer-relocation diff. Focus only on event payload drift, transaction
> boundary drift, validation drift, and import-cycle risk. The change should move
> obvious API-local writers into data modules without changing behavior.

### SF4 Codex Review

Ask Codex to compare the diff against production runtime:

> Review the Hermes primitive cleanup. Focus only on whether production still uses
> SharedGateway, whether shared RunResult/OnEvent typing remains a production-safe
> contract, whether any run_step behavior was accidentally removed from a live path,
> and whether tests still cover the intended smoke/test primitive if retained.

## Done Condition

All four tickets have landed serially, `./verify` passes after the final ticket,
`docs/systems.md` and `docs/systems.html` no longer list these four items as open
friction, and `PROGRESS.md` plus `decisions.md` record the cleanup decisions.
