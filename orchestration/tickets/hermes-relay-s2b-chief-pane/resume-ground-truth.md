# S2b resume — ground truth (working tree vs HEAD `fe9e2b5`)

Established 2026-07-18 by the resuming orchestrator after the previous orchestrator +
implementer died mid-Wave-3 (usage outage). Method: `git diff HEAD`, mapped every change to
the plan's waves, ran the focused unit tests. Reported wave status was NOT trusted.

## Non-ticket noise in the tree (DO NOT touch, NOT S2b scope)

A separate live-system change — the `initiative_planning` worker type — is intermixed in the
working tree. All of it is out of my scope and excluded from any S2b reasoning:

- `src/planner/worker_types/{__init__.py, configuration.py}`, new
  `src/planner/worker_types/initiative_planning.py`
- `src/planner/minds/config.py` (adds `panels-worker-initiative-planning` skill name)
- `tests/unit/test_*worker_type*.py`, `tests/unit/test_probe_type.py`,
  `tests/unit/test_go_no_go_gate.py`, `tests/support/probe.py`,
  new `tests/unit/test_initiative_planning_worker_type.py`
- `docs/worker-types.md`, `docs/employee-runtime.md`, `PROGRESS.md`
- `skills/panels-chief-of-staff/SKILL.md` (owner edit), new
  `skills/panels-worker-initiative-planning/`, `skills/panels-worker/SKILL.md`
- `.claude/worktrees/frontend-shared-components`

These were confirmed unrelated: the diffs only register/reference the new worker type; none
touch the relay backend, the neutral vocabulary, the pool, composition, or the chat guard.

## S2b files in the tree, mapped to plan waves

| File | Wave | State |
|---|---|---|
| `hermes_backend/neutral_vocabulary.py` | 1 | COMPLETE — `NewConversationRequest` + `new_conversation` kind, full wire dispatch |
| `hermes_backend/relay_neutral_route.py` | 1 | COMPLETE — passes `pool_provider` into the session |
| `hermes_backend/neutral_downstream_session.py` | 1/3 | COMPLETE — `_new_conversation` branch (off-loop rebind via `pool.init_executor`, passes old live id, bootstraps returned live id, empty history), optional `pool_provider` ctor param |
| `hermes_backend/employee_child_pool.py` | 1/2 | COMPLETE — `adopt_stored_session`, `rebind_fresh_session` (per-employee serialized, stale no-op, close-then-create), `created_fresh` tracking, `on_stored_session_bound` callback, live-id map |
| `hermes_backend/composition.py` | 2 | COMPLETE — adoption read (`_read_chief_session_key`), `persist_chief_session` callback injected into pool |
| `chat/data.py` | 2 | COMPLETE — `record_agent_session_key` call-only writer (INSERT OR IGNORE + UPDATE + `chat_session_created` event) |
| `core/server.py` | 3 | PARTIAL — `_build_role_gateways(chief_owned_by_pool=...)`, `_lifespan` flag split, `_assert_single_chief_owner` helper CALLED in the **production branch only**. MISSING: `/api/meta` `relay_chief_enabled`, and the test-mode compose path (Wave 4) that the boot-raises test needs. |
| `tests/unit/test_hermes_backend_new_conversation.py` | 1 | COMPLETE, GREEN 7/7 |
| `tests/unit/test_hermes_backend_chief_composition.py` | 2/3 | Present; 6 PASS / 4 FAIL (the 4 pin the unfinished work) |

## Test result (focused run, 2026-07-18)

`test_hermes_backend_new_conversation.py` — **7 passed**.
`test_hermes_backend_chief_composition.py` — **6 passed, 4 failed**:

- PASS: adoption seeds stored key; NULL key → fresh; fresh binding persisted + re-adopted;
  flag-off wiring; flag-on pool-owns-chief; `_assert_single_chief_owner` helper unit.
- FAIL `test_flag_on_chief_lifecycle_ops_rejected` — `ChatTurnLifecycle.__init__()` has no
  `chief_pool_owned` kwarg (guard unwritten).
- FAIL `test_flag_on_ticket_and_day_ops_unaffected` — same missing ctor param.
- FAIL `test_flag_on_stale_running_chief_recovery_is_settled` — same; settle branch unwritten.
- FAIL `test_lifespan_boot_raises_on_inconsistent_composition` — boots with
  `PLAN_TEST_MODE=1 + PLAN_RELAY_BACKEND_ENABLED=1`, takes the `test_mode` branch
  (`server.py:210`) which composes NO pool and never calls the assertion → `called["n"]==0`.
  This requires the **Wave 4 test-mode compose path** to exist and call the assertion.

