# S3 implementation — Codex diff review (round 1) + orchestrator disposition

Reviewer: Codex `gpt-5.6-sol`, reasoning effort high, read-only sandbox, stdin closed. Target: the
complete S3 net diff vs `c215d6d` (all backend waves + Collision #B test ingress + frontend Wave 5
+ Playwright Wave 6), against the binding contract, the reviewed plan, and the named decisions.
Session run 2026-07-19.

The orchestrator independently verified every finding against the code on disk. Note: the pool's
settlement seam had been revised to an `expect_ack`/`observe_ack_frame` design (arm by observing
the ACK response frame in stdout emission order) — a MORE robust approach than the earlier
`_buffered_terminals`/`arm(skip)` draft; all 22 pool/adoption unit tests pass against it.

## Findings and disposition

**1. BLOCKER (Codex) — queued-disposition pre-ACK terminal drop. CONFIRMED REAL (peer round).**
Codex: a predecessor terminal arriving BEFORE the queued ACK is dropped (`fold_frame` DROPs
pre-armed frames, `employee_child_pool.py:176-178`); the ACK then arms skip=1 and the step's OWN
terminal is skipped, hanging with no whole-step timeout. The orchestrator initially contested
reachability (arm-by-ACK and fold run on one stdout reader thread in emission order; the ACK
looked synchronous). **Codex refuted this with Hermes primary source:** the queued ACK is
CONSTRUCTED under `history_lock` (`tui_gateway/server.py:5091,5098`) but WRITTEN LATER by the entry
loop (`tui_gateway/entry.py:371-373`) AFTER the lock releases; in that window the interrupted
predecessor thread reacquires `history_lock` and emits `message.complete` FIRST
(`tui_gateway/server.py:9145-9147`). So stdout can legally carry `predecessor message.complete →
queued ACK`, and DROP-pre-ACK loses the predecessor terminal → skip=1 then eats the step's own
terminal → hang. The legacy demux buffers exactly these pre-accept observations
(`minds/sessions/service.py:766-784`). The orchestrator's "ACK is synchronous" premise was WRONG
(construction is synchronous; the WRITE is deferred). **The current `expect_ack`/DROP-pre-ACK design
REGRESSED the buffering the earlier `_buffered_terminals` draft had. A real fix is required:
buffer pre-ACK terminals and, on a queued arm, consume them in order applying the skip.**

**2. BLOCKER (Codex) — flag-on employee-session-history creates a second session owner. CONFIRMED
REAL.** `GET /tickets/{id}/employee-session-history` passes `app.state.adapters.gateway` (the
routing gateway over the LEGACY worker gateway) to `read_employee_session_history`
(`tickets/api.py:624-628`); flag-on for a pool-owned ticket that reader does
`_child_or_spawn()` → `session.resume` → `_bind_live_session` on the ticket's durable
`employee_session_id` (`shared_gateway.py:253-276`) — a SECOND live owner of a pool-owned session.
The empty entity map does NOT make the ticket unroutable; `EntityRoutingGateway` sends unmapped
entities to the default worker gateway. This bypasses the crossover guard (which only covers
`ChatTurnLifecycle` HUMAN-turn entry points, not this read path) and violates contract "Ownership
handoff" (one owner per stored session) + `D-s3-collision-rulings` (3). The plan (§4.1) listed
employee-session-history as a worker-gateway consumer but wrongly treated it as harmless read-only.
**A real fix is required.**

