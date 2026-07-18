# PROGRESS

Read this first after any context compaction. It is the build's memory — a snapshot of where
things stand right now, not a history log. Older cycles collapse into the "Recently landed" ledger at
bottom; the blow-by-blow is git's.

## Current work cycle (2026-07-18): project-and-Worker-type Closeout lanes

Current build stage:

- Ticket `t_scvazj90` is in Implementation on branch `ticket/t_scvazj90-closeout-lanes`.
- Closeout reuses the complete automatic Employee-step eligibility decision. Its only added fact is
  that any matching effective-project + Worker-type Closeout with non-empty `ticket_status` holds
  the lane. No queue table, claim row, or migration was added.
- Discovery orders today's candidates by `updated_at`, then Ticket id, and submits only the oldest
  eligible Closeout from each free lane. The existing `BEGIN IMMEDIATE` claim repeats the same
  complete decision before changing status.

What just passed:

- RED proved a projectless Closeout waiter was incorrectly submitted while another matching
  Closeout awaited approval. The eligibility rule now blocks it.
- RED proved two empty Closeouts in one free lane were both submitted. Discovery now submits only
  the oldest waiter.
- The focused discovery and Ticket-engine set is green (`71 passed`). Coverage includes all
  non-empty statuses, parent-derived project identity, projectless lanes, independent
  project/Worker-type lanes, Stop, atomic claim recheck, acceptance release, non-Closeout behavior,
  and durable-session resume. Focused Ruff and mypy are clean.
- The single independent Codex implementation-diff review reported `NO VIOLATIONS`.

Next step:

- Run one canonical `./verify` on the settled tree and propose Implementation.

Blockers:

- None.

## Current work cycle (2026-07-17): Hermes integration restructure — S0 protocol spike

Exploration note (2026-07-18): the VPS agent-GUI survey found a possible simplification
that must be tested before adding provider-specific translators. Hermes 0.18.2 has native
`hermes acp`; maintained ACP adapters exist for Claude Agent SDK and Codex app-server; Emdash
uses all three behind one ACP runtime. The open gap is Hermes `clarify`/ACP elicitation.
Research and the proposed three-provider conformance spike are recorded in
`orchestration/vps-agent-gui-research-2026-07.md`. This is a finding, not an owner decision;
the active S2b work remains unchanged.

Current build stage:

- Owner approved the relay architecture (see `D-hermes-relay-architecture` and
  `D-runtime-neutral-pane-vocabulary` in decisions.md): Panels' server keeps the single Hermes
  connection but relays native conversation frames to the browser instead of translating them
  through SQLite; the pane speaks a runtime-neutral ACP-shaped vocabulary so future Codex/Claude
  CLI workers plug in as adapters.
- Staged plan: S0 live spike → S1 relay foundation (`hermes serve` + WS client + relay endpoint)
  → S2 native-vocabulary chat pane → S3 worker steps on the shared backend → S4 deletion of the
  demux/DB-streamed turns/polling plus the transcript-ownership ruling → S5 optional profile
  registration for desktop access.
- S0 PASSED live: authenticated WS connect (`gateway.ready`), session.create, prompt.submit
  with real streamed frames, hard socket drop, reconnect + session.resume with full history and
  proven conversational continuity, plus the concurrent-resume probe (server does not lock —
  relay must stay sole upstream owner). Record: `orchestration/hermes-relay-redesign/s0-spike.md`;
  staged plan: `orchestration/hermes-relay-redesign/plan.md`. Scratch backend stopped after the
  run; the live Panels server and the user's Hermes processes were never touched.

What just passed:

- Investigation complete (three parallel sweeps + direct source verification). Key verified facts:
  `tui_gateway` is the official surface with stdio and WS transports over one wire format;
  `hermes serve` is the headless backend; `apps/shared/src/json-rpc-gateway.ts` is the shared
  client; events route to the owning transport and carry `session_id`; WS disconnect has a
  grace-windowed reaper cancelled by quick reconnect/resume; Panels today uses ~10 RPC methods and
  rebuilds the client half (~4k+ lines) with the DB in the conversation path (500 ms poll confirmed
  at `web/src/components/ChatPanel.svelte:155-161`).

- Owner redirected the upstream topology mid-S1: one child process per employee
  (`D-child-per-employee`) supersedes the shared `hermes serve` backend and the prompt-carried
  credential identity (spawn env `PLAN_TICKET_ID` + actor is the carrier). No idle reaping in
  v1 — provider-side prompt caching gives keep-alive no token benefit; respawn costs seconds
  plus a fresh shell env. Plan and S1 contract revised accordingly; the in-flight S1 ticket
  agent was paused before the redirect and resumed on the revised contract.

- The S1 ticket completed its full pipeline: multi-round-reviewed plan, implementation,
  and an implementation-diff Codex review that converged 9 → 3 → 2 → 0 findings
  ("the ticket is complete"). 14 new files under `src/planner/hermes_backend/` +
  `tests/unit/` and three minimal edits (`core/server.py`, `core/config.py`,
  `config.yaml`); 56 focused tests green; backend off by default; nothing under
  `minds/`/`chat/`/`runtime/` touched. Mid-pipeline rulings are recorded as
  `D-relay-raw-frame-transport` (raw stdio frames on the spawn seam; pool owns session
  binding; 9-method denylist) and `D-s1-concurrency-scope` (trigger-bound limitations).
- First canonical `./verify` FAILED at the skip-scan gate: the registry-completeness test
  carried a forbidden `pytest.skip` for a missing Hermes checkout. Orchestrator-direct
  integration repair: the test now hard-asserts the checkout resolves (skips would
  silently disable the drift trip-wire); unused import removed; focused file green;
  skip-sweep across all seven new test files clean. Failed transcript retained at
  `data/verify/s1-relay-foundation.log`.

- The second canonical run passed every suite (919 unit, all frontend, 114 Playwright,
  mypy across 129 files, build) but failed the ruff gate on scratch-grade lint in the
  copied S0 spike script; orchestrator-direct lint repair applied, repo-wide Ruff clean.
  Transcript retained at `data/verify/s1-relay-foundation-2.log`.
- The third canonical `./verify` passed all gates, but during its e2e phase the ticket
  agent's ordered R3-round3-1..4 code audit landed one genuine one-line fix (the
  transport's started-flag was set before `Thread.start()`, so a failed start would make
  shutdown join a never-started thread) — flagged immediately, inspected and accepted.
  Because that pass no longer described the settled tree, it is not the claim; transcript
  retained at `data/verify/s1-relay-foundation-3.log`.
- The fourth canonical `./verify`, against the frozen settled tree, PASSES all gates:
  Ruff, mypy (129 source files), 919 unit tests (nine existing warnings),
  compile/static/CSS checks, Svelte, production build, frontend tests, and 114
  Playwright tests — final `VERIFY: PASS`. Transcript
  `data/verify/s1-relay-foundation-4.log`, SHA-256
  `6c9713e992947827a4d8bf948e2e286d65ed1951b9cc2975698544013ef362a5`. S1 is complete;
  nothing is committed pending the owner's word.

- S1 is committed to main as `052a968` (green-wave practice; owner said continue). The
  owner's unrelated `skills/panels-chief-of-staff/SKILL.md` edit remains uncommitted.
