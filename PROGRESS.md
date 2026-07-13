# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log. Older cycles collapse into the "Recently landed" ledger at
the bottom; the blow-by-blow is git's.

## Current work cycle (2026-07-13): shared scrollbar polish

Current build stage:

- Ticket `t_ccpejh0c` Implementation is complete and verified in isolated worktree
  `/private/tmp/panels-t_ccpejh0c` on `ticket/t_ccpejh0c-scrollbars`; it is ready to propose.
- The CSS-only shared treatment reserves stable gutters on all seven owned scroll surfaces, keeps
  visible native behavior for touch and forced colors, and hides/reveals the thumb only for fine
  pointers outside forced-colors mode.
- Codex plan review found six concrete constraints; all are implemented in
  [D-shared-scrollbar-treatment]: unwind the existing unconditional chat hide, cover all seven
  Panels overflow surfaces, use `:focus-within` rather than assuming focusable containers, test real
  non-hover and forced-colors contexts, avoid screenshot/pixel assertions, and reuse tokens.

What just passed:

- Focused Playwright coverage passes 3/3 after proving RED against baseline CSS. It covers all seven
  scroll surfaces, fine-pointer rest/hover/focus/active states, a real touch context, and forced
  colors both at rest and during interaction.
- Two Codex implementation-review fix cycles were resolved: add a real forced-colors context, then
  exclude forced colors from the fine-pointer auto-hide media query. The final review has no code or
  behavior violations; its only observation was to ensure the new test file is included in the commit.
- Canonical `./verify` passes: Ruff; Mypy across 119 source files; 654 unit tests; compile/static, CSS,
  Svelte, frontend build and frontend tests; 73 Playwright e2e tests; final `VERIFY: PASS`.

Next step:

- Commit the isolated branch and propose Implementation for approval. Do not merge or deploy before
  Closeout.

Blockers:

- None.

## Current work cycle (2026-07-13): today-scoped Review implementation

Current build stage:

- Ticket `t_4ub5h4sw` is implemented on dedicated branch
  `ticket/t_4ub5h4sw-review-today`, based on main `05c8f99`; the dirty primary tree remains untouched.
- Review ticket approvals now come only from the current planning day's membership. The shared queue still
  owns ordering and all non-ticket queue data, and day add/remove events invalidate `queues` so an open
  Review screen and shell badge refresh without a reload.
- Implementation is complete, independently reviewed, and verified. No merge, deploy, or live cutover
  belongs to this stage.

What just passed:

- Focused proof: 655 unit tests; all six frontend test groups; and 18 Review-focused browser tests,
  including off-day exclusion, different-day exclusion, add/remove/re-add without reload, badge/empty-state
  transitions, approval, revision, skip, open, keyboard, and file-preview behavior.
- Independent Codex implementation review raised generated-bundle packaging, different-day coverage, and
  remove-without-reload coverage; all three were addressed, and the follow-up returned `NO VIOLATIONS`.
- Canonical `./verify`: Ruff; mypy across 119 source/typing files; 655 unit tests; compile/static, CSS,
  Svelte check/build, six frontend test groups, and 71 e2e tests; final `VERIFY: PASS`.

Next step:

- Commit the verified ticket branch and propose Implementation. Closeout owns the later merge into main,
  integration verification, and worktree cleanup.

Blockers:

- None.

## Current work cycle (2026-07-13): chat preview stability closeout

Current build stage:

- Ticket `t_svy3xjxj` accepted Implementation. Its current-main artifact is integrated by merge commit
  `4579a6a`, preserving Job B, typed tickets, multi-image chat, preview de-flaking, and the unrelated dirty
  nested frontend worktree.
- The implementation keys chat rows by durable message/turn identity and preserves the shared
  `MarkdownBlock` preview subtree only while source and preview context are unchanged. Browser coverage
  proves all seven preview kinds in Ticket and Chief chat and still proves real-target replacement.
- The branch adopted `6fb2fac`'s compact memory format before integration; old per-ticket process logs were
  not reintroduced.

What just passed:

- Implementation artifact: focused live-chat 10/10, multi-image chat 8/8, shared previews 10/10; independent
  current-main review `NO VIOLATIONS`; canonical `./verify` with 654 unit and 70 e2e tests.
