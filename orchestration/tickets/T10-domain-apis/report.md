# T10 report — domain APIs over the canonical writers + derived views

Closed by a second orchestrator: the first died mid-pipeline after implementation landed but
before verification, diff review, or reporting. On arrival the implementation was already
complete — all 46 planned routes present (20 tickets/links/board/queues, 16
sprints/items/ideas/current, 8 days, 2 runs), both views modules full, zero
`NotImplementedError` left in the owned files, gates green. What was missing: any smoke run,
the codex diff review, and this report.

## What was built (per plan.md, amendments §9 binding)

- `src/planner/tickets/api.py` — shared plumbing (per-request conn dependency, `txn`,
  `parse_enum`, Cfg/Clk/Ctx annotations), 20 handlers, grant marshallers with pure
  None-passthrough, A1 gap-fill writers `_set_title`/`_set_project`.
- `src/planner/sprints/api.py` — 16 handlers, A1 writers `_create_idea`/`_set_sprint_dates`,
  A7 item-deadline marshalling.
- `src/planner/days/api.py` — 8 handlers, `resolve_day_id` (`today` + A6 canonical ids),
  `_day_view` with A2 txn-wrapped materializing read, post-commit `submit_replan` on invalidate.
- `src/planner/dispatch/api.py` — heartbeat/close with run→ticket resolution and unconditional
  `require_claim`.
- `src/planner/tickets/views.py` — serializers (`claim_lock` never echoed, derived
  `claim_active`), ticket detail/list, run/event readers (D4/D5), copy-text (§10.4), board
  (§10.3, §7.2 ordering), queues (approvals with A5 review aging, pickup via dispatch logic
  verbatim, overdue via `overdue_list`).
- `src/planner/sprints/views.py` — item/sprint/idea serializers, rollups, current-sprint view
  (§5), approval/overdue item row providers.
- `orchestration/tickets/T10-domain-apis/smoke.py` — 23-check golden-path smoke against a booted
  test-mode server on a temp DB.

## Test results

No committed pytest tests are named by this ticket (the bar is gates + smoke; T10 ships no test
files). Full gate run after the review fixes:

- `ruff check .` — All checks passed!
- `mypy src/` (strict) — Success: no issues found in 83 source files
- `pytest tests/ -q` — 81 passed (whole unit suite, unchanged by T10)
- smoke — all checks including both amendment proofs (A6 compact-ISO day id, A7 deadline
  validation incl. non-string) and the (H)/claim guards:

```
ok 22 — plan reject-all: agent_forbidden before 'day has no plan' validation
ok 23 — SIGINT terminates the live server with returncode 0
SMOKE PASS (23 checks)
```

## Review outcomes

- Plan review (plan-review.md): 7 findings — 5 accepted as binding amendments A1/A2/A5/A6/A7,
  2 refuted in writing (board card `id`; overdue item statuses).
- Impl review (impl-review.md): 2 MAJOR findings, both accepted and fixed directly — close_run
  now authenticates (resolve run → `require_claim`) before parsing the outcome; item-deadline
  marshalling rejects non-strings instead of laundering them through `str()`. All nine audit
  areas otherwise clean. Gates re-run green after both fixes.

## Deviations from ticket

None in route behavior. Four gap-fill writers live in api modules because T10 owns no `data.py`
(recorded deviations D1–D3, hardened writer-shaped by amendment A1).

## Concerns / requests for the integrator

1. **Relocation request (from plan review finding 1):** move `_set_title`/`_set_project`
   (tickets/api.py) into `tickets/data.py` and `_create_idea`/`_set_sprint_dates`
   (sprints/api.py) into `sprints/data.py` as integration glue. They are writer-shaped for pure
   cut-paste (no FastAPI/pydantic in signatures or bodies).
2. **SPEC clarification (plan review finding 4):** overdue excludes `deferred_next_sprint` items
   though §4.5 literally says "not done/dropped" — the stage-3 `overdue_list` interpretation is
   reused rather than forked (concern C5).
3. **C2 residual:** wrong-JSON-type bodies on pydantic-modeled routes yield FastAPI 422s, not the
   `{"error": ...}` envelope (an app-level RequestValidationError handler would live in T09's
   `core/server.py`). Absence-shaped inputs always reach engine errors; PATCH routes take raw
   dicts. Candidate T09 follow-up; T12's CLI must treat any non-2xx as failure.
4. **C3:** multi-key PATCH applies one self-committing writer per key (fixed order, key set
   validated upfront) — a mid-sequence rejection leaves earlier keys applied.
5. Run-route claim ordering (impl fix 1) is not smoke-reachable — exercising it end to end needs
   T11's runtime to mint a real run/claim; covered by inspection + codex.