- Sequencing correction while cutting S2 (`D-chief-first-cutover`): a ticket's chat and
  steps share one stored session with one owning process, so ticket chat cannot move
  before steps — the Chief (no automatic steps) cuts over first; ticket employees move
  chat + steps together in S3. S2 splits into S2a (neutral vocabulary + Hermes
  translator + mediated commands + transcript-mirror tee, backend) and S2b (Chief pane
  cutover, frontend). During transition the tee keeps the Panels transcript mirror fed
  so `D-transcript-ownership-open` stays open for S4.

- S2a (`hermes-relay-s2a-neutral-translator`) is COMPLETE and verified. Final scope per
  the owner's free/not-free rulings (`D-only-free-hermes-features`,
  `D-native-turn-concurrency`): the neutral conversation core (attach/history, send with
  resolved image refs, clarify answer, approval response, interrupt) plus compact and
  the as-is catalog read; model machinery cut; cut/unknown request kinds rejected. Six
  source + eight test files; additive wiring only in `core/server.py` and
  `composition.py`; chat/ call-only; S1 files unchanged. Pipeline: one plan review and
  one diff review (per `D-codex-loop-cap`); the diff review surfaced six genuine
  defects, plus the image-reference defect found by S2b's planning review — all seven
  fixed RED-first. Canonical `./verify` PASSES all gates: Ruff, mypy (135 files), 956
  unit tests, build, frontend, 114 Playwright. Transcript
  `data/verify/s2a-neutral-translator.log`, SHA-256
  `e1984e0cfe9940302e4e9e0764cf03d3d07837395caff04603102732b798b2cb`.
- S2b (`hermes-relay-s2b-chief-pane`) is cut and mid-planning: contract locks the
  ownership handoff (flag-on composition never starts the legacy Chief child; pool
  adopts and persists the Chief binding), pool-owned new-conversation, the neutral pane,
  and Playwright re-anchor in both flag states. Its plan review produced 13 internal
  findings (being folded) and three cross-boundary rulings (image refs → fixed in S2a;
  central flag-on Chief guard incl. recovery settle → in-scope; Chief binding
  persistence → in-scope). Implementation stays gated until this S2a commit lands.

- S2a committed to main as `fe9e2b5`; S2b's implementation gate opened against it.
- S2b implementation was interrupted mid-run by a usage outage (~17:20): Waves 1–2
  (vocabulary/new-conversation seam; Chief adoption + binding persistence) reported
  complete, Wave 3 (composition split + two-owner assertion + central Chief guard)
  partial on disk, Wave 4 (scripted-child e2e composition) and all frontend waves not
  started. A fresh resume orchestrator was dispatched: ground-truth the tree against the
  reviewed plan first (`resume-ground-truth.md`), complete implementation, then the
  single Codex diff review. Non-ticket files in the tree (owner's SKILL.md edit;
  live-system `initiative_planning` worker-type output) are explicitly out of its scope.

- S2b (`hermes-relay-s2b-chief-pane`) is COMPLETE and verified. The resume orchestrator
  ground-truthed the outage-interrupted tree, completed the central Chief crossover
  guard, the scripted-child e2e composition, the frontend (neutral client, capabilities
  probe, `ChiefNeutralPane`, route swaps), and both Playwright suites. Two Codex diff
  rounds (cap) surfaced and closed seven findings — notably fail-closed binding
  persistence (publish only after the write lands; failure tears the child down) and
  the invented-catalog-shape defect (picker + scripted child now parse the native
  `pairs`/`skill_count` shape). Canonical `./verify` PASSES all gates: Ruff, mypy
  (137 files), 984 unit tests, build, frontend, and 129 Playwright (15 new flag-on
  Chief scenarios + legacy flag-off unchanged). Transcript
  `data/verify/s2b-chief-pane.log`, SHA-256
  `7c01892dbf156b5111a9866be00d35697c51390ceee9be0f893bac8cdd9a9f46`.
- Meanwhile the live Panels system committed its own `initiative planning worker`
  (`f75ddfc`, 19:33) through its normal flow; the canonical run covers the combined
  state. The owner's in-progress skill edits remain uncommitted.
- S3 (`hermes-relay-s3-ticket-employees`) is cut and mid-planning (plan written, review
  round next); implementation gated on this S2b commit.

Next step:

- Commit the S2b green wave, open S3's implementation gate, run S3 to hand-back →
  integration → verify → commit. Then the de-patch stage and S4 deletion (transcript
  ruling still open with the owner).

Blockers:

- None.

## Current work cycle (2026-07-16): fail-closed Employee session ownership

Current build stage:

- Ticket `t_8vww58fj` is integrated and in final closeout verification. Panels fetches every durable
  owner deterministically and rejects any ambiguous effective binding before returning or persisting it.
- Hermes now restores every current-turn session ContextVar over the shared local terminal snapshot and
  exports those values to the command's child processes. Shell functions stored in the snapshot cannot
  intercept that restoration.
- Production and the available compatible backups had zero duplicate durable owners. The existing
  transactional ownership gate is sufficient, so no schema migration was added.

What just passed:

- RED for direct durable lookup returned an arbitrary `200` Ticket when two Tickets shared a session;
  after the read hardening, the same focused test passes with validation and sorted Ticket ids.
- RED for both ordinary and force-fresh second-Ticket claims did not raise; after the writer gate, both
  focused cases pass and prove the claimant row and event stream remain unchanged.
- A Codex review found the idempotent already-corrupt ownership edge; RED reproduced it, and the writer
  now rejects it before the ordinary idempotent return.
- Final review found the same issue on compare-and-swap winner adoption; RED reproduced that path, and
  the writer now validates the one effective session before either returning or persisting it.
- The complete focused worker lookup, Ticket engine, and human Chat binding set passes (`56 passed`, one
  existing FastAPI deprecation warning). Focused Ruff reports `All checks passed!`, and
  `git diff --check` is clean. Canonical `./verify` was deliberately not run in this branch.
- Hermes' exact cross-session, all-mapped-variable, snapshot-function, snapshot-poisoning, and
  CLI-rotation regressions pass (`18 passed`); focused Ruff and `git diff --check` are clean.
- Panels merged as `b8ee189`; Hermes merged as `047ba8298`. Both repositories retained their unrelated
  pre-existing workspace changes.
- `panels restart` resumed this conversation as durable session `20260715_195950_a2e173` with new live
  id `9fabc812`; `panels worker my-ticket --json` resolved `t_8vww58fj`, not `t_4bps5bwm`.

Current hypothesis:

- Confirmed. Panels owns unambiguous durable Ticket binding; Hermes owns authoritative per-turn terminal
  identity. Fixing both boundaries removes the demonstrated leak and fails closed on inconsistent data.

Next step:

- Run one canonical `./verify` against this settled tree, then supersede the stale failed Closeout
  proposal with the merge, review, restart, and verification evidence. No further source change is planned.

Blockers:

- None.

## Current work cycle (2026-07-15): controlled Panels restart supervisor

Current build stage:

- The verified `codex/panels-restart-supervisor` branch is approved and integrated into
  `main`. `panels serve` is now the stable foreground owner of one replaceable
  application process, and `panels restart` requests replacement through a local
  versioned control exchange. The server launch root, interpreter, environment, and log
  streams come only from the original `serve` process; a Ticket worktree cannot choose
  the replacement.