- Canonical post-merge `./verify` on `main`: Ruff; Mypy across 119 source/typing files; 654 unit tests;
  compile/static, CSS, Svelte, frontend build, six frontend test groups, and 70 e2e tests; final
  `VERIFY: PASS`.

Next step:

- Commit this concise closeout record, remove the merged worktree and branch, then propose Closeout. No
  deploy, restart, migration, or follow-up Ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-13): Job B — the `new_worker` production type (verified green, ready to commit)

Current build stage:

- Job B = the create-a-worker worker: a production-shipped `new_worker` type whose worker designs and lands
  ANOTHER worker. Design produced by the `worker-smith` sub-agent (sense-check, owner-driven); IMPLEMENTING now
  on the same agent. Full decision set = [D-ticket-types-new-worker].
- Owner-designed bespoke lifecycle `needs_kickoff → needs_stages → needs_thinking → needs_drafting →
  needs_closeout → done` (one thinking stage = the crux). Skill `panels-worker-new-worker` = coding abstracted +
  owner-lightened; approved draft persisted at scratchpad `panels-worker-new-worker-SKILL.md`, copied verbatim.
- Bundled in: (A) step-runner fix (`employee_step_runner` threads the ticket's own definition — the lone
  straggler that would crash a live new_worker step; VERIFIED LIVE); (B) no transition hooks; (C) structural
  invariant points at the production registry; and the GLOBAL kickoff-ceiling change.
- The kickoff-ceiling turned out to be a DECOUPLING, not a one-liner (worker-smith surfaced; corroborated):
  `default_ceiling` was overloaded as both the fresh-ticket start ceiling and the "first worker stage"
  threshold (recap-writable gate + sprint in-progress). Resolved (my call — Option 1): add `needs_kickoff` to
  `ceiling_range` so `default_ceiling` derives to it; add a `first_worker_stage` view and re-point recap +
  sprint at it, PRESERVING their behavior. Coding's default moves `needs_success` → `needs_kickoff`.

Build progress: IMPLEMENTATION COMPLETE + orchestrator-reviewed. Codex diff review clean after fixes; full
`./verify` PASS twice (68 e2e). Ready to commit — awaiting owner go.

- DONE: the definition, verbatim skill, registration (+facade export), delta-A step-runner fix, the ceiling
  decoupling (Option 1: `ceiling_range` now leads with `needs_kickoff`; new `first_worker_stage` view;
  recap gate + sprint in-progress + external-work seed all re-pointed at it), and all test updates.
- Ceiling decoupling turned up a THIRD hidden `default_ceiling`-as-first-worker consumer during implementation
  (external-work seed, `data.py` create_ticket_from_external_work) — caught by a failing test, re-pointed at
  `first_worker_stage`. Recap-writable, sprint-in-progress, and external-work seed behavior all PRESERVED for
  coding (they key off `first_worker_stage == needs_success`, unchanged); only the fresh-ticket start ceiling
  moved (→ `needs_kickoff`, global).
- Gates (worker-smith, NOT full ./verify): ruff clean whole-repo; mypy clean on `src/ tests/typing/` (119 files);
  654 unit tests pass; 68 e2e tests pass. Nothing committed.
- New: `src/planner/ticket_types/new_worker.py`, `skills/panels-worker-new-worker/SKILL.md`,
  `tests/unit/test_new_worker_type.py` (10 tests: golden manifest + drive-to-done + drive-to-dropped).
