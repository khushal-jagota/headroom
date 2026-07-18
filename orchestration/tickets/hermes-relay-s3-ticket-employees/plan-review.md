# S3 plan — Codex plan review (round 1) + orchestrator disposition

Reviewer: Codex `gpt-5.6-sol`, reasoning effort high, read-only sandbox, stdin closed. Target:
`implementation-plan.md` (first draft) against the binding contract, the S2b predecessor
(plan + Codex review), the consumed runtime/composition/CLI/tickets/hermes_backend code, and the
named decisions. Session run 2026-07-18.

The orchestrator independently verified EVERY finding against the code before acting. All ten are
correct as stated; none refuted. The plan was revised in place; this file records each finding and
its disposition. Per `D-codex-loop-cap`: one round + at most one confirming round. **No confirming
round is run** — the reason is stated at the end (the material questions became owner-decisions, not
code-correctness questions a second Codex round adjudicates).

## Findings and disposition

**1. BLOCKER — the step-settlement observer cannot identify "its submitted turn."** CONFIRMED with
primary evidence. The first draft's "at-most-one-in-flight-turn per employee" invariant is FALSE: a
neutral-pane human send injects native frames with NO `chat_turns` row
(`neutral_downstream_session.py:86-113`), so the eligibility active-Chat factor
(`automatic_employee_step_eligibility.py:116-121`) does not see it and a step can be claimed while a
human Hermes turn is running. The `prompt.submit` ACK reports a disposition
(`sessions/service.py:531-572`); terminals are idless fan-out (`employee_child_relay.py:367-411`);
`SharedGateway` owns its terminal via a registered consequence + disposition, NOT the next
terminal (`shared_gateway.py:819-861`). A `queued` step would wrongly settle on the predecessor's
terminal — the exact `D-owned-turn-identity` hazard (`decisions.md:513-525`).
**Disposition: §1 fully rewritten.** The unsound design is removed; §1.2 states the proof; §1.3
presents the settlement as an OPEN FORK (Collision #A) with the recommendation being **Option A1 —
own the terminal from the ACK disposition** (steered → errored no-terminal; streaming → own current;
queued → skip the predecessor's terminal, own next), reconstructing the disposition half of owned-
turn identity on the pool path without the demux. Flagged for main's ruling.

**2. BLOCKER — the observer invokes runner callbacks on the child stdout thread.** CONFIRMED.
`RawFrameChildTransport` runs `on_frame` on its stdout-reader thread
(`raw_frame_transport.py:127-148`); the runner's `on_event` uses the runner's thread-affine SQLite
connection (`employee_step_runner.py:425,577-584`; `db.py:204-214`; the tee warns cross-thread use
raises, `transcript_mirror_tee.py:7-15`). **Disposition: §1.4 corrected.** The stdout-thread sink
now does the MINIMUM (fold in-memory settlement state + enqueue streamed frames under a lock, swallow
exceptions); `run_ticket_step` drains the queue and calls `on_event` on the RUNNER thread, so the
SQLite connection is never touched off-thread. Added as a hard constraint.

**3. BLOCKER — flag-on composition has no complete replacement for the worker gateway.** CONFIRMED
and broader than the first draft's "day chat?" framing. Verified consumers of the worker gateway:
day chat (`chat/service.py:64-101`, `chat/api.py:52-75`, `EntityRoutingGateway` default
`shared_gateway.py:66-84`), `/api/chat/commands` + entity status (`chat/api.py:127-146`), ticket
employee-session-history (`tickets/api.py:578-593`); the pre-startup adapter is an offline
placeholder (`core/adapters/real.py:27-56`). **Disposition: §4.1 rewritten, §2.2 + §4.2 corrected.**
Recommendation: **reading (a) — the worker gateway STAYS running flag-on to serve the non-ticket
surfaces, but holds NO ticket/Chief session** (the crossover guard + empty entity map keep pool-owned
entities off it). "Never started" reads as "never OWNS a ticket employee session." `start_background_loops`
KEEPS `shared_gateway` and gains `step_gateway`; the assertion becomes the empty-entity-map +
pool-step-gateway form. Flagged for main's ruling (confirm (a) or rule the wider cutover in-scope).

**4. MAJOR — the ticket route repeats S2B-ROUTE-001.** CONFIRMED. `TicketRoute` eagerly opens
`chatGatewayStatus` (`TicketRoute.svelte:27-35` → `/api/chat/{ticket}/status`), a legacy-gateway
surface, which S2b required lazy-in-disabled-branch-only for the Chief
(`codex-implementation-review.md:49-52`). **Disposition: §7.1 amended** — the swap must make
`chatGatewayStatus` disabled-branch-only + dispose otherwise.

**5. BLOCKER — rebind persistence is not fail-closed; the CAS lacks an expected binding.**
CONFIRMED. First-create IS guarded (`employee_child_pool.py:270-301`); REBIND is NOT
(`employee_child_pool.py:382-414`) — a failed persist leaves the child alive with an unpublished
fresh session and the old key on a closed session. The canonical CAS needs expected→candidate and
can silently RETAIN on mismatch (`tickets/data.py:342-376`); the runner guards itself with a
post-write equality check (`employee_step_runner.py:551-568`). **Disposition: §3.2 corrected.** The
new writer takes `expected_stored_session_id` + `candidate_stored_session_id` and asserts the
returned binding equals the candidate (fail-closed); the `on_stored_session_bound` callback widens to
carry both ids; `rebind_fresh_session` gains a fail-closed teardown. Recorded as a real S2b-adjacent
defect S3 fixes, not inherits.

**6. BLOCKER — Collision #1 is real, but its fallback is not contract-compliant.** CONFIRMED. The
legacy worker child has no `PLAN_TICKET_ID` (`server.py:99-106`, `shared_gateway.py:648-653`);
identity rides per-turn Hermes env (`cli/main.py:1313-1326`, `decisions.md:455-463`). The
read-`PLAN_TICKET_ID`-first-fall-back-to-Hermes fallback KEEPS the Hermes-env read for flag-off,
which contradicts the contract's Acceptance §3 "Hermes-env reads absent from the worker path (both
compositions)" (`contract.md:47-56,83-84`). **Disposition: §6.2 sharpened.** The fallback NEEDS AN
EXPLICIT OWNER OVERRIDE; the plan does not proceed on it without one. Main must rule (authorize the
flag-off fallback as an override until S3b, accept flag-off broken until S3b, or another resolution).

**7. MAJOR — `PoolStepGateway.interrupt` is not implementable from the planned surface.** CONFIRMED.
The runner calls `interrupt(session_key, ticket_id, deadline=<absolute>)`
(`employee_step_runner.py:307-318`); native `session.interrupt` needs the LIVE id
(`hermes_frame_translation.py:263-266`); the pool record exposes only the stored id
(`employee_child_pool.py:52-59`); the pool's live map + request method are private + relative-timeout
(`employee_child_pool.py:120-123,429-468`). **Disposition: §1.6 added.** The pool gains
`interrupt_live_turn(employee_entity_id, *, deadline)` resolving the current live id from
`_live_session_id_by_employee` and converting the absolute deadline to the remaining relative
timeout; `PoolStepGateway.interrupt` delegates to it; no live id → no-op.

**8. MAJOR — the required Playwright "automatic step" cannot run under the planned test composition.**
CONFIRMED. Relay test mode composes only pool+relay (no loops/runner — `server.py:216-244`);
`TestModeAcceptingEmployeeRevisionRunner` does no Hermes work (`testmode.py:45-47`); the test router
exposes only `set-now` (`testmode.py:77-99`). **Disposition: §9.5 added Collision #B.** Recommendation
**(a) — compose the real runner + `PoolStepGateway` in relay test mode and add a `test_mode`-gated
step-trigger route** (the same "add test machinery so Playwright drives the real path against scripted
frames" S2b established). Flagged for main's confirmation that the test machinery is in-scope.

**9. MAJOR — the bounded step timeout is unrequested.** CONFIRMED. The legacy path drains until a
terminal with no whole-step timeout (`shared_gateway.py:862-900`); the contract requires settlement
"exactly as today." **Disposition: §1.7 — the timeout is REMOVED.** Settlement is by lifecycle
terminal or child death only; the per-RPC ACK timeout stays (it is not a whole-step cap). Added as a
hard constraint.

**10. MINOR — first-draft Collisions #2 and #5 are not genuine.** CONFIRMED. #2 (runner gateway
typing) is ordinary authorized typing — the contract explicitly changes the injected transport; the
plan's own allowlist manufactured the "collision." #5 (settle-not-resume reading) is already resolved
by the existing split (`server.py:157-178` excludes `agent_running_step`; steps resume,
`decisions.md:426-433`). **Disposition: both WITHDRAWN** (§2.3, §4.3, §12 tail). The Protocol lives
in a `runtime/`-local module so `minds/` is untouched entirely.

**Codex confirmed sound (not touched):** B (the `on_event` `{type,session_id,payload}` shape matches
`observe_worker_gateway_event`, `shared_gateway.py:868-873` ↔ `chat/service.py:689-725`); F as code
preservation (eligibility/discovery/claim genuinely untouched — but that preservation does NOT make
the active-Chat factor a turn-correlation proof, per finding 1); Collisions #1/#3/#4 are genuine.

## Why no confirming Codex round (D-codex-loop-cap)

The cap allows one confirming round "when the first drove material fixes." It did. But the confirming
round would not add value here, and the discipline says the cap bounds rounds, not fixing:

- The mechanical findings (2, 4, 5, 7, 9) are closed by concrete plan corrections a second Codex read
  would merely re-confirm — the fixes are direct and evidence-cited.
- The two false collisions (#2, #5) are withdrawn on Codex's own reasoning.
- The FOUR remaining open items (#A settlement, #B test ingress, #1 CLI override, #4 worker-gateway
  reading) are now **owner decisions**, not code-correctness questions Codex can adjudicate. Codex
  already gave its read on each; a second round cannot rule scope or override the contract. These go
  to main.

Per `D-s1-concurrency-scope`/`D-codex-loop-cap`, beyond-contract robustness edges do not earn further
rounds; genuine contract collisions go to the owner. That is the state here. The plan proceeds to
main with the four open rulings; implementation remains gated on S2b integration AND these rulings.