- Stopgap commit `430cf92` separately makes the server operator-owned in the shared and
  new-worker skills. Workers never discover or signal a PID, run `panels serve`, or launch
  a worktree replacement. Until the controlled command is available, they report that a
  restart is required and stop.
- The verified landing includes `main` through merge `ddaf5c1`. No live server was
  stopped or restarted as part of implementation or integration.

What just passed:

- Focused lifecycle acceptance passes 12 real-process cases. They cover duplicate
  ownership, same-socket safety, acknowledgement ordering, operator shutdown during
  incomplete and accepted requests, unexpected-child priority over queued restart,
  serial generations, captured-root restart, unexpected exit, and cleanup.
- The integrated control/shutdown/provisioned-skill bundle passes 95 tests. Ruff is
  clean, mypy reports no issues across 120 source files, and `git diff --check` is clean.
- The corrected implementation reviews report `NO FINDINGS` on both Standards and Spec.
  Two read-only Codex implementation reviews report `NO VIOLATIONS`.
- The clean canonical completion run passes Ruff, mypy across 122 source files, 854
  unit tests with nine existing warnings, compile/static/CSS checks, Svelte with zero
  errors or warnings, the production build, all frontend tests, and 114 browser tests.
  Every gate is `ok` and the run ends `VERIFY: PASS`. The full transcript is
  `orchestration/tickets/panels-restart-supervisor/verify-final.txt`, SHA-256
  `2ccfe2a325214e276abd717cc1e6116fb9d8c05cbb7ec500d5c997b7f00e5ce3`.
- The first verifier attempt exposed only that the borrowed main-worktree virtualenv's
  editable install resolved the old main source. Running the same gate with this
  worktree's `src` first on `PYTHONPATH` corrected the test environment; no product
  change was needed.

Next step:

- The next operator-started `panels serve` process will use the landed supervisor. Do
  not start, stop, or restart the live server merely to complete this integration.

Blockers:

- None.

## Current work cycle (2026-07-15): paired new-worker Understanding Stage

Current build stage:

- Ticket `t_gn7x278u` is verified on isolated branch `ticket/t_gn7x278u-understanding` from
  `main` at `5f5ea6f`. The completed diff inserts paired `needs_understanding` immediately after
  Kickoff, backfills existing `new_worker` field maps without rewinding Tickets, updates the shipped
  specialist and live Worker-type documentation, and adds backend, gateway/session, and Playwright
  coverage. An accepted High review finding is corrected: historical typed-ticket schemas that preserve
  `worker_type = 'new_worker'` with coding-shaped field JSON now gain every missing new-worker-specific
  slot (`understanding`, `stages`, `thinking`, `drafting`) in lifecycle order while preserving all
  original top-level slot values as legacy extras.
- Parent-review production corrections are applied in this worktree only. Reconciliation uses entered
  status only when the Stage actually changes; current paired reconciliation preserves `paired_work`.
  Ownership override writes now no-op only on identical override maps, emit
  `previous_effective_ownership_mode`, preserve status for same-effective current-Stage changes, and never
  change status for future-Stage overrides. Takeover/release now persist or clear explicit current-Stage
  overrides even when the default has the same effective mode. Drop remains on resting status, and
  auto-accepted proposal writers use entered status only for real Stage advances.
- The corrected plan and completed diff passed fresh read-only Codex reviews with `NO VIOLATIONS`.
  Accepted findings made Understanding the explicit `first_worker_stage`, separated deterministic
  protocol assertions from claims about model judgment, completed the Chief external-work prefix,
  hardened second-turn session coverage, and closed the historical migration gap.

What just passed:

- Focused registry, migration, external-work, eligibility, shared Ticket Chat/session, provisioned-skill,
  and browser progression tests are green in the isolated worktree. Chromium's Codex sandbox failure
  was rerun outside the sandbox and both focused e2e cases pass.
- The High review regression was replayed RED against this worktree's `src` with the pre-fix migrator:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_db.py::test_create_schema_backfills_historical_new_worker_coding_fields_for_audit -q`
  failed at `tickets_data.audit_ticket_registry_integrity()` for `t_legacy_new_worker` with corrupt fields
  JSON. After the correction, the same regression plus the existing proper-shape/idempotence migration
  test pass, and the affected DB/new-worker suite passes:
  `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_db.py tests/unit/test_worker_type_persistence.py tests/unit/test_new_worker_type.py -q`
  (`91 passed, 2 warnings`). `PYTHONPATH=src .venv/bin/ruff check src/planner/core/db.py tests/unit/test_db.py`
  and `git diff --check` are clean.
- The canonical corrected-tree `./verify` passes Ruff, mypy across 116 source files, 823 unit tests
  with nine existing warnings, compile/static/CSS checks, Svelte with zero errors/warnings,
  production build, all frontend tests, and 102 Playwright tests. Every gate is `ok` and the run ends
  `VERIFY: PASS`. The full transcript is
  [verify implementation](/files/tickets/t_gn7x278u/artifacts/verify-implementation.txt), SHA-256
  `115f678308f4bf6970f5eb824a0d891e3646035a8362f32638944cd4c517e735`.
- Parent RED was reproduced with the expanded command: the stale discovery-loop test still expected no
  automatic paired dispatch, two human chat tests expected immediate `paired_work` without an automatic
  opening precondition, and two e2e tests waited for background loops that test-mode servers deliberately
  do not compose.
- After corrections, focused ownership/external-work/eligibility regressions pass:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_stage_ownership_backend.py tests/unit/test_chief_external_work.py tests/unit/test_automatic_employee_step_eligibility.py`
  (`78 passed`, one existing warning). The broader focused unit set also passes:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_stage_ownership_backend.py tests/unit/test_chief_external_work.py tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_human_chat_turn.py tests/unit/test_automatic_employee_step_eligibility_actions.py`
  (`139 passed`, two existing warnings).
- The parent command's unit-test portion passes:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_employee_step_runner.py tests/unit/test_automatic_employee_step_eligibility_actions.py tests/unit/test_human_chat_turn.py`
  (`167 passed`, two existing warnings).
- The expanded parent command now passes all selected unit tests plus both new-worker public-flow e2e
  tests. The e2e tests explicitly invoke the production `EmployeeStepRunner` with the shared fake gateway
  against the server DB because test mode omits background loops, then continue through the public Chat
  and browser surfaces. Ruff and `git diff --check` pass.
- Independent correction review found one redispatch edge: accepting a non-gating proposal on an already
  opened paired Stage used entered-stage status and reset it to `empty`. Acceptance now uses entered status
  only when the Stage advances; a regression keeps unchanged paired Stages at `paired_work`.
- Corrected review found the compatibility check still required an earlier worker-step event when a later
  paired Stage reused a session created by ordinary Ticket Chat. Compatibility now keys only on whether a
  worker-step opening started after the current Stage marker; an earlier human turn/session no longer
  blocks the one opening. The expanded focused gate, Ruff, stale-wording search, and diff check pass.
- Corrected implementation commit `8a24cfe` passed final independent review with `NO VIOLATIONS` and is
  integrated into current main as merge commit `d4867fe`. Fresh owner edits were backed up, stashed for the
  merge, and restored without conflict; the nested frontend worktree remains untouched.