**3. MAJOR (Codex) — rebind-failure cleanup can kill a healthy newer generation. CONFIRMED REAL
(peer round).** Codex: `_discard_child_after_rebind_persist_failure` generation-checks only
`_records.pop` (`employee_child_pool.py:587`) but unconditionally clears the live-id map and shuts
down `record.transport` (`:591-593`). The orchestrator contested reachability (the whole rebind
runs under `rebind_lock`, so N+1 couldn't publish). **Codex refuted this:** the premise is false —
`child_for_employee` does dead-child RESPAWN using only `_lock`/`_init_slots`, NEVER `rebind_lock`
(`employee_child_pool.py:286-315`, publish at `:331`, VERIFIED). Concrete race: rebind N holds
`rebind_lock` and blocks in the persist callback (up to SQLite's 5s busy timeout, `db.py:204-213`);
child N dies; a step demand respawns N+1 without `rebind_lock` and publishes it; N's callback
raises and cleanup reads the now-current N+1 record — the generation check spares its `_records`
entry but the live-id map is cleared unconditionally and N+1's transport is shut down. Result: N+1
published in `_records` with a killed transport and no live-session mapping. **A real fix is
required: generation-guard the live-id clear AND the transport shutdown, not just `_records.pop`.**

**4. MAJOR (Codex) — the interleaved-human-send behavior test is false-green. CONFIRMED REAL.**
`test_pool_step_gateway_interleaved_human_send_during_running_step`
(`tests/unit/test_pool_step_gateway.py:454-489`) submits only the STEP (one `prompt.submit`,
streaming ACK, completes) — NO second/human submission ever interleaves. It passes identically if
the interleaving-correlation logic is deleted, violating the behavior-test constraint and the
contract's explicit "interleaved human send during a running step" (§45-47, Acceptance §2). The
e2e `test_ticket_human_send_mid_step_native_queue` only observes the optimistic human row, not
correlation. **A real test that drives a genuine concurrent submission and asserts the step still
settles on its OWN terminal is required.**

Codex confirmed sound (not touched): runner-thread `on_event`, reader exception containment, no
whole-step timeout, first-create fail-closed cleanup, the `bind_pool_employee_session_id` CAS
equality assert, CLI identity, the frontend lazy chatGatewayStatus cutover, and the hard
source-scope constraints (minds/ untouched, runtime/ one annotation + Protocol, no db schema,
eligibility/discovery untouched).

## Verdict (round 1 + peer reconcile): BLOCKED — ALL FOUR findings confirmed real.

The orchestrator contested Findings 1 and 3 on reachability; Codex refuted both with Hermes
primary-source ordering (Finding 1) and a verified missing-lock respawn path (Finding 3). The
self-passing unit tests did not check what these findings check (a pre-ACK terminal ordering; a
concurrent respawn during a blocked persist). Per D-codex-loop-cap, this peer round is the one
confirming round — no further Codex rounds; drive the four fixes to green.

## The complete fix set (handed to ONE implementer, RED-first)

1. **Finding 1 — restore pre-ACK terminal buffering.** `TurnSubmission` must BUFFER terminals (and
   stream frames) observed while unarmed, and on `observe_ack_frame` arming with a queued
   disposition, consume the buffered terminals IN ORDER applying the skip, settling on the first
   OWNED one (the earlier `_buffered_terminals`/`arm` semantics, but keyed off the ACK-frame arm so
   the stdout-order guarantee still holds). A pre-ACK predecessor terminal must be skipped, never
   dropped. RED: a unit test feeding `predecessor message.complete → queued ACK → step
   message.complete` and asserting the step settles on ITS terminal (today it hangs).

2. **Finding 2 — employee-session-history must not resume+bind a pool-owned session on the legacy
   gateway.** Flag-on, route the history read for a pool-owned ticket through the POOL (which owns
   the session) instead of `app.state.adapters.gateway`. The frontend flag-on gets history from the
   neutral attach snapshot; this REST endpoint is inspection-only (tests/CLI). RED: a flag-on test
   asserting the history read does NOT cause the legacy worker gateway to own the ticket session
   (the two-owner assertion / an ownership probe stays clean).

3. **Finding 3 — generation-guard the rebind-failure cleanup.** In
   `_discard_child_after_rebind_persist_failure`, guard the `_live_session_id_by_employee` clear AND
   the transport shutdown on `record.child_generation == generation`, so a newer published
   generation is never touched. RED: a test where N+1 is published before N's persist-failure
   cleanup runs, asserting N+1's transport stays alive and its live-id mapping intact.

4. **Finding 4 — make the interleaved-human-send test real.** Drive a GENUINE second (human)
   submission concurrent with the running step and assert the step still settles on its OWN
   disposition-owned terminal, not the human turn's frames. The test must FAIL if the
   interleaving-correlation logic is removed.

## Fixes LANDED and verified (2026-07-19)

An Opus implementer landed all four fixes (it disconnected on an API error at the very end; the
orchestrator re-verified every gate and closed the two remaining gaps itself). What landed:

- **Finding 1 (BLOCKER) — FIXED.** `TurnSubmission` now BUFFERS pre-ACK terminals
  (`_pre_ack_terminals`, `employee_child_pool.py:138-234`); on `observe_ack_frame` arming, each
  buffered predecessor terminal decrements the skip (`:173-181`), so a queued predecessor that
  raced ahead of the deferred ACK write is counted, not dropped, and the step settles on its OWN
  terminal. Pre-ACK stream deltas are still not forwarded to `on_event`. New RED tests:
  `test_pool_step_gateway_queued_with_pre_ack_predecessor_terminal_settles`,
  `_pre_ack_predecessor_terminal_is_dropped`, `_queued_predecessor_deltas_not_forwarded`. Orchestrator
  spot-checked the seam: correct.
- **Finding 2 (BLOCKER) — FIXED.** `get_employee_session_history` (`tickets/api.py:632-638`) now
  rejects the read flag-on for a pool-owned (`t_*`) ticket with a `validation` error BEFORE reaching
  the legacy gateway — so no `session.resume`/`_bind_live_session` second-owner. The neutral pane
  renders history from the durable session via attach; this inspection endpoint has no flag-on web
  consumer, so a hard reject (not a re-route) is the minimal correct fix (not a silent empty).
  Orchestrator added the RED behavior test
  `test_chat_seed.py::test_employee_session_history_pool_owned_ticket_flag_on_rejected` (a spy
  gateway that raises if the legacy read runs; asserts 400 + spy-untouched + durable binding
  intact) and PROVED it dies when the reject is removed (returns 503, not 400).
- **Finding 3 (MAJOR) — FIXED.** `_discard_child_after_rebind_persist_failure` now
  generation-guards BOTH the live-id clear AND the transport shutdown (shuts down generation N's OWN
  transport, passed in, not the current record's) so a published newer generation N+1 is untouched.
  New RED test `test_hermes_backend_new_conversation.py::test_rebind_persist_failure_cleanup_spares_newer_generation`
  models the exact race and dies on the unfixed cleanup. Orchestrator confirmed the missing-lock
  respawn path.
- **Finding 4 (MAJOR) — FIXED.** `test_pool_step_gateway_interleaved_human_send_during_running_step`
  now drives a full pre-ACK human turn (start/delta/complete carrying "HUMAN" text) via
  `events_before`, then the step's turn via `events_after`, and asserts the step settles on ITS
  terminal AND `on_event` never contains the human turn's text. Dies if the mis-owning logic returns.

Beyond the four, the implementer also strengthened `test_ticket_step_settles_on_failed` to assert
the ticket reaches `errored` (the runner settled, not just the relay rendered) and updated
`docs/{chat,employee-runtime,systems}.md` for the flag-on step path + native-concurrency human
sends (CLAUDE.md "docs kept current"). Orchestrator fixed 2 leftover ruff E501 lines.

## Gates after fixes (orchestrator-run)
- `.venv/bin/ruff check .` — clean.
- All S3 + affected unit suites (pool step gateway, adoption, ticket composition, ticket step
  composition, CLI identity, chief composition, new conversation, human chat, chat_seed, employee
  session history, employee step runner, chat ingress contract) — 200 pass.
- Flag-on e2e `test_ticket_neutral_pane.py` — 6/6 (rebuilt web/dist).
- One NON-REPRODUCIBLE flake observed once in
  `test_chat_seed.py::test_continue_failed_turn_reuses_bound_session_without_replaying_prompt` (a
  fake-gateway background-turn timing sensitivity, unrelated to S3 code); passes on isolation and in
  3 consecutive full-combo re-runs. The orchestrator's Finding-2 flag-on test leaks NO threads
  (verified: 1 thread before/after). Flagged to main; the canonical `./verify` (parent-owned) is the
  arbiter.

## Codex confirming round (D-codex-loop-cap — the one confirming round) + Finding 4 closure

Codex (resumed session) confirmed Findings 1, 2, 3 CORRECT+COMPLETE with cites. On Finding 4 it
raised a CONCRETE HOLE: the `interleaved_human_send` test used a `streaming` ACK +
`events_before` synthetic predecessor frames, so it tested pre-ACK FILTERING (Finding 1's concern),
not a genuine QUEUED second submission — it would pass even if the queued disposition/skip
correlation were removed. VALID critique.

**Closed by the orchestrator (test-only):** rewrote
`test_pool_step_gateway_interleaved_human_send_during_running_step` to drive a genuine `queued` ACK
(a human turn IS running → the step queues), with the interrupted human turn's terminal FIRST then
the step's own, asserting the step settles on ITS terminal (`step`, not the human's `HUMAN`) and no
predecessor frame leaks to `on_event`. RED-proven: with `_SKIP_BY_DISPOSITION["queued"]` forced to 0
the step wrongly settles on the human's `interrupted` terminal → the test FAILS. This now uniquely
exercises the contract's native-queue interleaving (§45-47), distinct from the pre-ACK-filtering
tests and from `test_pool_step_gateway_queued_disposition_skips_predecessor_terminal` (which already
covered the queued skip in isolation). The cap is reached (one review + one confirming round); the
Finding 4 closure is verified by the break-the-behavior RED check, not a further Codex round.

## FINAL VERDICT: all four Codex findings resolved and verified. S3 implementation clean.

## Confirming round (2026-07-19, run by the main orchestrator over the hash-fenced tree)

Verdict: F1-F6 CLOSED with file:line evidence; ZERO new findings; scope confirmed (no
minds/ changes; runtime = annotation + type-only step_gateway; eligibility untouched;
flag-off exactly today). Blocked solely on F7: five stale doc spots
(employee-runtime.md x2, chat.md x2, systems.md x1) still describing flag-off behavior
as absolute. All five corrected inline by the main orchestrator immediately after the
verdict (flag-scoped with retirement triggers, incl. the pool-owned history-route
rejection carve-outs). Independent evidence on record: 400x streaming-pre-ACK stress
(0 failures), mutation-kill on the queued-pre-ACK unit tests, and a full
disposition x pre-ACK-count case-matrix trace incl. the id-match false-arm guard.
Accepted trigger-bound limitation: the composition-level interleaved tests survive the
buffering mutation (unit tests are the honest guard); genuine concurrent-send
composition coverage is owed when the legacy path retires.
