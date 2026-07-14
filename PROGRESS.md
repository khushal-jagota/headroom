# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log. Older cycles collapse into the "Recently landed" ledger at
the bottom; the blow-by-blow is git's.

## Current work cycle (2026-07-14): architecture deepening implementation

Current build stage:

- Shared understanding is confirmed for all six architecture candidates. Implementation is isolated on
  branch `codex/architecture-deepening` in
  `/Users/khushaljagota/.hermes/planning-v2-worktrees/architecture-deepening`, based on current `main` at
  `b7ca44a`.
- The resolved domain model is Worker type, stored Stage, Review, Panels Chat, Employee session history,
  and Automatic Employee-step eligibility. Worker type is required and immutable; reading Stage is direct.
- Landing order is Worker workflow → Automatic Employee-step eligibility → Review → canonical Chat turn →
  managed Markdown → resource catalogue → Employee session history. The last item is an isolated final
  commit because its restart/session behavior is the most finicky.
- AD01 is complete on the branch at `f9246d5`. The independently reviewed Worker-type/stored-Stage
  replacement follows the program-memory commit `aa1f29d` and the separately isolated test stabilization
  `afa57fd`.
- AD02 is complete on the branch at `05e2fda`. Its delegated implementation and corrected diff passed
  independent review with `NO VIOLATIONS`, and the committed checkpoint passes the canonical gate.
- AD03 is complete on the branch at `d3dcc29`. Membership-only discovery and the final
  `BEGIN IMMEDIATE` claim call the same complete seven-factor function; direct revision stays separate;
  wake, lock, and shutdown ownership follow the frozen contract. The accepted missing no-gated-field stale
  regression is fixed, the qualifying read-only corrected-diff review reports `NO VIOLATIONS`, and the
  committed checkpoint passes the canonical gate.
- AD04's Ticket-only Review implementation plan is complete and contract-locked. Three review rounds found
  and corrected the file-preview selector omission, overbroad event and mutation invalidation, missing
  current-day/cold-start handling, and the Ticket-creation companion-event contradiction. The final fresh
  read-only re-review, session `019f5eca-4772-7771-8727-515fd60e285e`, reports `NO VIOLATIONS`.
- AD04 is complete on the branch at `0037933`. The initial independent diff review found two
  frontend naming/type violations: the Ticket detail still used `AnyRecord`, and a Worker-count helper still
  said Agent. Both are corrected through the delegated path. The fresh corrected-diff review, session
  `019f5ede-7f24-78a2-b59a-4a6c51223672`, reports `NO VIOLATIONS`, and the committed checkpoint passes the
  canonical gate.
- AD05's canonical human Chat-ingress ticket is defined. It deletes the three retired HTTP routes, their
  parallel service functions, synchronous adapter methods/result shapes, and the unused browser SSE client.
  The gateway `stream` method remains because it is the one transport consumed by the canonical server-owned
  turn for both messages and commands; exact `/new` remains on that path.
- AD05's delegated implementation plan is corrected and contract-locked. The first independent review found
  one omission: its focused commands did not run the existing unit and browser image suites. Both are now
  explicit unchanged preservation contracts outside the edit allowlist and run in full. Fresh re-review,
  session `019f5eed-a837-7601-820e-f0ca50188e40`, reports `NO VIOLATIONS`.
- AD05 is complete on the branch at `754ff27`. The product diff removes every retired Chat
  ingress/result/method shape, keeps the single server-owned turn, and preserves images, live turn state,
  commands, `/new`, Employee-step separation, and session-key rules. Review session
  `019f5f04-3ebd-7a50-a26c-c0a072b8738f` reports `NO VIOLATIONS`, and the committed checkpoint passes the
  canonical gate.
- AD06's deep canonical Chat-turn ticket is defined. It gives one human-turn owner the request, atomic
  admission, causal session-key binding, typed observations, transcript projection, Pause, and idempotent
  settlement. It explicitly leaves Employee delivery and AD09 history separation outside the boundary.

What just passed:

- The architecture report passed the prior canonical `./verify` with 654 unit and 70 e2e tests. The design
  interview resolved deletion posture, domain language, behavior-preservation constraints, refresh policy,
  catalogue scope, and the explicit separation between Panels Chat and Employee session history.