- The first canonical post-merge `./verify` exposed three stale expectations of the retired silent-paired
  behavior: two probe genericity tests expected immediate `paired_work` on paired Stage entry, and one
  browser marker fixture did the same. Test-only commit `d6d5d1a` now expects `empty` on entry and seeds
  `paired_work` only for the post-opening rendering assertion. All three focused tests pass; independent
  post-verify review reported `NO VIOLATIONS`.
- Final canonical post-merge `./verify` passes all gates: Ruff, mypy, 850 unit tests, build checks,
  frontend tests, and 102 Playwright tests. The transcript is
  [verify closeout](/files/tickets/t_gn7x278u/artifacts/verify-closeout-paired-opening.txt), SHA-256
  `d11ecba7eaf5976f1125aecdf5a66556723ae9d51f22d6ffa6219b69d033e979`.
- Both temporary ticket worktrees/branches and their two temporary owner-state stashes are removed. The
  Closeout proposal is filed and the Ticket now rests at `awaiting_approval`.

Next step:

- Await Closeout approval. No deployment, restart, or live database operation is part of this closeout.

Blockers:

- None.

## Current work cycle (2026-07-15): exploration Worker type

Current build stage:

- Ticket `t_8vfx962r` has landed on `main` through feature commit `4f7916b` and merge
  commit `5e8c9fa`. The production registry now carries `exploration`; startup provisions
  `panels-worker-exploration`; Worker and Chief front doors and live docs name the type.
- The approved lifecycle is Kickoff → paired Understanding → Research Plan → Research →
  paired Answer → Follow-up → Closeout → Done. Research owns source discovery and corpus
  curation; there is no separate Corpus Stage. The specialist ends with the approved
  transferable-problem discipline for cross-domain research.
- Codex review found stale probe validation lists and stale live-doc references; those were
  corrected. Two later review rounds drove the remaining doc/comment cleanup and the final
  review reports `NO VIOLATIONS`.
- Panels restarted on the merged code. The live manifest serves the exact `exploration`
  lifecycle and `data/hermes-home/skills/panels-worker-exploration` resolves to the shipped
  specialist.
- Four real exploration Tickets now carry grounded Kickoff proposals: durable user memory
  (`t_pszubyxa`), Vylo onboarding (`t_jj25dbnj`), Panels VPS workflow (`t_4bps5bwm`), and
  existing-Worker management (`t_z9upbzf0`). The first three sit under their relevant
  sprint items; the fourth is a loose current-sprint Panels Ticket. All four are on today
  and await Kickoff approval. No exploration work was performed.

What just passed:

- Canonical `./verify` with this worktree's source pinned passes Ruff, mypy across 117
  source files, 823 unit tests with nine existing warnings, compile/static and CSS checks,
  Svelte check with zero errors/warnings, production build, frontend tests, and 100
  Playwright tests. It ends `VERIFY: PASS`; the transcript is
  `data/files/tickets/t_8vfx962r/artifacts/verify-closeout.txt` with SHA-256
  `26abe805a176f8d473a59fcc26d8560f4c3f63418514ecae2e24186a694cc0c7`.
- Canonical readback passed every onboarding check: title, priority, Worker type, Stage,
  approval status, exact Kickoff body, sprint item, effective project/sprint, and today
  placement. The record is
  `data/files/tickets/t_8vfx962r/artifacts/exploration-onboarding/onboarding-results.json`.

Next step:

- Propose Closeout for approval with the merge, verification, live activation, and four
  onboarding Ticket IDs. Do not start or monitor the explorations.

Blockers:

- None.

## Current work cycle (2026-07-14): live Panels connection status

Current build stage:

- Ticket `t_ycpcb619` has approved Implementation commit `fe7f0c1`. The current-main integration is
  complete on `integrate/t_ycpcb619` through feature commit `e3d77c4` and checked-in configuration
  correction `a1c00f2`; it retains newer main work and serves combined bundle `index-BKICWA5O.js`.
- The existing event WebSocket emits configured quiet heartbeats and owns the browser's
  Connected/Reconnecting/Offline lifecycle. Recovery preserves cursor replay and keyed invalidation,
  then performs one catalogue-owned refresh of currently subscribed resources. Navigation keeps this
  signal separate from worker presence on desktop and mobile.
- Current-main integration review found the missing checked-in heartbeat default and stale bundle
  evidence; both were corrected, the config gap has a RED/GREEN regression, and the final fresh review
  reports `NO VIOLATIONS`.
- Post-integration canonical `./verify` passes Ruff; mypy across 116 source files; 817 unit tests with
  nine existing warnings; compile/static and CSS checks; Svelte with zero errors/warnings; production
  build; the complete frontend suite; and 100 Playwright tests. The run ends `VERIFY: PASS`; transcript
  `data/verify/t_ycpcb619-closeout-pass.log` has SHA-256
  `ada673c7a925d66fb26bd6cd4529197b504c9038622121a840004563ea9c301e`.

Next step:

- Merge the verified integration into `main`, preserve unrelated local main edits, remove only this
  Ticket's temporary branches/worktree, and propose Closeout. No deployment, restart, migration, or
  follow-up applies.

Blockers:

- None.

## Current work cycle (2026-07-14): Workspace Hide done as the only visibility filter

Current build stage:

- Ticket `t_834r0tz6` is integrated with current main through merge commit `fb67232`; verified
  implementation commit `b0cc921` is its second parent. No deployment or restart applies.
- A fresh Workspace now starts with **Hide done** on. Turning it off reveals done tickets, and the
  existing app-level state preserves the choice across in-app navigation. The Ticket-status control,
  local state, filtering branch, status-only styles, and orphaned label helper are removed; every
  Ticket status remains visible. Grouping, Worker-type/Stage/activity ordering, collapse, selection,
  URL navigation, and row presentation stay on their existing paths.
- Live frontend documentation, the standing decision, and current Workspace redesign intent now describe
  the same one-toggle contract.
- The merge retained current main's full-page managed Markdown preview source and rebuilt one combined
  production bundle.

What just passed:

- The focused browser regression was RED against the old fresh default, then passed with the new default,
  absent status control, reveal-done behavior, and both navigation-persistent toggle states. Existing
  Chief, ownership, routing, collapse, row, and marker scenarios pass after removing only obsolete
  status-filter setup; two marker tests explicitly reveal done rows before asserting their terminal marks.
- The first independent Codex review found stale redesign intent and the now-orphaned `ticketStatusLabel`;
  both were removed. Corrected implementation reviews and the post-merge integration review with model
  `gpt-5.5`, read-only sandbox, and high reasoning report `NO VIOLATIONS`.
- The post-merge canonical `./verify` passes Ruff; mypy across 116 source files; 799 unit tests; compile/static
  and CSS checks; Svelte check with zero errors and warnings; production build; frontend tests; and 96
  Playwright tests, ending `VERIFY: PASS`. The full transcript is
  `[closeout verification transcript](/files/tickets/t_834r0tz6/artifacts/verify-closeout.txt)`.

Next step:

- Propose Closeout for approval. No deployment, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): full-page managed Markdown previews

Current build stage:

- Ticket `t_9fqnvpbc` is integrated into `main` by merge commit `cf0ea3f`; verified implementation
  commit `9c90622` is its second parent. The ticket branch and isolated worktree are removed.
