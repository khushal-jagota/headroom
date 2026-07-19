# S3 resume — ground truth (2026-07-18)

Re-established from scratch after the prior orchestrator stalled. HEAD `c215d6d` (S2b). Sole
writer now. No verify was running at resume (`pgrep -f scripts/verify.py` clean).

## git diff HEAD → plan wave mapping

Backend waves 1–4 + the Collision #B test ingress are ON DISK and complete. The FRONTEND wave
(Wave 5) and PLAYWRIGHT wave (Wave 6) are NOT started — `web/` has ZERO changes, no
`tests/e2e/test_ticket_neutral_pane.py`, and no `web/tests/*` for the ticket pane.

New source (untracked):
- `src/planner/hermes_backend/pool_step_gateway.py` — Wave 1 `PoolStepGateway` + native→on_event shaping.
- `src/planner/runtime/step_gateway.py` — the `StepGateway` Protocol (runtime-local, minds/ untouched). Verified: type-only, matches the runner surface.

Modified backend (mapped to waves):
- `hermes_backend/employee_child_pool.py` (+255) — Wave 1 turn-observer seam + on_frame third sink + `interrupt_live_turn`; Wave 2 `stored_session_resolver` + on-demand resolve + widened `on_stored_session_bound` + rebind fail-closed fix.
- `hermes_backend/composition.py` (+80) — Wave 2 per-employee persistence dispatch (Chief vs ticket) + adoption resolver; pool-before-loops selection.
- `hermes_backend/scripted_relay_child.py` (+66) — extended stateful child driving disposition ACKs + step frames.
- `core/server.py` (+149) — Wave 3 `_lifespan` flag-on compose pool before loops + `step_gateway`; generalized `_assert_single_employee_owner`; **Collision #B (a): composes REAL EmployeeStepRunner over PoolStepGateway in relay TEST mode, exposes it as `app.state.employee_step_runner`.**
- `core/loops.py` (+11) — Wave 3 `start_background_loops` gains `step_gateway`, keeps `shared_gateway`.
- `core/testmode.py` (+19) — Collision #B (a) `POST /test/run-step/{ticket_id}` → `runner.try_run_automatic_step`, test-gated.
- `chat/service.py` (+39) — Wave 3 crossover guard predicate widened `chief_pool_owned` → `entity_pool_owned(entity_id)` + settle-not-resume for any pool-owned entity.
- `runtime/employee_step_runner.py` (+5) — Wave 1 one-line annotation widen `gateway: SharedGateway` → `gateway: StepGateway`. Confirmed exactly one annotation change.
- `tickets/data.py` (+47) — Collision #3 call-only `bind_pool_employee_session_id` ownership-CAS writer.
- `tickets/api.py` (+37) — Wave 4 by-ticket-id worker-self resolution route.
- `cli/main.py` (+30) — Wave 4 `worker_my_ticket` reads `PLAN_TICKET_ID` first + transitional Hermes-env fallback (ruling #1).

Rulings recorded: `decisions.md` D-s3-collision-rulings folds all five (#A disposition settlement, #1 transitional CLI fallback, #3 CAS writer, #4 worker gateway stays for non-ticket consumers, #B test ingress). Contract amended on disk with the same five.

Non-ticket tree noise (ignored): `.claude/worktrees/frontend-shared-components`,
`skills/panels-chief-of-staff/SKILL.md`, `skills/panels-worker-initiative-planning/SKILL.md`.

## Focused test run at resume (all GREEN)

- New S3 unit suites — 49 passed: `test_pool_step_gateway.py`, `test_pool_ticket_adoption.py`,
  `test_hermes_backend_ticket_composition.py`, `test_hermes_backend_ticket_step_composition.py`,
  `test_worker_cli_identity.py`.
- Modified existing unit suites — 37 passed: `test_hermes_backend_chief_composition.py`,
  `test_hermes_backend_new_conversation.py`, `test_human_chat_turn.py`.
- Runtime suites (eligibility/discovery/step-runner) — 154 passed, unchanged:
  `test_automatic_employee_step_discovery_loop.py`, `test_automatic_employee_step_eligibility*.py`,
  `test_employee_step_runner.py`.

No partial/broken backend code found. Backend is complete per plan; nothing to finish before Wave 5.

## Step B — frontend + Playwright waves COMPLETE (2026-07-19)

The frontend implementer (Opus) hit an API disconnect (ECONNRESET) at the very end of its run,
so the orchestrator re-verified every gate itself. All work landed and is green.

Wave 5 — ticket pane cutover:
- `web/src/routes/TicketRoute.svelte` — the `<aside class="chat-rail">` block swapped to the
  ChiefOfStaffRoute tri-state pattern: `$relayChief === "enabled"` → `<ChiefNeutralPane
  entityId={stableId} label={detail.title || "Ticket"} />` (reuses the entity-generic S2b
  component, no duplication); `disabled` → legacy `<ChatPanel>`; `error`/`unknown` →
  retry/loading placeholders. `chatGatewayStatus` made LAZY (opened only in the disabled branch,
  `chatStatus?.dispose()`) — closes S2B-ROUTE-001 for the ticket (flag-on never touches
  `/api/chat/{ticket}/status`). Confirmed: no App.svelte / BoardRoute / ChiefNeutralPane /
  neutralPane.ts edits — BoardRoute's ticket branch already delegates to `<TicketRoute>`, so the
  one edit covers both; the one meta flag (`relay_chief_enabled` = `relay_backend_enabled`)
  governs Chief AND tickets, so `relayChief` is reused as-is (zero capabilities.ts churn).
- `web/tests/ticket-neutral-pane.test.mjs` (new) + wired into `web/package.json` test chain.

Wave 6 — Playwright:
- `tests/e2e/test_ticket_neutral_pane.py` (new, flag-on) — 6 scenarios driving the REAL runner →
  PoolStepGateway → scripted child via `POST /api/test/run-step/{id}`: step streams live into a
  subscribed pane (MutationObserver token-growth proof), settles on completed, settles on failed
  (held step interrupted), human chat send/render, human send mid-step (composer never disabled),
  recovery after child reset.
- `tests/e2e/conftest.py` — additive `relay_tickets` server-factory param + `relay_tickets_server`
  fixture seeding two automatic-step-eligible tickets (a normal one + a "hold open"-titled one
  whose step prompt carries the scripted child's hold cue). Orchestrator removed four dead
  constants (`RELAY_TICKET_ID`/`_SESSION_KEY`/`_WORKER_TYPE`/`_STAGE`) the seed doc-comment
  referenced but the `create_ticket`-based seed never used.

## Gates (all green; orchestrator-run, not agent-claimed)
- `web && npm run check` — 127 files, 0 errors, 0 warnings.
- `web && npm test` — all 10 unit files pass (incl. new `ticket-neutral-pane.test.mjs`).
- `tests/e2e/test_ticket_neutral_pane.py` — 6/6 pass (flag-on, real path).
- Flag-off re-anchor: `test_live_chat_state.py` + `test_chat_images.py` +
  `test_ticket_file_previews.py` — 40/40 pass unchanged (legacy ChatPanel on ticket routes intact).
- `.venv/bin/ruff check .` — all checks passed (repo-wide).

## Step C — Codex review COMPLETE (2026-07-19)

Codex `gpt-5.6-sol` high, read-only, reviewed the complete S3 net diff vs c215d6d. Round 1 raised
2 BLOCKERs + 2 MAJORs. The orchestrator contested two on reachability; a peer round had Codex
refute both with Hermes primary-source ordering (Finding 1) and a verified missing-lock respawn
path (Finding 3) — all four confirmed real. An Opus implementer fixed all four (disconnected at the
end; orchestrator re-verified + closed the last two gaps itself). Codex's confirming round verified
Findings 1/2/3 CORRECT+COMPLETE and flagged a Finding-4 test hole (streaming-not-queued), which the
orchestrator closed by rewriting the interleaved test to a genuine queued submission (RED-proven).
Full detail: `codex-implementation-review.md`.

## Final gates (orchestrator-run; ./verify is the parent-owned arbiter, never run here)
- Full unit suite: 1038 passed.
- `.venv/bin/ruff check .`: clean. Focused strict mypy on the core changed backend files: clean.
- `web`: `npm run check` 127 files 0/0; `npm test` all pass (incl. new ticket-neutral-pane unit).
- Flag-on e2e `test_ticket_neutral_pane.py`: 6/6. Flag-off + Chief re-anchor
  (live_chat_state, chat_images, ticket_file_previews, chief_neutral_pane): 55/55.
- Scope discipline: `minds/` untouched; `runtime/` = one runner annotation + new `step_gateway.py`;
  no db schema change; eligibility/discovery modules + tests unchanged.
- One non-reproducible flake noted (see review doc); passes on isolation + 3 full re-runs.

S3 implementation is COMPLETE and verified across all waves.