- The new branch and worktree were created without carrying unrelated dirty files from the original
  worktree.
- AD01 now has a delegated implementation plan covering the exact public vocabulary, a v18 all-shape
  migration, historical event rewriting, RED/static tests, and a bounded implementation allowlist. The
  worktree was moved outside the source tree before implementation so it does not appear as an untracked
  child of `main`.
- Independent read-only plan review found two violations: the plan still passed ancient schemas through
  pre-lock lifecycle/kickoff rebuilds, and it omitted the live coding-worker skill from its allowlist. Both
  findings are accepted. The delegated revision is consolidating every recognized Ticket schema into one
  lock-held terminal rebuild and adding the missing skill as a vocabulary-only edit.
- The corrected plan passed the independent read-only re-review with `NO VIOLATIONS`. Its exact naming map,
  consolidated migration, behavior-preservation tests, and implementation allowlist are now the locked AD01
  contract.
- The orchestrator generated the AD01 contract skeleton in the five Python/TypeScript contract files plus
  the canonical v18 SQLite DDL and indexes. `contract-lock.md` records the exact declarations; consumer and
  migration implementation is now ready for delegated work. The tree is intentionally RED until consumers
  are rewired, so no canonical `./verify` has been run.
- The delegated implementation has made the new Worker-type/Stage contract test green (3 tests) and wired
  the core Ticket data, API, view, resolution, machine, manifest, event, and v18 migration paths. A bounded
  allowlist gap was found in two direct-creation test fixtures; those two test paths are now explicitly in
  scope for only `worker_type="coding"` fixture arguments. No production boundary or locked contract changed.
- A broad unit pass found two further legacy HTTP-create fixtures in the project and worker-command tests.
  Their paths are added for only the old create key → `worker_type` update; no tested behaviour changes.
- Orchestrator spot-checking found the Ticket-list boundary still planned a Worker-type parameter solely to
  interpret a Stage filter. This conflicts with the owner ruling that Stage reads use the stored value
  directly. The plan is corrected: list filtering accepts only `stage`; the list query/CLI Worker-type
  disambiguation surface is deleted. Creation remains the Worker-type choice point.
- A frontend boundary scan found `ApprovalBlock.newState` feeding only Ticket lifecycle scope. The
  component is added to the bounded allowlist for the exact `newState` → `newStage` prop rename; no alias
  remains and its rendering/approval behaviour is unchanged.
- Focused implementation evidence is now green: the Worker-type/Stage contract, manifest, ingress, and
  persistence set passes 37 tests; `tests/unit/test_db.py` passes 37 migration tests; mypy passes across
  120 files; and the frontend passes `svelte-check`, its Node tests, and a production build (three existing
  Svelte warnings only). The canonical `./verify` remains intentionally unrun until independent diff review.
- Migration spot-checking added byte-preservation for corrupt post-kickoff field JSON, explicit rollback on
  NULL required inputs, both one-column partial shapes, real `new_worker` values, historical event recovery,
  event rollback, repeated-open idempotence, and an assertion that no Ticket row is read before the v18 lock.
- A final direct-read audit found `_row_to_ticket` still resolving Worker type to decode fields. The codec is
  now explicitly in scope: generic no-definition decoding reads stored slots as stored, so plain Ticket reads
  need no Registry lookup. Definition-supplied audit/interpretation and every write door stay validated.
- The live-vocabulary scan found one unused exported sprint blocker helper whose interface still spoke in
  Ticket States. It duplicates the canonical link summary and has no callers, so it is deleted rather than
  renamed; one day-membership comment is corrected to say the Ticket itself is untouched.
- The delegated AD01 implementation is complete. Pre-review checks pass: 699 unit tests, Ruff, mypy over
  119 source files, frontend `svelte-check` (three existing warnings), frontend tests, production build,
  documentation rename assertion, and `git diff --check`. Generated `web/dist` output is clean. These are
  focused/pre-review checks, not the canonical completeness claim.
- Independent implementation review found two migration violations: an existing `stage` in a partial
  schema was still passed through the legacy `state` conversion, and nullable legacy `status` was rejected
  instead of taking the reviewed `else empty` path. Both are accepted and delegated for focused regression
  fixes. The review's path-scope finding is also accepted: orchestrator-owned domain and memory files will
  be committed separately from the bounded AD01 implementation diff.
