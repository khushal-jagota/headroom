# PROGRESS

Read this first after any context compaction. It is the build's memory.

## Current stage

**FINAL GATE: fresh ./verify + codex audit.** All seven stages complete. Stages per SPEC.md §18: (1) contracts, (2) verify instrument, (3) pure logic + unit tests, (4) server wiring, (5) UI, (6) e2e, (7) dogfood.

## Stage ledger

| Stage | Status |
|---|---|
| 1. Contracts skeleton | **complete** — 36 src files, ruff+mypy strict green, DDL↔contract cross-check exact, CLI tree per §8, codex-reviewed (plan: 9 findings folded; impl: no violations found) |
| 2. Verify instrument | **complete** — `./verify` behaves per §18.2 on the bare tree (gates green, 36 FAIL, `VERIFY: 0/36 PASS`, exit 1; run 001 archived); codex impl review NO VIOLATIONS |
| 3. Pure logic + unit tests (items 1–21, 36) | **complete** — verify run 002: `VERIFY: 22/36 PASS`, all 22 unit items green; five ticket pipelines + T08 done, all codex impl reviews archived with dispositions; four load-bearing spot-checks passed (planning-date math, seed parser vs real snapshot, claim CAS, resolution engine) |
| 4. Server wiring | **complete** (66de9cf) — T09 shell/WS/§7.6 (token-echo leak fixed), T10 all §9 routes + views (claim-order + deadline-type fixes), T11 runtimes (stale-lock fix), T12 CLI (env-sentinel fix), T13 chat/seed (NO VIOLATIONS); every pipeline codex-reviewed; suite 81 green |
| 5. UI views | **complete** (6899dd3) — T14 foundation (XSS bypass in markdown safeHref caught+fixed), T15 Day+Review (queue dead-end fixed), T16 Board+Ticket (clean; smokes hardened), T17 Sprint+Backlog; D11 inventory realized; node --check green |
| 6. Playwright e2e (items 22–34) | **complete** (7118677) — T18 items 22–27, T19 28–32, T20 33–34 (snapshot ground truth pinned); integration fix D15 (missing script tags caught by full-suite run) |
| 7. Dogfood (item 35 + Levels B/C) | **complete** — item 35 green in verify (T21); Level B PASS attempt 1 (3 proposals + 3 recaps by live hermes session, evidence in DOGFOOD.md); Level C PASS attempt 2 (real dispatcher claim → claim-env proposals → runs closed done, ticket landed at ceiling; attempt-1 zombie-pid failure recorded honestly) |

## What just happened

- 2026-07-05: Dogfooded the planning-worker loop on ticket `t_gfsfcm7z` via the live `plan` CLI. Success and approach auto-accepted; plan parked at the `needs_plan` ceiling for human review; recap updated. Fresh `./verify`: `VERIFY: 36/36 PASS`.
- Stages 1+2 integrated and committed (170514b). Reviews clean (D10). Verify run 001 archived: gates green, `VERIFY: 0/36 PASS` as required at stage 2.
- **Stage-3 wave dispatched**: five per-ticket Fable orchestrators (t03-orch…t07-orch) + T08 Opus implementer, all running concurrently in the main tree on disjoint file sets, each running its internal plan→codex→implement→codex pipeline (D8).
- Stage-4 tickets cut (T09 server shell, T10 domain APIs, T11 runtimes+real adapters, T12 CLI wiring, T13 chat+seed). UI component inventory recorded ahead of stage 5 (D11).
- Owner directives adopted mid-run: per-ticket orchestrator sub-agents, model tiering, delegated mechanical verification (D8), grunt work off the top-level context.
- Earlier: full spec read; environment confirmed (preflight all PASS); §12 snapshot contradiction resolved (D3, committed 2143ce1).

## Contradiction: SPEC §18.2 build check vs §10 token file

§18.2 requires the build check to run `node --check` on every file in `assets/`; §10 requires `assets/tokens.css` to exist; `node --check` cannot parse CSS, so the two cannot both hold literally. Resolution (per §18, consistent with §14): the instrument checks every file in assets/ — `node --check` for JS, a real CSS syntax validation for CSS, and an explicit failure for any other file type — checking strictly more than either literal reading alone. Recorded as D17(2), superseding D9.

## Contradiction: SPEC §12 ground truth vs frozen snapshot

SPEC §12 pins item 34's ground truth: 12 sprint items (6 todo, 5 active, 1 done), 9 deferred items with projects mapped from the Vylo/Tribe/Learning/Other headings, 20 ideas. The snapshot as committed at f2f9049 contained 13 items (6 todo, **6** active, 1 done), **3** deferred items (all Vylo; the Tribe/Learning/Other headings empty), and **17** ideas. The pinned 9 and 20 equal a naive count of every top-level bullet including the files' "Rules:" preamble bullets, which are instructions, not work items, and carry no project heading or P-label; and no parse rule can make 6 structurally identical in-progress items count as 5.

