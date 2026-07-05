# T10 implementation review — codex findings + orchestrator dispositions

Reviewer: `codex exec` (non-interactive, repo root), pointed at the six owned source files, the
smoke, ticket.md, plan.md (§9 amendments binding), the stage-3 data layers, authctx, carryover,
dispatch logic, and SPEC §3/§4/§5/§6/§7/§8/§9/§10. Audit areas: (H)-route completeness, grant-pair
passthrough, `today` resolution + A6, board card shape, one-writer-per-edge + A1, transaction
discipline + A2, queues + A5, §7.6 claim plumbing, serialization + D6 + A7.

Codex's clean verdicts: **A** all 14 (H) routes call `reject_agents(ctx)` first, no non-(H) route
calls it; **B** grant-pair passthrough clean (absent halves reach the engine as `None`); **C**
`today`/A6 day-id resolution correct; **D** board six columns, exact card keys, §7.2 ordering;
**E** no route-level events or state mutation outside the four A1 gap-fill writers; **F**
self-transacting writers called bare, non-self-transacting mutations wrapped, `_day_view` wraps
`read_day` (A2); **G** queues use A5 review aging, dispatcher pickup primitives, planning-date
overdue; **I** `claim_lock` never serialized, response shapes match the plan.

## Findings and dispositions

### 1. MAJOR — `close_run` parsed `outcome` before resolving the run and calling `require_claim`
**Disposition: ACCEPTED — fixed directly (`src/planner/dispatch/api.py`).** A malformed outcome
returned `validation` without the claim check ever running, so "require_claim unconditionally"
(plan §0) held only on the happy path. The plan's own handler text (§1.4 #2) listed parse-first —
a self-inconsistency resolved in favor of the §0 statement and the repo's auth-before-validation
discipline (the same ordering the smoke proves for `reject_agents` on day-plan routes). The
handler now resolves `run → ticket_id`, calls `require_claim`, then parses/restricts the outcome.
Not smoke-reachable (creating a real run needs T11's dispatcher runtime); verified by inspection
and covered by the unchanged unit suite.

### 2. MAJOR — `_marshal_item_deadline` laundered non-strings via `str(raw)`
**Disposition: ACCEPTED — fixed directly (`src/planner/sprints/api.py`).** PATCH items takes a raw
dict, so a JSON number `20260704` stringified to compact ISO, passed validation, and the original
**int** went to `update_item_field` (which does not validate) — a type-corrupt deadline in the DB.
The marshaller now rejects any present non-string with the same `validation` envelope before
parsing. Smoke step 11 gained the assertion: `PATCH /api/items/{i1}` `{deadline: 20260704}` → 400
`validation`.

## Outcome

Both findings fixed; gates re-run after the fixes: ruff clean, mypy strict clean (83 files), unit
suite green (81 tests), smoke `SMOKE PASS (23 checks)`.

<details>
<summary>Raw codex output (verbatim findings + clean areas)</summary>

```
**Violations**

MAJOR — src/planner/dispatch/api.py:45
`close_run` parses and rejects `outcome` before resolving `run_id -> ticket_id` and before
`require_claim`. A bad `outcome` can return validation without ever requiring claim headers. Plan
§0 requires run routes to resolve the run to a ticket id and call `require_claim` unconditionally;
SPEC §7.6 requires run close writes to carry matching claim headers. Source: plan.md:81,
SPEC.md:165.

MAJOR — src/planner/sprints/api.py:152
`_marshal_item_deadline` validates `date.fromisoformat(str(raw))`, then PATCH passes the original
raw value to `update_item_field` at line 220. A JSON number like `20260704` is accepted via
stringification instead of being rejected as a non-string deadline. A7 requires present non-null
item deadlines to be route-marshalled as the original string before reaching writers that do not
validate it. Source: plan.md:1002, SPEC.md:38.

**Clean Areas**

A clean — all 14 pinned `(H)` routes call `reject_agents(ctx)` as first handler statement; no
non-`(H)` route calls it.
B clean — accept/edit-accept passes absent grant halves through as `None`; standalone grant has
route-level missing/invalid handling.
C clean — all day routes resolve through `resolve_day_id`; `today` uses planning date and
non-`today` ids are built from parsed dates.
D clean — board view has six non-dropped columns and exact card keys with §7.2 ordering.
E clean — no route appends events outside the four private A1 writers; no other route-level state
mutation found.
F clean — ticket/sprint data writers are called bare; days/dispatch/link mutations are wrapped;
`_day_view` wraps `read_day`.
G clean — queues use A5 review aging, dispatcher pickup primitives, and planning-date overdue
logic.
I clean except the A7 deadline violation above — `claim_lock` is not serialized, and the other
response shapes match the plan.
```

</details>