- Both migration findings are fixed. Lifecycle Stage, ceiling, and field conversion now runs only from a
  legacy `state` source; an existing `stage` and ceiling are copied exactly. Legacy NULL and unknown
  `status` values map to `empty`, while canonical NULL `ticket_status` remains rejected. The expanded
  migration suite passes 38 tests, Ruff passes on both touched files, and `git diff --check` is clean.
- The domain and current-cycle memory files are isolated in an architecture-program commit, removing
  them from the bounded AD01 implementation diff. The independent corrected-diff re-review is running from
  that base.
- Corrected-diff re-review confirmed both migration fixes, then found one High shipping violation: FastAPI
  serves checked-in `web/dist`, but the delegated frontend build had reverted its generated output, leaving
  the old route and response contract live. The finding is accepted. Generated `web/dist/index.html` and
  its hashed JavaScript add/delete are now explicitly build-only paths in the bounded allowlist and are
  regenerated from the reviewed Svelte source.
- The final corrected-diff review reports `NO VIOLATIONS`: the migration fixes, immutable Worker type
  boundary, direct stored-Stage reads/filtering, retired public vocabulary removal, served bundle, and
  changed-path allowlist are clean.
- The first canonical `./verify` execution passed Ruff, mypy over 119 source files, 700 unit tests, compile
  and static checks, Svelte check/build/tests, then failed 13 of 77 e2e tests. Every failure is the same
  missed fixture rename in two already-allowed files: direct test setup still executes `UPDATE tickets SET
  state = ?` after the canonical column became `stage`. No product assertion ran or failed in those cases.
  A fixture-only repair is delegated; the gate must be rerun after focused review.
- The two stale-Stage e2e helpers are repaired and independently reviewed with `NO VIOLATIONS`; their
  focused browser set passes 14/14. The next full gate passed every prior layer and those 13 cases, then
  exposed one unrelated chat-follow fixture race (`scrollTop` returned to 1436 instead of staying 0).
- Bug-diagnosis proved the chat failure is test synchronization, not AD01 product behavior: the unchanged
  exact test passed 10/10 naturally; delaying native scroll delivery reproduced the exact failure 3/3;
  synchronously dispatching the scroll event passed 3/3 target-reaching controls. `ChatPanel.svelte` is
  unchanged. The one-line fixture fix passes the focused test, Ruff, diff-check, and independent review
  with `NO VIOLATIONS`; it is committed separately as trivial test stabilization at `afa57fd`.
- The post-fix canonical `PYTHONPATH="$PWD/src" ./verify` is clean: Ruff; mypy across 119 source files;
  700 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three existing
  warnings), production build, frontend tests; 77 Playwright e2e tests; final `VERIFY: PASS`. The full
  transcript is retained at `data/verify/ad01-pass.log`.
- AD02's delegated plan mapped the behavior-bearing definition, narrow registry, complete call-site
  threading, deletion set, schema-v19 rebuild, tests, and bounded allowlist. A separate read-only runtime
  audit found the hidden seed, CLI, field-codec, creation, read-model, and import-graph edges before work.
- Independent plan review found two High violations: seed still duplicated coding's complete field set,
  and Chief's CLI could not carry novel Worker-type fields. Both are accepted and corrected. Seed now
  derives its complete slot map from the resolved definition; Chief gains generic repeated field input.
- The corrected AD02 plan also deletes the shallow guard instead of renaming it and puts Ticket-position
  validation on `WorkerTypeDefinition`. Independent re-review reports `NO VIOLATIONS`; the review record
  and exact contract lock are in the AD02 ticket directory.
- Implementation found one bounded allowlist omission: the required extra-definition-field seed proof
  belongs in `tests/unit/test_seed.py`. That test-only path is added; no product boundary or locked shape
  changes.
- A final static wording scan found `tests/support/__init__.py` still used “ticket type” and claimed
  production was coding-only. It is added for that docstring-only correction; executable support stays
  unchanged.