- The centralized preview route fetches managed Markdown and renders it directly through the shared
  `MarkdownBlock` surface in a full-page document layout. Ticket and chat targets share that branch;
  bounded embedded Markdown, HTML sandboxing, and other file kinds are unchanged.
- The merge combined current main's managed-HTML base preparation with the Markdown route: HTML still
  resolves relative and root-relative sibling assets before Blob rendering, while Markdown keeps its
  canonical source and shared renderer contract.

What just passed:

- The two conflict-sensitive browser cases and the complete file-preview browser file pass 14/14. They
  prove the real Markdown popup URL and geometry, nested Markdown/image previews, self-link bounds,
  hash target switching, chat targets, absent duplicate top-level controls, retained HTML sandboxing,
  and managed HTML sibling stylesheets/images in both surfaces.
- Independent merge review found one low documentation gap for the chat full-preview hash. The live
  frontend doc now names both ticket and chat routes; corrected-diff review reports `NO VIOLATIONS`.
- Post-merge canonical `./verify` on `main` passes Ruff; mypy across 116 source files; 799 unit tests;
  compile/static, CSS, frontend check/build/tests; 96 Playwright tests; final `VERIFY: PASS`. The full
  transcript is [verify closeout](/files/tickets/t_9fqnvpbc/artifacts/verify-closeout.txt).

Next step:

- Propose Closeout for approval. No deployment, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): shutdown `0.0s` repair

Current build stage:

- Ticket `t_s0q8d2ln` is cut on branch `codex/fix-shutdown-zero-budget` from `main` at
  `bf2ac9b`. The implementation and all accepted review corrections are complete. A fresh final
  Codex full-diff review reports `NO VIOLATIONS`; the final standards re-review reports no code-smell
  findings and only requested this memory cleanup. The final spec re-review found one public-shape
  overreach and one inaccurate role label; both are corrected. The closing spec review then found a
  remaining SQLite busy-timeout deadline leak; it is reproduced and corrected. The final SQLite-
  aware Codex review reports `NO VIOLATIONS`, and both closing review axes report no findings.
- Shutdown now closes Employee admission, interrupts only running turns bound to their Ticket's
  durable Employee session, settles unfinished visible turns before process exit, and preserves the
  Ticket/session for restart recovery. Every interrupt, drain, gateway, child-process wait, and reader
  join consumes the same absolute deadline. A zero-time router observation is inconclusive, while a
  positive-time stuck router still errors. Every unique role gateway is attempted even after failure.
- No configuration, durable state, Ticket status, scheduler, retry queue, public adapter contract, or
  new lifecycle owner was added.
- The first canonical gate invocation failed because the shared `.venv` editable install imported
  `/Users/khushaljagota/.hermes/planning-v2/src` instead of this worktree. Its 39 unit and five e2e
  failures are older-source/new-test mismatches, including missing Chat recovery fields and the
  pre-fix shutdown behavior. The full failed transcript is
  `data/verify/t_s0q8d2ln-environment-fail.log`. With `PYTHONPATH` pinned, Python resolves this
  worktree's `src/planner`.
- The corrected-source canonical gate exposed an acceptance-fixture contention contradiction, not
  a production deadline failure. The process fake's 5,000-delta flood had produced 2,241 update
  events and 51,520 characters before shutdown, then the log reported `database is locked`. That
  flood manufactured the same SQLite contention which the separate locked-database regression
  deliberately owns. The process fixture now emits one nonempty delta, still requires the visible
  turn to settle `interrupted`, and also rejects any shutdown-settlement SQLite lock error. The
  locked-database regression is unchanged.
- A fresh read-only Codex review of the complete corrected diff, including the fixture/lock-boundary
  distinction, reports `NO VIOLATIONS` with model `gpt-5.5` at high reasoning effort. Recording that
  review is trivial integration glue performed directly by the orchestrator. No implementation changed.
- The corrected tree passes the complete canonical gate with this worktree's `src` pinned in
  `PYTHONPATH`: Ruff; mypy across 115 source files; 791 unit tests; compile/static and CSS checks;
  Svelte check with zero errors and warnings; production build; frontend tests; and 94 Playwright
  tests. Every gate is `ok` and the run ends `VERIFY: PASS`. The full transcript is retained at
  `data/verify/t_s0q8d2ln-pass.log`; the earlier genuine fixture-race failure is retained separately
  at `data/verify/t_s0q8d2ln-process-race-fail.log`.

What just passed:

- The production-process regression was RED with the exact `0.0s` router error and Uvicorn shutdown
  failure. Its corrected acceptance fixture keeps one nonempty partial output and still requires exit
  code zero, clean application shutdown, visible worker-turn settlement as interrupted, no SQLite
  settlement lock error, and an unchanged Ticket Employee session. It passes 10 consecutive process
  invocations. The deliberate locked-settlement regression and the ordinary unlocked settlement
  regression both pass; Ruff on the changed test and the complete diff check are clean.
- The expanded focused suite passes 169 tests with two existing warnings. It covers exact-once
  concurrent stop, missing and parked session guards, lock and reply deadline consumption, child
  cleanup, all-role cleanup, zero-versus-positive router waits, first-wins Chat and Ticket settlement,
  shutdown late completion, and ordinary-Pause late completion.
- Ruff is clean, mypy passes across 113 source files, and diff and whitespace checks are clean.
- Three Codex implementation-review rounds found and drove corrections for process-exit settlement,
  late Ticket completion, and their combined races. The final fresh implementation review and the
  final post-two-axis full-diff review report `NO VIOLATIONS`; the fresh post-fixture full-diff review
  also reports `NO VIOLATIONS`.

Next step:

- Hand the committed `codex/fix-shutdown-zero-budget` branch back to the owner. No merge, push,
  deployment, restart, or live-database action is authorized in this ticket.

Blockers:

- None.

## Current work cycle (2026-07-14): durable failed Chat turns

Current build stage:

- Ticket `t_f9ue37gz` is integrated into `main` by merge commit `bf2ac9b`; the verified
  implementation commit is `499b5be`. `main` has since advanced to `8fbf648` with the independently
  verified shutdown repair and remains checked out in `/private/tmp/panels-main-shutdown-closeout`.
- Failed and interrupted `chat_turns` now project into Panels Chat with partial output preserved. A
  guarded Continue action can resume only the latest eligible human turn in its exact current Hermes
  session, without replaying the original prompt.
- The shared Ticket and Chief Chat panel renders quiet, distinct Failed/Interrupted outcomes. Day Chat
  keeps the same backend state contract; no Day Chat screen was invented.
- The live v20 database was backed up with SQLite integrity `ok`, then migrated through canonical startup
  to schema v21. The live database reports integrity `ok`, the recovery column and unique index exist,
  and `/api/chat/t_f9ue37gz/state` serves the new `outcomes` contract. Panels now runs from the `main`
  worktree on port 8767.

What just passed:

- Focused backend tests cover ordinary and partial failures, interruption, session/turn/worker races,
  duplicate and stale recovery rejection, Ticket/Day/Chief eligibility, and the v20→v21 migration.
- Svelte check, frontend unit tests, production build, and four focused Playwright cases pass, including
  refresh, exact-once partial output, continuation, no-action failure, Chief interruption, and startup
  restart recovery.
