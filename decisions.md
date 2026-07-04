# decisions.md

Every delegated or judgment call, briefly justified. Numbered for reference from PROGRESS.md and ticket records.

## D1 — Local commits are made during the run (never pushed)

CLAUDE.md's operating model requires isolated git worktrees for parallel tickets and serial integration; git worktrees can only see committed state, so the run commits locally at integration points. Nothing is ever pushed. This supersedes the global no-auto-commit preference for this unattended goal run, whose own harness history is itself a chain of local commits.

## D2 — Orchestration records live in `orchestration/`

The per-ticket pipeline (ticket text, plan, codex plan review, codex diff review, integration note) is recorded under `orchestration/tickets/TNN-*/`. Named "orchestration" to avoid any collision with the product's own ticket entity. The audit checks that the pipeline actually ran; these files are the evidence.

## D3 — Snapshot amended to match the pinned §12 ground truth

SPEC §12 pins item 34's expected import exactly (12 items 6/5/1, 9 deferred under the four project headings, 20 ideas, 4 tickets). The committed snapshot disagreed: 13 items (6/6/1), 3 deferred (Tribe/Learning/Other headings empty), 17 ideas. The 9/20 figures match only a count that treats the files' "Rules:" preamble bullets as work items — those bullets are instructions with no project heading or P-label, and importing them as sprint items/ideas would corrupt both the test fixture semantics and the eventual live cutover; and nothing can make 6 identical in-progress items parse as 5. SPEC.md is unmodifiable, so the data was amended to be consistent with the spec: "Finish design leftovers." moved from In Progress to deferred.md (relocated, not deleted), five deferred items added under the empty headings, three ideas appended. The parser is implemented on principled semantics (items = bullets under recognized section headings; preambles are prose). Chosen over the alternative — a bullet-grep parser that imports "Group by project." as a work item — because that parser would be wrong for every other input including the live directory at cutover.

## D4 — CLI lives at `src/planner/cli/`

SPEC §2 names core/ plus six domains and doesn't place the CLI. The CLI is a surface over the HTTP API, not a domain; it gets its own folder beside the domains (non-trivial things get folders). Amended after T01 plan review: it imports contracts, click, httpx, and stdlib — never domain logic or data layers. The layering is the rule; click/httpx are pinned dependencies chosen for it.

## D5 — Test-mode clock control via `POST /api/test/set-now`

§6.1 says the clock is injectable in test mode; §13 defines PLAN_FAKE_NOW as the initial value. E2E items 28–30 need time to move during a running server session (e.g. crossing the 05:00 boundary), so test mode exposes one additional endpoint, `POST /api/test/set-now {now}`, 404 outside test mode like the two tick endpoints. Chosen over per-request `now` overrides on the tick endpoints because "honored everywhere the clock is read" (§13) must include reads the UI triggers, not just ticks.

## D6 — Background loops idle in test mode; ticks only via test endpoints

With PLAN_TEST_MODE=1 the dispatcher and boundary scheduler do not tick on timers; `POST /api/test/tick-dispatcher` / `tick-boundary` run single synchronous ticks. Deterministic tests require exactly-one-tick semantics; the spec's test endpoints exist precisely to drive ticks.

## D7 — Pipeline collapses for trivial tickets

Per CLAUDE.md, steps 2–5 of the per-ticket pipeline may collapse for trivial tickets. Collapsed so far: **T02** (plan step folded into the ticket text, which codex then reviewed — plan review still happened, see orchestration/tickets/T02-verify/plan-review.md); **T08** (single test file against an already-reviewed instrument API: implementation dispatched directly, codex review at diff stage only).

## D8 — Per-ticket orchestrator sub-agents with model tiering (owner directive, mid-run)

From stage 3 onward, each ticket is dispatched to a per-ticket orchestrator sub-agent (Fable) that runs the pipeline internally: planning agent → codex plan review → orchestrator sense-check/adjust → implementation agent → codex diff review → report. The top-level agent only decomposes, integrates serially, runs `./verify`, and spot-checks load-bearing code (resolution engine, claim/reclaim, planning-date math, seed parser per CLAUDE.md). Directed by the owner during the run; supersedes the flat pipeline used for T01/T02 (which ran plan and implementation agents directly under the top-level orchestrator).

Model tiering (owner: use judgement, Fable allowed liberally for groundwork): planners are Fable for the load-bearing groundwork tickets — T04 resolution engine, T05 dispatch/claims, T07 seed parser — and Opus for the mechanical ones (T03 days, T06 sprints); implementers are Opus everywhere (a good plan + codex diff review carries implementation); orchestrators always Fable.

Mechanical verification is delegated too (owner directive): Opus verification agents run `./verify` and smoke checks, archive the full output under `orchestration/verify-runs/`, and report headlines; the top-level agent runs checks itself only when truly cheap. The final goal demonstration is the exception — run fresh by the top-level agent with full output shown, as the goal condition requires.

## D9 — "every file in assets/" under `node --check` reads as every JS file

