# PROGRESS

Read this first after any context compaction. It is the build's memory.

## Current stage

**Stage 1 (contracts skeleton) — starting.** Stages per SPEC.md §18: (1) contracts, (2) verify instrument, (3) pure logic + unit tests, (4) server wiring, (5) UI, (6) e2e, (7) dogfood.

## Stage ledger

| Stage | Status |
|---|---|
| 1. Contracts skeleton | in progress |
| 2. Verify instrument | not started |
| 3. Pure logic + unit tests (items 1–21, 36) | not started |
| 4. Server wiring | not started |
| 5. UI views | not started |
| 6. Playwright e2e (items 22–34) | not started |
| 7. Dogfood (item 35 + Levels B/C) | not started |

## What just happened

- Full read of SPEC.md, PRINCIPLES.md, codex-audit.md, harness-prep.md, and every file in `migration/source-snapshot/`.
- Environment confirmed: Python 3.14.3 venv with pinned deps, Playwright chromium, node v22.22.3, codex CLI 0.142.5, hermes CLI present. All preflight items PASS per harness-prep.md.
- **Contradiction found and resolved (see below).**

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

Write ticket T01 (contracts skeleton), dispatch planning sub-agent, codex-review the plan, implement, review, integrate.

## Blockers

None.

## Direct edits log (glue/integration only, per CLAUDE.md)

- 2026-07-04: snapshot amendment (data fix for the §12 contradiction, D3) — migration/source-snapshot/{sprints/current/sprint-tracking.md, deferred.md, ideas.md}.