- Independent implementation review and the post-merge integration review both report `NO VIOLATIONS`.
- The post-merge canonical gate passes Ruff, mypy across 115 source files, 775 unit tests, compile/static
  and CSS checks, Svelte check with zero errors and warnings, production build, frontend tests, and 94
  Playwright tests, ending `VERIFY: PASS`. The
  [closeout transcript](/files/tickets/t_f9ue37gz/artifacts/closeout-verify.txt) is retained with the ticket.
- A [real implementation screenshot](/files/tickets/t_f9ue37gz/artifacts/implementation.png) shows the
  preserved partial response, compact Failed outcome, safe Continue action, and unchanged composer with
  no clipping.

Next step:

- Await Closeout approval. No merge, deployment, restart, live migration, or ticket-owned cleanup remains.

Blockers:

- None.

## Current work cycle (2026-07-14): stage-level ownership

Current build stage:

- Ticket `t_ue4pt9ru` is integrated into `main` by merge commit `1595cf7`. During Closeout the user
  rejected the remaining Ticket-level Execution route, so its removal is now being completed directly
  on `main` without touching unrelated owner edits in the checkout.
- Stage ownership and scope remain separate. Worker type now selects the specialist skill; Tickets no
  longer persist, serialize, edit, prompt with, or display a separate execution route.
- Schema v23 removes the retired column and legacy audit/history exposure while preserving Ticket state and
  the legacy `khushal` coding ownership mapping. The UI retains only Owner and Continue/Stop controls.

What just passed:

- Focused RED/GREEN coverage passed for schema creation and migration, Ticket contracts and API rejection,
  worker prompts, and the Owner-only Ticket facts UI.
- Canonical `./verify` passes Ruff; mypy across 116 source files; 799 unit tests; compile/static and CSS
  checks; Svelte check with zero errors and warnings; production build; frontend tests; and 96 Playwright
  tests, ending `VERIFY: PASS`.
- Independent read-only review found no blocking logic or security issues. Its two suggestions were resolved:
  the worker-prompt docstring now names Worker-type skill selection, and legacy `khushal` mapping is covered
  through the full `create_schema` migration path.
- A delayed second review then found that legacy route audit events remained externally readable. The v23
  follow-up now removes those events, redacts old generated worker-step prompts in stored chat and Hermes
  history views, and removes the obsolete private v21 route-writing migration. Independent follow-up review
  found no blocking logic or security issue; its coverage suggestion for `implementer` and corrupt payloads
  was added before the final canonical pass.

Next step:

- Commit only the scoped v23 follow-up and refresh Closeout evidence.

Blockers:

- None.

## Current work cycle (2026-07-14): Workspace ticket ordering

Current build stage:

- Ticket `t_mw2vegjw` is integrated into `main` by merge commit `e53bfe0`; the ticket branch is
  deleted. The product change remains limited to the Workspace sorter, its focused browser regression,
  the served bundle, and the matching frontend decision record.
- Within each existing project category, rows now sort by served Worker-type manifest position,
  that type's served Stage position, newest `activity_at`, then the existing board sequence. Workspace
  waits for both board and manifest resources so the retired activity-first order never flashes first.

What just passed:

- The focused Playwright regression failed first against the activity-first implementation, then
  passes with `coding` and `new_worker` tickets proving type order, per-type Stage order, and
  newest-first activity ties while retaining category, status-filter, and Hide done assertions.
- Svelte check reports zero errors and warnings; the complete frontend unit script passes.
- Initial independent Codex review found the pre-manifest fallback/timing gap. Workspace now treats the
  manifest as a required resource, the browser test waits on a manifest-backed project section, and
  corrected-diff review session `019f60c1-64fe-7270-8baf-499a4b3a42e6` reports `NO VIOLATIONS`.
- The branch-level `./verify` passed before integration. Main advanced with unrelated Chat work, so the
  generated bundle was rebuilt during the merge; focused Svelte and browser checks passed, and fresh
  integration review session `019f621d-46b1-7913-a11b-ba8d7ba18da4` reports `NO VIOLATIONS`.
- The one post-merge canonical `./verify` passes Ruff; mypy across 115 source files; 791 unit tests;
  compile, static, CSS, and frontend checks; production build; frontend tests; and 94 Playwright tests,
  ending `VERIFY: PASS`. The full transcript is
  `[post-merge verify transcript](/files/tickets/t_mw2vegjw/artifacts/verify-closeout.txt)`.

Next step:

- Propose Closeout for approval. No deploy, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): managed HTML sibling assets

Current build stage:

- Ticket `t_z79gfuuf` is integrated into `main` by merge commit `5772978`; the ticket branch and isolated
  worktree are removed. Unrelated owner note edits and the nested frontend worktree were preserved across
  integration and remain uncommitted.
- Both managed HTML preview lifecycles now prepare fetched HTML through one shared absolute managed-base
  helper before assigning it to embedded `srcdoc` or the full-page Blob URL. The iframe sandbox remains
  exactly `allow-scripts`; fetch cancellation, source cleanup, and Blob revocation are unchanged.
- The focused Playwright fixture covers relative and root-relative images and stylesheets in both preview
  surfaces. The frontend system doc describes the same contract.

What just passed:

- The new focused browser case passes after the shared base helper was added, including the corrected
  HTML-aware insertion case with a false `<head>` token before the real document head. The complete frontend
  unit script and Svelte check also pass.
- The first independent review found that raw string matching could mistake `<head>` text inside a comment
  for the real document head. The helper now parses HTML, prepends the managed base to the actual head, and
  serializes the document; the new false-token regression passes. Fresh corrected-diff Codex review reports
  `NO VIOLATIONS`.
- The canonical branch gate passes Ruff; mypy across 115 source files; 760 unit tests; compile/static, CSS,
  and frontend checks; production build; all frontend tests; and 91 Playwright tests, ending `VERIFY: PASS`.
- The post-merge canonical gate on `main` passes the same complete set with 760 unit and 91 Playwright tests,
  ending `VERIFY: PASS`. The retained transcript and ticket artifact are
  `data/verify/t_z79gfuuf-main-pass.log` and
  `[full verify transcript](/files/tickets/t_z79gfuuf/artifacts/verify.txt)`.

Next step:

- Propose Closeout for approval. No deploy, restart, migration, or follow-up ticket applies.

Blockers:

- None.

## Current work cycle (2026-07-14): slate-blue brand accent

Current build stage:

- Ticket `t_bbzgswpj` is integrated into `main` by merge commit `b479e5b`; the ticket branch and worktree are
  removed. The four-token replacement and its focused regression were small enough to implement directly
  rather than dispatching a second ticket. Unrelated active owner edits were restored after the merge.
- The owner selected Option A — Soft Steel. The shared brand family is now `#9aadd2` bright,
  `#222a38` surface, `#dce6f8` text, and `#111318` ink. Green done and red error tokens are unchanged;
  no component layout or interaction styling changed.

What just passed:

- The focused Playwright regression failed first against the amber token family, then passes 2/2 on desktop
  and mobile after the shared-token change. It checks the exact family, navigation, waiting, pending,
  approval, link, hover, focus, selected, done, and error treatments.