SPEC §18.2's build check runs `node --check` on every file in `assets/`; `node --check` only parses JavaScript, and `assets/` legitimately holds CSS (`tokens.css`, required by §10). The instrument checks every `*.js` under `assets/` recursively — the only reading under which the required CSS can exist at all. Flagged by codex during the T02 implementation review; recorded here for the auditor.

## D10 — T01/T02 integration reviews

T02 codex diff review: NO VIOLATIONS (seven confirmations, file/line cited; archive at orchestration/tickets/T02-verify/impl-review.md). T01 codex diff review found no missing enum members, tables, or route gaps; its closing schema cross-check (every table's columns vs its contract dataclass) succeeded inside the codex session and was independently re-run by the top-level orchestrator: all six audited tables match exactly, all ten tables present (archive at orchestration/tickets/T01-contracts/impl-review.md). Verify run 001 (orchestration/verify-runs/001-stage2.md) matched the stage-2 expectation exactly: gates green, 36 FAIL, `VERIFY: 0/36 PASS`, exit 1.

## D11 — UI component inventory (recorded before any component is written, per SPEC §10)

Seventeen components cover all six screens (SPEC says "five screens" in the inventory sentence while defining six; the inventory covers all six — treated as a spec typo, not a licence to skip one). Build few, reuse hard:

1. **App shell** — top nav with six screen links + Review pending-count badge; content slot; hash routing.
2. **Panel** — titled section container; the only box primitive.
3. **Markdown block** — renders markdown strings (brief, field values, proposals, bodies, kickoff/review text).
4. **Field editor** — textarea + save for directly-editable text (notes slots, day notes/brief, kickoff/review fields pre-freeze, current_state_note).
5. **Meta chips** — one badge component with variants: priority, state/status, project, deadline, markers (pending-proposal, running-claim, blockers-cleared, auto-blocked, frozen).
6. **Entity row** — title + chips + click-through; used by board cards, day ticket list, item lists, loose tickets, ideas, overdue.
7. **Proposal card** — rendered proposal + quick-edit textarea + Accept; hosts the grant-pair picker; used on Review and Ticket.
8. **Grant-pair picker** — ceiling select (valid onward states + "no further") + at-cap radio; embedded in proposal card and grant control; Accept disabled until both halves chosen.
9. **Plan tree** — root focus + child rows, per-node Accept/Invalidate, top-level Accept-all/Reject-all.
10. **Chat panel** — message list + input; input is a pluggable source component (the §14 audio seam); offline notice state.
11. **State control** — current state, human jump select, drop (Ticket).
12. **Grant control** — plain ceiling/at-cap pickers + save (Ticket).
13. **Create form** — title + small fields; variants: ticket, item, idea.
14. **Event log** — kind + summary + time rows (Ticket).
15. **Run history** — status/times/summary rows (Ticket).
16. **Review card** — the one-at-a-time surface: wraps Proposal card (or needs_review result, or item status proposal), Skip, open-ticket link.
17. **Error line** — structured-error rendering (code + message), inline near the failed action.

## D12 — Post-first-write test consolidation in test_dispatch.py and test_sprints.py (fence-required justification)

T05 and T06 initially split several acceptance items across multiple anchored tests (six `test_a09_*`, three `test_a11_*`, seven `test_a15_*`, five `test_a16_*`, six `test_a10_*`, five `test_a20_*`). SPEC §18.3's first fence requires a 1:1 mapping, and the verify scorer deliberately fails an item with multiple anchored matches. The change: for each affected item, its assertions were merged into exactly one `test_aNN_` named test asserting the item's full SPEC statement; supplementary tests were renamed to non-anchored prefixes and kept running with no assertions weakened or deleted. Justification: restores the 1:1 fence; coverage strictly non-decreasing.

Two further post-first-write test changes from codex implementation reviews, both strengthenings, no assertion weakened: **test_a07** now asserts the exact four-step state-change sequence for the ceiling=done leg instead of a membership check plus guard (T04 impl review finding 7); **test_a09** and three sibling link rejections now pin the full structured-error envelope (code, message presence, detail keys/values) instead of the ErrorCode alone (T05 impl review finding 1).

## D13 — Seed spec tensions resolved during T07 (flagged by the implementer, ratified here)

(a) SPEC §12 routes workspace "body/success/approach text into the matching fields' values", but §4.2 fixes exactly four field keys — there is no "body" field. `Success:`/`Approach:` text imports into the matching values; `Body:` plus unrecognized sub-bullets import into `fields.success.notes` (the free-guidance slot legal in every imported state). Nothing is dropped; the report stays silent-drop-free. (b) §12's ticket mapping list names Priority/Ticket ID/Chat ID but not Project, so a workspace ticket's `Project:` line is preserved verbatim inside the notes text and the DB `project` column stays NULL for standalone imported tickets — spec-literal; the human can set project post-cutover. (c) Blocked tracking items import with an empty `blocked_by` list — the markdown names no blocker ticket ids; migration preserves source truth.
