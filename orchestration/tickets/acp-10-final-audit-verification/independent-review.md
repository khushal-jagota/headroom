# ACP-10 final settled-tree independent review

Date: 2026-07-21 (Europe/London)

## Verdict

**READY. Zero unresolved P0/P1 findings.**

The settled ACP tree, requirement ledger, backend qualification, Computer Use record,
legacy-absence evidence, current live docs, and the named load-bearing implementation seams agree
with the ACP-10 contract and owner decisions. I found no concrete requirement violation or evidence
contradiction that warrants returning work to an implementation ticket.

This is the required read-only independent review. I did not run `./verify`, focused tests, a server,
or a browser; I did not use an external model CLI; and I did not change product source, tests, docs,
configuration, generated assets, `PROGRESS.md`, or `decisions.md`.

## Inspected evidence and contracts

- Owner brief: the pasted implementer brief at
  `/Users/khushaljagota/.codex/attachments/5c7bf47e-0634-4e06-8262-c7d5541d8df6/pasted-text-1.txt`.
- Standing and program intent: `PRINCIPLES.md`, `orchestration/acp-migration/plan.md`, both program
  reviews, the frozen ACP ticket contracts relevant to the seams below, and their final review
  dispositions.
- ACP-10 audit artifacts: `requirement-audit.md`, `backend-qualification.md`,
  `computer-use-evidence.md`, and `legacy-absence-evidence.md`.
- Current live documentation: `docs/README.md`, `docs/chat.md`, `docs/employee-runtime.md`,
  `docs/worker-types.md`, `docs/frontend.md`, `docs/systems.md`, and `docs/cli.md`.
- Current settled diff/status and `git diff --check`. The latter reported no whitespace errors.

The ledger's remaining `unproved` rows are only the explicit closeout sequence: this review and its
disposition, the no-writer freeze, the single canonical verification, final memory/result closure,
and the completed artifact set (`CLOSE-06` through `CLOSE-10`). They are not hidden product defects.

## Load-bearing seam results

1. **Official SDK, ordered ingress, and typed replay — pass.**
   `src/planner/conversation/sdk_child.py:473-548` places `session/load` and prompts behind response
   consumption epochs, while `src/planner/conversation/ordered_ingress.py:231-347` reserves raw
   `session/update` order, validates typed `SessionNotification` values, drains through one bounded
   consumer, and makes invalid input visible as a typed protocol rejection. Thought and message
   updates remain separate in `web/src/lib/acp/conversationState.ts`; the public compaction state has
   no summary surface.

2. **Binding/session/generation ownership — pass.**
   `src/planner/conversation/sqlite_binding_repository.py` performs binding replacement with
   compare-and-swap, registered-backend and Ticket-selected-backend equality, an atomic Ticket
   session mirror, and a committed-row re-read. `src/planner/conversation/employee_registry.py`
   keeps child generation separate from durable binding generation and privately loads replacements
   before publication.

3. **Turn broker, FIFO, and requested cancellation — pass.**
   `src/planner/conversation/turn_broker.py:482-671` freezes cancellation cause before settlement,
   `:1222-1279` advances the server-owned FIFO once, and `:1281-1380` completes same-binding
   requested-cancel recovery before settling Stop or starting the Send Now successor. Claude alone
   declares `requires_fresh_child_after_requested_cancel=True` at
   `src/planner/conversation/claude_backend.py:162-170`; Hermes and Codex retain the false default at
   `src/planner/conversation/hermes_backend.py:56-61` and
   `src/planner/conversation/codex_backend.py:151-159`. The focused tests and real Claude late-output
   failure/retest recorded in `computer-use-evidence.md` cover both capability branches and prove the
   successor appears exactly once without old-child output following it.

4. **Permissions and reverse-service confinement — pass.**
   `src/planner/conversation/permission_broker.py` preserves the exact request/options, admits only
   an attached current owner, marks the first valid response settling under the ownership guard,
   publishes the outcome before restoring activity, and settles detach/timeout/generation/shutdown
   causes once. `src/planner/runtime/acp_step_gateway.py:142-197` adds the Ticket/session/backend/run
   transaction guard for Automatic Employee permissions. Descriptor-backed confinement and
   symlink/race rejection are owned by
   `src/planner/conversation/reverse_services/path_confinement.py`; filesystem and terminal calls
   additionally validate exact binding and child generation in their respective service modules.

5. **Compaction topology, deadline, and privacy — pass.**
   `src/planner/conversation/configuration.py:5-10` sets the single 300-second compaction capture
   breaker. The broker carries one absolute deadline through capture and settlement. Hermes uses
   source-child fork plus fresh unpublished-child private load and durable CAS in
   `employee_registry.py`/`hermes_turn_strategy.py`; Codex and Claude observe provider lifecycle
   in-place through `codex_turn_strategy.py` and `claude_turn_strategy.py`. Public replay is limited
   to content-free started/completed/exact-failure boundaries. The qualification and Computer Use
   records honestly retain both Claude's initial private-summary replay defect and its exact
   classifier correction, and retain Codex's accepted pinned stored-plan presentation limitation
   without inventing a prose parser.

6. **Schema 25/26 ordering and failure behavior — pass.**
   `src/planner/core/db.py:187-223` runs v25 before v26. The v25 cutover is one `BEGIN IMMEDIATE`
   transaction with rollback around validation, correctness-row conversion, legacy deletion, and
   its durable marker (`:226-346`). The canonical compaction-provenance parser rejects malformed,
   non-exact, or duplicate boundary items (`src/planner/conversation/contracts.py:120-150`). The v26
   rebuild rejects a contradictory non-Hermes pre-v26 binding before rewriting, preserves all other
   Ticket bytes while assigning the authorized one-time Hermes value, and does not rewrite a valid
   post-v26 selection (`src/planner/core/db.py:392-442`). The named rollback, contradiction,
   idempotence, and parser tests in `tests/unit/test_db.py` match those claims.

7. **One backend/Worker authority and functional-worker truth — pass.**
   `src/planner/conversation/backend_catalog.py:189-197` registers exactly ordered `hermes`, `codex`,
   `claude`. `src/planner/worker_types/configuration.py:36-68` builds the Worker registry from the
   same catalog object and rejects authority drift. Ticket default/override/freeze and binding
   equality are reflected consistently in the ledger, source, live docs, and real provider records.
   `backend-qualification.md` records the exact pinned adapter identities, truthful unsupported
   reverse capabilities, Claude startup preflight, and real human -> Automatic Employee -> human
   continuity; Gemini is absent.

8. **Automatic Employee prompt and settlement boundary — pass.**
   `src/planner/runtime/acp_step_gateway.py:83-118` prepares pending worker context on the runner
   thread; `:199-287` resolves the durable selected session, completes the caller-thread binding
   handshake, submits the prepared text as the real ACP prompt, and acknowledges context revisions
   only after tracked admission. It returns only terminal status/session/error. The runner and
   `employee_step_repository.py` keep execution correctness separate from model context, settle the
   exact running row once, and prevent a lost claim or stale completion from winning. The named
   gateway tests cover pre-admission retention and acknowledgement-failure redelivery without
   treating a transcript/event/run row as delivery.

## Findings

None. Zero unresolved P0/P1 findings.

## Closeout boundary

This READY verdict completes the independent review half of `CLOSE-06`; the lead still owns the
separate `review-disposition.md`, no-writer freeze/input manifest, and the one canonical `./verify`
run. This review does not claim those later gates have already passed.