- The delegated AD02 implementation replaces the forwarding stack with one immutable
  `WorkerTypeDefinition`, a narrow registry, and explicit production/test configuration. All semantic
  rules require a resolved definition; direct stored Stage and field reads remain registry-free. The old
  `ticket_types` package, coding bridge, guard, coding enums/tables, and optional-definition paths are
  deleted.
- Schema v19 rebuilds recognized historical Ticket tables under the existing lock-held migration envelope,
  preserves stored field JSON, and removes SQLite's coding-shaped `fields` default. Sanctioned live creation
  must supply Worker type and derives every slot from that definition. A migration may classify an old row
  without a stored Worker type as `coding`; that is historical data conversion, never a live default.
- Focused implementation evidence is green: Ruff and diff-check pass; mypy passes across 114 source files;
  650 unit tests pass; the focused database suite passes 60 tests; the affected browser suite passes; and
  the production frontend build is byte-identical. These are pre-gate checks, not the completeness claim.
- Independent implementation review found one High transport violation: generic Chief `--field-file`
  entries could overwrite fixed request keys. The accepted correction rejects command-specific reserved
  keys before reading a file or sending a request, while leaving Worker-type field validity with the API.
  Focused CLI browser tests pass 6/6, including every reserved key and a create-only name reaching the
  reconcile API. Corrected-diff re-review reports `NO VIOLATIONS`.
- The committed AD02 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 650 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad02-pass.log`. An initial invocation exited before
  all gates because the external worktree's `.venv` link was absent; its separate startup transcript is
  `data/verify/ad02-startup-failure.log`, and restoring/removing the local link changed no tracked file.
- AD03's delegated plan inventories the complete eligibility, discovery, wake, runner, transactional claim,
  runtime composition, action, test, typing, instruction, and documentation surfaces. Independent review
  found one Medium allowlist omission: `config.yaml` still used “Ticket readiness” in a live line-9
  comment. The correction adds only that comment, includes it in the static vocabulary guard, and makes
  wake-specific test-double naming explicit. Corrected-plan re-review reports `NO VIOLATIONS`; the exact
  interfaces are frozen in the AD03 contract lock.
- AD03 implementation pre-review evidence is green: the affected integrated unit set passes 232 tests;
  Ruff, strict mypy over 114 source files, compile checks, docs consistency, the exact changed-path audit,
  `git diff --check`, and the empty-index check all pass. These are focused checks, not the canonical
  completeness claim. Independent reviewer session `019f5ea0-46f2-76e1-a78f-cae334227de9` found only the
  missing stale no-gated-field downstream-side-effect regression; no production-path violation was found.
- The accepted AD03 test gap is corrected without production changes. The stale-claim matrix now supplies a
  test-only ungated non-terminal definition at the final claim seam while leaving the exact shared function
  untouched, and proves no status/event, Panels Chat message/turn, gateway/prompt, or wake. The focused
  correction plus function-identity proof passes 2 tests; Ruff and diff checks pass.
- The qualifying fresh read-only corrected-diff review, session
  `019f5ea6-d03f-7bc1-b527-f334a3f594d5`, reports `NO VIOLATIONS`. It reconfirmed all seven factors,
  transaction timing, direct revision, wake/composition/shutdown ownership, deleted compatibility names,
  bounded paths, timer backstop, and docs parity.
- The committed AD03 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 687 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad03-pass.log`.
- AD04 now has an exact backend/TypeScript response, Ticket-only UI, event/mutation invalidation, deletion,
  preservation, test, and changed-path contract in `ad04-ticket-only-review/contract-lock.md`. Its final
  correction explicitly allows only migration-time historical Worker-type classification: live Ticket
  creation and all canonical storage continue to require an explicit Worker type with no coding default.
- AD04 focused implementation evidence is green: the affected Python set passes 90 tests; frontend checks,
  tests, and production build pass with zero errors and the three existing Ticket-route warnings; the
  affected browser set passes after its stale selector was corrected; Ruff, mypy over 114 source files,
  CSS syntax, and `git diff --check` pass. These remain pre-gate checks, not the completeness claim.
- The independent AD04 implementation review's accepted `AnyRecord` and Agent-helper findings are fixed.
  The regenerated served bundle points to `index-DzbUvGPe.js`, and the corrected full diff has no review
  violations.