- Real ticket screenshots were captured at 1440×1000 and 390×844 with no browser console or page errors.
  Contrast ratios are 7.75:1 for bright-on-base, 8.21:1 for ink-on-bright, and 11.48:1 for accent text on
  the accent surface.
- Independent Codex review found two concrete gaps: the focused test omitted the explicit current-waiting
  stage mark, and live CSS comments still named the retired amber accent. The waiting assertion was added and
  mutation-checked across both viewports; every stale live comment was corrected. Final fresh review reports
  `NO VIOLATIONS`.
- The first canonical gate passed mypy across 115 files, 760 unit tests, compile/static checks, the complete
  frontend gate, and 90 Playwright tests. It failed only Ruff on seven overlong lines in the new test. The
  test was reformatted, the two embedded JavaScript strings were wrapped, and focused Ruff plus both browser
  cases are green.
- The corrected branch passes the complete canonical gate: Ruff; mypy across 115 files; 760 unit tests;
  compile/static and CSS checks; Svelte check with zero errors and warnings; production build; frontend
  tests; 90 Playwright tests; final `VERIFY: PASS`. The transcript is
  `data/verify/t_bbzgswpj-pass.log`.

Next step:

- Await Closeout approval. No merge, deploy, restart, migration, or follow-up work remains.

Blockers:

- None.

## Current work cycle (2026-07-14): architecture deepening integration

Current build stage:

- The reviewed architecture head `9e51df2` is integrated with restart-recovery main `49660f5` through merge
  commit `2384159`; the import-order repair is `b574046`, and the verification record is `c5279d6`. Main is
  fast-forwarded to `c5279d6`. No push or pull request was created.
- The public and domain shape follows the architecture branch: required immutable Worker type, direct
  stored Stage reads, Automatic Employee-step eligibility, Ticket-focused Review, canonical Panels Chat,
  and explicit Employee session history. Retired readiness names, Ticket Chat identity, generic Chat
  transport bags, old routes, and live `coding` defaults remain deleted.
- The source integration is complete. Restart recovery now uses the same
  architecture owners: canonical first-wins Chat settlement, strict typed gateway resume, Employee session
  id transitions, and the renamed discovery/eligibility runtime. Startup recovers ordinary human Chat and
  running Employee steps before automatic discovery; post-handoff worker turns settle without re-prompting;
  one absolute deadline is shared through runtime and gateway shutdown.
- Focused backend tests for Chat, Employee recovery, loop composition, gateways, session history,
  discovery, eligibility wake, config, the closed Chat ingress, and Worker-type/migration boundaries are
  green. Both named Playwright cases pass; compile, Ruff, mypy, the retired-surface scans, conflict-marker
  scan, and diff check pass. Independent corrected-diff review reports `NO VIOLATIONS`.
- The first fresh merge-diff review found one High omission: no-session Employee restart recovery errored
  the Ticket but left its stale visible worker turn running. The accepted repair now settles that worker turn
  through canonical Chat settlement with the same exact error before the canonical Ticket transition, with
  no replacement turn or gateway call. Its focused regression and the complete Employee-runner/core-loop
  pair pass; Ruff, focused mypy, compileall, and diff check pass. Fresh corrected-diff review session
  `019f5ffb-6b3b-7310-9f2f-c8e95b908436` reports `NO VIOLATIONS`. The merge commit and canonical gate remain.
- Merge commit `2384159` was created. Its first canonical gate completed all suites and found only one Ruff
  import-order failure in `tests/typing/tt02b_field_seam_cases.py`; mypy passed across 115 source files,
  760 unit tests passed, the complete frontend gate passed, and 88 Playwright tests passed. The import-only
  repair was applied directly as trivial integration glue and committed at `b574046`.
- The corrected integration head `b574046` passes the canonical gate: Ruff; mypy across 115 source files;
  760 unit tests; compile/static and CSS/Markdown checks; Svelte check with zero errors and zero warnings;
  production build; frontend tests; 88 Playwright tests; final `VERIFY: PASS`. The full transcript is retained
  at `data/verify/architecture-merge-pass.log`.
- The actual main checkout initially retained ignored Python bytecode under the deleted `ticket_types`
  directory. Removing only that stale cache made the deletion boundary honest. The final main checkout then
  passed the same complete canonical gate with 760 unit and 88 Playwright tests; final `VERIFY: PASS`. Its
  transcript is `data/verify/architecture-deepening-merged-pass.log`. Owner note edits and the unrelated
  nested frontend worktree remain uncommitted; the exact pre-merge note snapshot remains in the named stash.

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
- AD06's first independent plan review found four accepted violations: a claim-local Chat rule would split
  AD03 eligibility; a human-only owner cannot honestly own worker-origin Pause; ordinary CAS could defeat
  literal `/new`; and the private execution value was not declared. The ticket and decisions now require one
  eight-factor eligibility rule, cross-origin visible Pause control, one-use force-fresh `/new` binding, and
  an exact private execution dataclass. A corrected delegated plan and fresh re-review are required.
- A read-only audit of the owner's explicit Worker-type invariant found no executable violation: all live
  creation paths require a stored Worker type and reads return it without fallback. Two stale docs still
  called `coding` the default; those sentences were corrected as a small integration repair. Historical
  migration/cutover classification remains the only allowed implicit coding assignment.
- AD06's corrected delegated plan passed a fresh independent Codex review with `NO VIOLATIONS` (session
  `019f5f2a-63c2-7231-bd29-038e29ca4159`). The four original findings are fully resolved, and the exact
  owner, transport, transaction, session-binding, settlement, test, deletion, and changed-path contracts
  are ready to lock before implementation dispatch.
- AD06's delegated implementation is committed at `e32dd47`. `ChatTurnLifecycle` now owns human admission
  and execution plus cross-origin visible Pause; human admission and the final Employee claim exclude one
  another under SQLite's write lock; the sole automatic decision has eight factors; causal binding makes
  Panels and the actual Hermes write use the same session; exact `/new` forces only its first fresh bind;
  and generic first-wins Chat settlement is idempotent across completion, failure, and Pause. Employee
  delivery and Ticket settlement remain separate. Independent implementation review session
  `019f5f48-af76-7030-b9d5-1939bf6ba749` reports `NO VIOLATIONS`.
- AD06 is complete. Its committed checkpoint at `70d4291` passes the canonical gate with 714 unit tests and
  80 Playwright tests; the full transcript is retained at `data/verify/ad06-pass.log`.
- AD07's Managed Markdown ticket is defined. It preserves the current continuous `contenteditable` with
  atomic previews and exact source-token serialization; one new frontend owner will concentrate hardened
  rendering, preview reconciliation, serialization, and teardown while `FilePreview` keeps target-specific
  behavior. No visual, parser, backend, resource-cache, AD08, or AD09 change is in scope.
- AD07's delegated implementation plan is complete and contract-locked. Independent review session
  `019f5f5e-5233-7741-8777-cee8b0322dda` reports `NO VIOLATIONS` after checking same-source dirty reset,
  failed-save retry, final-DOM move/deletion reconciliation, read-only preview identity, exact serialization,
  teardown, docs/assets, and the bounded allowlist. Delegated implementation is the next step.
