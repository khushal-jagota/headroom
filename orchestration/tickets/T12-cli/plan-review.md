# T12 plan review — codex exec output + orchestrator dispositions

## Codex findings (verbatim)

1. plan.md:105 — `send()` returns any 2xx body before checking for `{"error": ...}`. Correct fact: T12 ticket line 11 and T01 line 1140 require any structured error envelope to go to stderr and exit 1.

2. plan.md:324 — `ticket list --json` emits `data["tickets"]`, not raw response JSON. Correct fact: `GET /api/tickets` returns `{"tickets": [...]}` (tickets/api.py:246), and T01 line 1140 says `--json` prints raw response JSON. Same violation at plan lines 377 (`item list`, actual `{"items": [...]}`) and 417 (`idea list`, actual `{"ideas": [...]}`).

3. plan.md:493 — `queue approvals/pickup/overdue --json` emits only a selected array. Correct fact: the only live route is `GET /api/queues`, returning `{"approvals": ..., "pickup": ..., "overdue": ...}` (tickets/api.py:452, tickets/views.py:397). The smoke at plan line 600 also asserts the sliced shape.

4. plan.md:476 — `run close` allows omitted `--summary` and sends JSON `null`. Correct fact: SPEC line 180 and T01 line 1130 define `plan run close --outcome <o> --summary -`; the CLI should validation-exit 1 when the explicit summary source is absent.

Codex confirmations: all route/method/body mappings match the live routers; header names/defaults match authctx.py; amendment-6 body handling correct (no implicit stdin, no double-read, `-` falls back to PLAN_TICKET_ID); no resolution verbs; seed report fields match MigrationReport; D4 layering respected. "NO OTHER VIOLATIONS".

## Orchestrator dispositions

1. **ACCEPT.** Today no server path returns a 2xx carrying `{"error": ...}` (PlannerError is rendered with a non-2xx status), so this cannot fire in practice — but the ticket's wording is unconditional ("`{"error": ...}` response → stderr + exit 1") and the check is one isinstance/key test. `send()` gains: on 2xx, if the parsed body is a dict containing an `"error"` key, route to `_fail_response` (exit 1). No legitimate success view emits a top-level `"error"` key, so no false positives. → plan amendment A1.

2. **ACCEPT.** T01 §14 convention is literal: `--json` prints the raw response JSON. `ticket list`/`item list`/`idea list` under `--json` emit the full response object (`{"tickets": [...]}` / `{"items": [...]}` / `{"ideas": [...]}`), not the sliced array. Human lines are still rendered from the array. → plan amendment A2.

3. **PARTIALLY ACCEPT.** The finding is right that the planned bare-array output diverges from the raw response. But emitting the full three-section `/api/queues` response for each queue verb would make `plan queue approvals|pickup|overdue --json` byte-identical — collapsing three §8 verbs into one, against their meaning ("the derived views", one per verb). Ruling: each queue verb emits the single-section object under its response key — `{"approvals": [...]}`, `{"pickup": [...]}`, `{"overdue": [...]}` — i.e. the raw response filtered to the verb's section, keys preserved. The smoke asserts `json.loads(stdout)["approvals"]`. → plan amendment A3.

4. **REFUTE.** The click stub on disk (`src/planner/cli/main.py`, `run_close`) declares `--summary` with `default=None` — optional. T12's scope line is explicit that the verb tree and flags are already fixed and must not change; flag required-ness is flag semantics. The server contract agrees: `CloseRunBody.summary: str | None = None` and `dispatch_data.close_run(summary=...)` accept a null summary. The SPEC §8 line (`plan run close --outcome <o> --summary -`) documents the canonical agent invocation, not a required flag — the same line's `--outcome` IS marked required in the stub, showing required-ness was deliberately assigned per flag in T01. Behavior stands: `--summary` absent → `"summary": null`; `--summary <spec>` → body read via `_read_source` (`-` = stdin). → no plan change; noted in plan amendments for the implementer.