- The committed AD04 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 692 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad04-pass.log`.
- AD05's inventory covers every legacy route, service, result type, protocol method, production/test adapter,
  browser helper, and affected test. The reviewed replacement preserves the exact `/turns ->
  start_human_turn -> GatewayAdapter.stream` path, session-key rules, Panels state, command behavior, and
  literal `/new` transition without retaining a synchronous compatibility shape. The orchestrator generated
  the two contract declarations and exact lock; consumer code is intentionally RED until delegated rewiring.
- AD05 focused implementation evidence is green: its static contract passes 4 tests; the affected unit
  bundles pass 212 and 131 tests; Ruff and mypy over 114 source files pass; frontend checking, tests, and
  production build pass with the three existing Ticket-route warnings; the complete unit/browser image
  preservation suites, live Chat-state suite, and affected message/command browser flows pass. The
  independent review also accepted the narrow lost-first-write winner correction as required preservation,
  not AD06 scope.
- The committed AD05 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 687 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad05-pass.log`.

Next step:

- Delegate AD06's implementation plan against the new ticket, independently review and freeze its exact
  owner, observation, transaction, deletion, test, and changed-path contracts, then dispatch implementation.
  Managed Markdown remains AD07.
- The owner has authorized merging only after AD09 and the complete branch pass final review and canonical
  verification; no partial program merge or push is authorized.
- The owner confirmed AD02 has no implicit live defaults at any layer. SQLite's coding-shaped `fields`
  default is removed by the lock-held v19 forward migration. Migrations may rewrite old tables and assign
  `coding` only when converting a historical row that predates stored Worker type; existing field JSON and
  related data remain preserved.

Blockers:

- None.

## Current work cycle (2026-07-13): safe interactivity in ticket HTML previews

Current build stage:

- Ticket `t_7uphhqfj` is integrated into `main` at `6f36d72`; the ticket worktree and branch are
  removed. No deploy, restart, migration, or follow-up ticket applies. Both embedded and full HTML
  previews consume one `allow-scripts`-only sandbox contract; same-origin and every other sandbox
  privilege remain absent.
- The copied Workspace-row reproduction is now a deterministic browser fixture. Its script-cloned rows
  paint at non-zero geometry and its selector changes the visible variant in both preview surfaces.

What just passed:

- The new Playwright test failed first against the empty sandbox, then passed after the shared policy
  change. The complete ticket-file preview browser file passes 11/11.
- Independent Codex review reported `NO VIOLATIONS` against the approved sandbox, interaction, paint,
  and regression contract.
- Canonical pre-merge `PYTHONPATH="$PWD/src" ./verify` passed Ruff, mypy across 119 source files,
  655 unit tests, frontend checks/build/tests, and 75 Playwright e2e tests.
- Canonical post-integration `PYTHONPATH="$PWD/src" ./verify` on `main` passed the same complete gate;
  final `VERIFY: PASS`.
- The unrelated active `CONTEXT.md`, `PROGRESS.md`, `decisions.md`, and nested-worktree edits were
  restored and remain unstaged.

Next step:

- Propose Closeout for approval. No repository or operational work remains.

Blockers:

- None.

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

## Current work cycle (2026-07-13): today-scoped Review closeout

Current build stage:

- Ticket `t_4ub5h4sw` is integrated into `main` by merge commit `879205a`; the reviewed ticket source
  remained byte-identical through integration alongside the already-landed scrollbar work.
- Review ticket approvals come only from the current planning day's membership, and day add/remove events
  invalidate `queues` so the Review screen, shell badge, and empty state refresh without a reload.
- The ticket worktree and branch are removed. No deploy, migration, restart, or follow-up ticket applies.

What just passed:

- Post-merge `PYTHONPATH="$PWD/src" ./verify` on the primary checkout: Ruff; mypy across 119 source/typing
  files; 655 unit tests; compile/static, CSS, Svelte check/build, six frontend test groups, and 74 e2e
  tests; final `VERIFY: PASS`.
- The tracked generated bundle still matches its HTML pointer. Unrelated active `CONTEXT.md`, `PROGRESS.md`,
  `decisions.md`, and nested-worktree edits were restored and remain unstaged with their original diff sizes.

Next step:

- Propose Closeout for approval. No repository or operational work remains.

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