- AD07's delegated implementation is committed at `30edca4`. One `managedMarkdown.ts` owner now holds
  hardened rendering, atomic preview islands, exact serialization, reconciliation, and teardown; the two
  wrappers keep product presentation/edit-save state; `FilePreview` keeps target-specific behavior; and the
  two old lifecycle modules are deleted. Independent implementation review session
  `019f5f6d-4892-7273-8343-51122fe517b4` reports `NO VIOLATIONS`. The canonical gate is next.
- AD07 is complete. Its committed reviewed checkpoint at `05e62a0` passes the canonical gate with 714 unit
  tests and 82 Playwright tests; the full transcript is retained at `data/verify/ad07-pass.log`.
- AD08's corrected delegated Resource Catalogue plan is complete and contract-locked. The first independent
  review found one High omission: Project-name updates were mapped only to Projects even though six cached
  aggregates embed the name. The accepted correction covers Projects, Board, today's Day, backlog Sprint
  items, Ideas, and current Sprint, plus conservatively selected opened Ticket details through a private
  catalogue-owned index. The plan also freezes the temporary Ticket-plus-Chat `chat_session_created`
  dependency. Fresh review session `019f5f81-89a3-7ba2-883b-bb68ce4022b1` reports `NO VIOLATIONS`.
- The AD08 lock retains exactly 13 cached projections, nine named mutation effects, entity-first ordinary
  invalidation, narrow Review inputs, and a semantic-free cache engine. It explicitly forbids live
  Worker-type inference/defaults; only the existing migration may rewrite a historical pre-Worker-type row
  to `coding`.
- AD08's delegated implementation is committed at `d2cc125`. One catalogue now owns all 13 cached reads,
  nine immediate mutation effects, and event dependencies; the generic cache remains semantic-free; phantom
  identities and forwarding modules are deleted; and the served bundle is current. The focused type, Node,
  unit, and 68-test browser evidence is green. Independent implementation review session
  `019f5f98-5fd1-7610-9cd5-92d2cf2a32a8` reports `NO VIOLATIONS`.
- AD08 is complete. Its committed reviewed checkpoint at `4e267c6` passes the canonical gate with 714 unit
  tests and 86 Playwright tests; the full transcript is retained at `data/verify/ad08-pass.log`.
- AD09's delegated Employee-session-history plan is corrected and contract-locked. The first independent
  review found that the plan still blessed the retained live seed importer as a `coding` default. The
  correction makes Worker type a required seed command/programmatic argument and passes it through exactly;
  only the terminal v20 migration may classify a genuinely old row with no stored Worker type as `coding`.
  Fresh review session `019f5fa7-9f49-7322-b33d-a91ccee643f1` reports `NO VIOLATIONS`.
- AD09's isolated product implementation is committed at `0e0bf06`. The first implementation review found
  two real violations: legacy Ticket `chat_session_created` still reached Ticket aggregates, and the
  required browser proof against silently repopulating deleted Panels rows from retained Employee history
  was missing. Both were corrected through delegation. Fresh review session
  `019f5fc8-c093-7163-8bee-2b15db5d19ea` reports `NO VIOLATIONS`; the canonical gate is next.
- AD09 is complete. Its committed reviewed checkpoint at `acf93cd` passes the canonical gate with 738 unit
  tests and 87 Playwright tests; the full transcript is retained at `data/verify/ad09-pass.log`. All nine
  architecture-deepening stages are now complete on the branch.
- The independent whole-program review of `b7ca44a..191f14a` is complete. It inspected the AD01–AD09
  contracts and full branch diff, including live Worker-type ingress, migration-only historical rewrites,
  resource dependencies, generated assets, API deletion seams, Markdown ownership, and Employee-history
  separation. Session `019f5fcf-cfa9-73d0-8741-27e7f687900a` reports `NO VIOLATIONS`; the exact record is
  `orchestration/tickets/architecture-deepening/final-review.txt`.
- The complete reviewed branch passes its final canonical gate at `2982197`: Ruff; mypy across 115 source
  files; 738 unit tests; compile/static and CSS/Markdown checks; Svelte check with zero errors and zero
  warnings; production build; frontend tests; 87 Playwright tests; final `VERIFY: PASS`. The full transcript
  is retained at `data/verify/architecture-deepening-final-pass.log`.

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
- AD06 focused evidence is green: 284 affected unit tests, Ruff over every changed production/test path,
  targeted mypy over all ten changed production modules, compileall, and `git diff --check`. The focused
  contract tests include real two-connection settlement races in both orders, both human-admission /
  Employee-claim lock orders, and actual Hermes session-id assertions for initial and dormant message,
  image, command, and alias writes. These are pre-gate checks, not the completeness claim.
- The independent AD06 implementation review inspected the complete changed path set plus the new untracked
  test before commit and returned exactly `NO VIOLATIONS`. Its transcript and disposition are recorded in
  `orchestration/tickets/architecture-deepening/ad06-deep-canonical-chat-turn/implementation-review.txt`.
- The committed AD06 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 714 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 80 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad06-pass.log`.
- AD07's exact owner API, lifecycle state, wrapper boundary, failed-save retry, serializer preservation,
  preview reconciliation, deletion set, test sequence, docs/bundle work, and changed-path allowlist passed
  independent plan review. The lock is recorded in
  `orchestration/tickets/architecture-deepening/ad07-managed-markdown/contract-lock.md`.
- AD07 focused implementation evidence is green: frontend tests pass; Svelte check has zero errors and the
  three existing Ticket-route warnings; both new browser regressions pass; all 13 file-preview browser tests
  pass; and `git diff --check` is clean. The independent reviewer inspected the complete source, test, docs,
  and served-bundle diff and returned exactly `NO VIOLATIONS`. These are pre-gate checks, not the canonical
  completeness claim.
- The committed AD07 checkpoint passes canonical `PYTHONPATH="$PWD/src" ./verify`: Ruff; mypy across 114
  source files; 714 unit tests; compile/static and CSS/Markdown checks; Svelte check (zero errors, three
  existing warnings), production build, frontend tests; 82 Playwright e2e tests; final `VERIFY: PASS`.
  The complete transcript is retained at `data/verify/ad07-pass.log`.

Next step:

- Merge the clean, reviewed, canonically verified branch serially to `main`, then verify the integrated
  checkout.
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

- **2026-07-13 — Restart and crash recovery** (`49660f5`): startup continues stranded Ticket Employee
  and ordinary Chat work in the same durable Hermes session without replaying the original prompt; one
  configured deadline bounds runtime and gateway shutdown. The pre-integration gate passed 700 unit and
  78 Playwright tests. [D-runtime-restart-continuation]
- **2026-07-13 — Hosted Panels authentication boundary** (`b7ca44a`): Tailscale Serve identity and
  browser-Origin checks cover UI, APIs, assets, files, and WebSockets; ordinary HTTP clients use the
  canonical asynchronous Chief turn. The post-integration gate passed 680 unit and 77 Playwright tests.
  [D-hosted-trusted-ingress]
- **2026-07-13 — Workspace Worker-type and Stage byline** (`7e67828`): the owner-selected stacked byline
  keeps the registered Worker-type and direct stored Stage label together while preserving `StageMark`,
  grouping, filters, selection, and navigation. The post-integration gate passed 655 unit and 76 Playwright
  tests. [D-workspace-row-byline]
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