## Two concrete incomplete points (where the implementer resumes, RED-first)

1. **Central Chief crossover guard in `chat/service.py`** (Wave 3, plan §1.4, Collision #1
   RULED in-scope). NOT written — `chat/service.py` is unmodified vs HEAD. Required interface
   (pinned by the 3 failing guard tests):
   - `ChatTurnLifecycle.__init__` gains `chief_pool_owned: Callable[[], bool] = lambda: False`;
     `create_app` wires it to `lambda: config.relay_backend_enabled` (`server.py`).
   - A private raising guard (name it a pool-ownership crossover guard) called at the TOP of
     `start_human_turn` (`:202`), `continue_human_turn` (`:248`), `pause_active_turn` (`:355`),
     `_answer_pending_clarification` (`:397`, the public entry the test calls — covers
     `_serially`) — raises `PlannerError` with "pool-owned" in the message BEFORE
     `_gateway_provider()`, only when `chief_pool_owned()` and
     `entity_id == CHIEF_OF_STAFF_ENTITY_ID`.
   - A DISTINCT settle-not-raise branch at the top of `recover_human_turn` (`:282`): when
     `chief_pool_owned()` and Chief, SETTLE the active turn via `settle_chat_turn`
     (errored/superseded) and return `None` (no gateway capture). Test asserts
     `read_active_turn` is `None` afterward.

2. **Wave 4 — test-mode scripted-child composition** (plan §6). Nothing exists:
   no `hermes_backend/scripted_relay_child.py`, no test-mode compose branch in `_lifespan`,
   no `/api/meta` `relay_chief_enabled`. The boot-raises test (failure #4) needs the test-mode
   compose path + the assertion call in it. Then the full stateful scripted child (§6.2) for
   the flag-on Playwright suite.

Wave 3 code already in the tree is consistent with the plan — completing the guard is
correct, not a revert. Everything downstream (Wave 4, the entire frontend §4/§5, the two
Playwright suites §7.1/§7.2, `neutral-pane.test.mjs`, `capabilities.ts`,
`ChiefNeutralPane.svelte`, the route swaps, `conftest.py` `relay_chief` fixture) is unstarted.

---

## UPDATE — implementation complete, Codex round-1 review + fix round done (2026-07-18)

The implementer built all remaining waves + the frontend + both Playwright suites (first
handoff). A Codex round-1 diff review (high effort) found 7 issues — 2 blockers, 2 major, 3
minor — recorded in `codex-implementation-review.md`. I independently verified all 7 against
the code; every one was correct. A second implementer round fixed all 7:

- S2B-OWN-001 (BLOCKER): F5 persistence now fail-closed — the persist callback runs inside the
  guarded publication path; on first-create failure the child is torn down + unregistered and
  no stored-id/binding is left; the rebind half fails closed too.
- S2B-CATALOG-001 (BLOCKER): the picker + scripted child now use the NATIVE catalog shape
  (top-level `pairs` + `skill_count`, skills = `pairs[-skill_count:]`), not the invented
  `categories[].items` shape that rendered zero skills against real Hermes.
- S2B-ACC-001 (MAJOR): interrupt / compact-4009 / child-reset / boot-raises tests now genuinely
  exercise their named behavior (a new `test_lifespan_boot_actually_raises_...` forces an
  inconsistent composition and asserts a two-owner RuntimeError at boot).
- S2B-CAP-001 (MAJOR): `capabilities.ts` maps only `=== false` → disabled; missing/non-boolean
  → error; the error branch has a working `/api/meta` retry. New `web/tests/capabilities.test.mjs`.
- S2B-NEW-001 / S2B-READY-001 / S2B-ROUTE-001 (MINOR): newConversation clears ephemeral+transcript
  before send; `data-neutral-ready` gated on first history; BoardRoute legacy status lazy in the
  disabled branch only.

The second implementer round also died before a clean handoff (its final message was lost), so I
established fix-round ground truth myself: read all 7 fixes in the source (all present + correct)
and re-ran the full gate set independently — full unit suite exit 0, 39 focused S2b unit green,
ruff clean, mypy Success (137 files), `npm run check` 0/0, `npm test` all suites (incl. the new
capabilities suite + resource-catalogue completeness), Playwright 19 passed with the strengthened
assertions. One confirming Codex round (round 2 of 2, D-codex-loop-cap) is running to verify the
7 fixes closed with no regression, then this is ready for the parent's canonical `./verify` +
integration. Nothing committed.