- CODEX ROUND (./verify PASSED but Codex found what it can't): fixed P1 — a SECOND coding-default straggler in
  `runtime/readiness.py::is_runnable` (threaded the ticket's own definition into all four machine predicates;
  blast radius was the WHOLE readiness poll, not one ticket). Ran a COMPREHENSIVE sweep of every machine/registry
  call on a non-coding runtime path — is_runnable was the only straggler; all others already thread the
  definition or are intentionally coding (board column seed). Added 2 readiness regression tests. Also: P2a SKILL
  item-2 reworded off the unsupported "default implementer" (now transition-hook framing); P2b stale comments
  fixed (`contracts.py` ManifestDict ceiling_range comment; BRIEF.md ceiling/default).

Next step:

- Commit (awaiting owner go): orchestrator review DONE — Codex diff review (P1 + 2 nits, all fixed), full
  `./verify` PASS twice, scope/ceiling + board-seed spot-checked clean. After commit, the live self-routing +
  novel-stage proof is an owner run.

Blockers:

- None. `main` green at `0595b94`; Job B verified green on top, ready to commit.

Parallel loose thread (not Job B): docs-worker parked on its `docs/ticket-types.md` + stale-doc-audit plan,
awaiting the owner's check/drive.

## Recent context (2026-07-13): Phase 5 done — the type-machinery is complete

The ticket-type machinery is code-complete and end-to-end: define a type → the board renders it → a
worker self-routes to its specialist skill. Phases 4a/4b/5 landed the type-driven backend read-models,
the web app deriving a per-type `Lifecycle` from the served manifest (`GET /api/ticket-types`), and
skill-driven worker realization (`WorkerProfile` active; `panels-worker` split into a type-agnostic base
+ per-type specialists; `skill_view` routing). Production ships coding-only; `probe` stays test-only. The
one thing NOT `./verify`-gated is the live self-routing proof (an owner run against `panels serve`).
Job B above is the payoff built on top of this. Decisions: [D-ticket-types-worker-routing] and the rest
of the ticket-types section of `decisions.md`.

<!-- migrate to docs/ticket-types.md when it lands: the ticket-types build detail below is the
     plain-language account of how the machinery was built and why. Once docs/ticket-types.md exists,
     its substance moves there and this block collapses to a one-line ledger entry. -->

## Ticket-types build detail (2026-07-10 → 07-13, held for docs migration)

The backend ticket-types machinery is N-ary end-to-end and proven. Built to a go/no-go gate through
serial tickets t_tt00–t_tt05b, each committed on green `./verify`:

- **t_tt00** (`824aa58`) — the registry contracts/logic/registry/coding package, additive-only (an AST
  allowlist proves nothing imports it yet), parity golden tests vs the live constants.
- **t_tt01** (`eeef51d`) — correctness-core parameterization: the engine is definition-driven and
  string-id-native (Tier-1) via the one seam `tickets/logic/coding_bridge.py`; Tier-2 scope/field-storage
  stays coding-bound and fails loud on a foreign definition.
- **t_tt02** (`ff8dbc9`) — the DB `ticket_type` column + migration (mirrors kickoff; lock held across
  snapshot→copy→swap). Shared `ticket_type_guard.resolve_and_validate` door; per-type default ceiling.
- **t_tt02b** (`85db3e3`) — generic field storage: `TicketFields` a frozen slot map keyed by field id;
  `Ticket.state`/`ceiling` → `str`; the full propose/accept/scope/drop drive path threaded per row.
- **t_tt02x** (`0b6a0a4`) — the canonical `tests/support/probe.py` fixture + probe type tests.
- **t_tt03** — type-driven CLI/API ingress + external-work genericization + `GET /api/ticket-types`.
  **The go/no-go gate passes:** a `probe` ticket drives `needs_kickoff → needs_alpha → needs_beta → done`
  through the real FastAPI TestClient with exact state/event assertions and no worker session — a missed
  ingress point would fail here (probe's states/fields aren't enum members).
- **t_tt04a** (`2ff4857`) + de-flake (`5c2bd0c`) — backend read-models type-driven.
- **t_tt04b** (`a236fb5`) — the web app derives per-type stages from the manifest; retired the hardcoded
  `ui.ts` lifecycle; coding renders byte-identically.
- **t_tt05** — skill-driven worker realization (see above).

Conscious relaxation (documented): coding ingress error *message* strings became per-type-aware
(codes/flow/data/events byte-identical, no test/frontend asserted the old strings). Flagged for a
possible follow-up: the shipped kickoff migration has the same latent pre-lock snapshot window that
t_tt02's migration closed.

---

## Recently landed

A one-line ledger of completed cycles. Dates are when the work landed; git carries the detail. Decision
references point into `decisions.md`.

- **2026-07-13 — Type-aware front doors** (skill edits): `panels-chief-of-staff` + `panels-worker` now
  know ticket types and the "new worker → `new_worker` ticket" mapping; `new_worker` closeout keeps the
  lists current. [D-ticket-types-front-doors]
- **2026-07-11 — Blockers-only** (`14b0d5c`, merged): one typed blocker read model, `blocks` the only
  explicit relationship; schema-15 blocks-only migration on the live DB. [D-blockers-one-model]
- **2026-07-11 — Ordinary Kickoff stage** (merge `304734e`): Kickoff became a real gated first field
  (`FieldName.kickoff`), replacing the dedicated kickoff accept route/compound proposal. [D-lifecycle-gates]
- **2026-07-11 — Full-page HTML preview** (`5924b83`) + **chat image attachment** (merge `5ad4265`,
  bundle `9421e9e`, repair `a6b0fed`): the **Open preview** document route and ordered managed chat
  images via Hermes native vision. [D-file-preview-contract], [D-chat-images]
- **2026-07-11 — Bounded Markdown preview height** (`e4a607d`): tokenized max height on the shared
  preview. [D-file-preview-contract]
- **2026-07-10 — Chat scroll-back during expanded activity**: upward wheel = reader intent, separated
  from near-bottom distance. [D-chat-follow]
- **2026-07-10 — Expandable live agent activity** (merge `4820cfa`): the collapsed activity row expands
  into a bounded, transient active-turn timeline. [D-live-activity]
- **2026-07-10 — Sprint-planning workflow** (`t_w5mb4y2x`): the review-first `panels-sprint-planning`
  skill replaced the legacy file-based one; no cron added. [D-sprint-planning-skill]
- **2026-07-10 — Editable implementer assignment** (`t_mkkvq9qz`): one nullable typed `implementer`
  value; `khushal` selects `user_takeover` at the Plan→Implementation transition. [D-implementer-value]
- **2026-07-10 — Chief `/new` + fresh-session startup repair**: `/new` as a native Panels session
  transition; the planning DB directory owns the default Hermes home. [D-new-session],
  [D-hermes-home-default]
- **2026-07-10 — Ticket Implementation/Closeout lifecycle** (`7317ec7`, migration hardening `d8643c5`):
  `in_progress`/`needs_review` → `needs_implementation`/`needs_closeout`; `result` → `implementation` +
  `closeout` fields; the separate result-approve path deleted. [D-lifecycle-gates],
  [D-lifecycle-migration], [D-migration-boundary]
- **2026-07-10 — Rollover** (`panels-rollover`): drafts the kickoff, waits for the user before placing
  tickets; narrow `PLAN_ACTOR=chief` elevation for the scheduled kickoff draft only. [D-rollover-skill]
- **2026-07-10 — UI redesign program** (frontend-shared-components branch, `097d4ec..3dab068`): serif
  voice, UI-only, screen shapes locked; six waves + a token-discipline pass, each Codex- and
  design-reviewed. A post-merge owner fidelity pass fixed deltas the wave reviews had passed.
  [D-ui-redesign], [D-ui-fidelity]
- **2026-07-10 — Frontend consolidation** (frontend-shared-components branch, five serial tickets): one
  shared-component set; one `ApprovalBlock`; the `.select` class dropped. [D-shared-component-set]
- **2026-07-08 — Svelte root cutover**: FastAPI serves `web/dist` at `/`; the classic JS route loop
  deleted; ticket chat reads the full durable Hermes session trace. [D-svelte-canonical]
- **2026-07-06/07 — Runtime redesign (W2/W3a → employee runtime online)**: the dispatch package removed;
  `src/planner/runtime/` (step runner) added; one shared persistent gateway child per role; agent
  identity is a per-turn CLI lookup. [D-gateway-topology], [D-runtime-names]
- **2026-07-06 — Redesign waves** (Sprint Overview, Backlog/Ideas split, Board top-down): UI reshaping to
  the approved mockups. Superseded by the UI-redesign program above.
- **2026-07-04..07-06 — Original build + ticket-redesign** (`SPEC.md`-era): the full planner shipped and
  audited; `SPEC.md` later retired. The one retained impasse is the §12 snapshot contradiction.
  [D-snapshot-contradiction]