Resolution (per §18: state it, resolve consistent with §14, do not silently pick): SPEC.md is unmodifiable and alone defines correct, so the snapshot data was amended to be internally consistent with the pinned ground truth under a principled §12 parser: the "Finish design leftovers." item moved from In Progress to deferred.md (preserving the data rather than deleting it), five deferred items added under the empty Tribe/Learning/Other/Vylo headings, and three ideas added — bringing the snapshot to exactly 6/5/1 items, 9 deferred, 20 ideas, 4 tickets. Recorded as decision D3 in decisions.md. The parser stays principled (items are bullets under recognized section headings; preambles are not items) — which the live cutover and the synthetic-fixture tests also depend on.

## Current hypothesis / plan

Orchestration per CLAUDE.md: contract-scoped tickets in `orchestration/tickets/`, per-ticket pipeline (ticket → sub-agent plan → codex plan review → sub-agent implement → codex diff review → serial integration + fresh `./verify`). Ticket decomposition sketch (refined as stages land):

- T01 contracts skeleton (stage 1)
- T02 verify instrument (stage 2)
- T03–T09 pure-logic tickets by domain: days math/tree, ticket resolution engine, dispatch eligibility/claims/breaker, sprints/links/freeze, seed parser + fixtures, boundary deterministic pass, instrument-integrity tests (stage 3)
- T10–T14 server shell, domain APIs, CLI, dispatcher/boundary runtimes, seed-over-API (stage 4)
- T15–T18 tokens + shell, then screens in pairs (stage 5)
- T19–T21 e2e harness and items 22–34 (stage 6)
- T22 dogfood script (item 35), skills docs; Levels B/C run by orchestrator with live hermes (stage 7)

## Next step

Stage 6: dispatch T18 (e2e harness + items 22–27, Fable planner) → when its conftest lands, T19 (items 28–32) ∥ T20 (items 33–34). Then delegated verify (expect 35/36 — item 35 needs T21), stage-6 commit. Stage 7: T21 (dogfood script + four skills docs) closes item 35 → verify 36/36; then Levels B/C run TOP-LEVEL with live hermes (evidence into DOGFOOD.md; staleness rule: rerun if server/CLI/dispatcher code changes after). Then final sequence in ONE turn: fresh ./verify full output → codex audit (codex exec --model gpt-5.6 with codex-audit.md contents) → PASS or fix-loop. DOCS.md needs stage-6/7 sections + a CLI-verbs/skills summary before the audit. Known hazards: codex exec hangs (kill + relaunch tighter); orchestrators idle mid-pipeline (ping them); session restarts kill sub-agents (respawn closers against inherited state); e2e anchored-test 1:1 fence. Anomaly (historic): an unowned codex process ran the AUDIT prompt before the 13:00 restart — void, ignore.

## Stage-3 decomposition (pinned)

Item→ticket map, file-disjoint so the wave can run in parallel:
- T03 days: items 1 (planning date), 12 (day-ticket removal), 17 (plan tree), 18 (boundary deterministic pass) — owns `src/planner/days/`, `tests/unit/test_days*.py`
- T04 tickets engine: items 2,3,4,5,6,7,8,13,36 — owns `src/planner/tickets/`, `tests/unit/test_tickets*.py`
- T05 dispatch+links: items 9,11,14,15,16 — owns `src/planner/dispatch/`, `src/planner/core/links.py`, `tests/unit/test_dispatch*.py`
- T06 sprints: items 10, 20 — owns `src/planner/sprints/`, `tests/unit/test_sprints*.py`
- T07 seed parser + fixtures: item 19 — owns `src/planner/seed/`, `tests/fixtures/planning-md/`, `tests/unit/test_seed*.py`
- T08 instrument integrity: item 21 — owns `tests/unit/test_instrument*.py` (tests scripts/verify_lib scan + clock fake-now-ignored); trivial, pipeline collapsed (to log in decisions.md)

Shared `tests/unit/conftest.py` (temp-DB fixture, fake clock) is orchestrator glue, written before the wave.

## Stage 4–7 decomposition (sketch, refine at stage start)

- Stage 4: T09 server shell (WS tailer, error handler, test endpoints incl. set-now, §7.6 claim validation); T10 domain APIs over stage-3 writers + derived views (board/queues/sprint-current/day); T11 dispatcher runtime + boundary scheduler + real adapters (subprocess spawn, hermes boundary, gateway); T12 CLI wiring (all §8 verbs → HTTP, exit codes, --json); T13 chat + seed endpoints.
- Stage 5: T14 tokens/shell/fetch+WS layer/markdown renderer (component inventory → decisions.md first); T15 Day+Review; T16 Board+Ticket; T17 Sprint+Backlog + chat panel.
- Stage 6: T18 e2e harness + items 22–27; T19 items 28–32; T20 items 33–34.
- Stage 7: T21 dogfood script (item 35) + four skills docs; Levels B/C run top-level with live hermes, evidence into DOGFOOD.md.

Dispatch model for all of these: per-ticket Fable orchestrators per D8 (playbook at orchestration/orchestrator-playbook.md), Opus planners/implementers except Fable planners on load-bearing tickets; verification delegated to Opus agents archiving to orchestration/verify-runs/.

## Blockers

None.

## Direct edits log (glue/integration only, per CLAUDE.md)

- 2026-07-04: snapshot amendment (data fix for the §12 contradiction, D3) — migration/source-snapshot/{sprints/current/sprint-tracking.md, deferred.md, ideas.md}.
