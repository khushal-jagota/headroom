The revised plan is not yet safe to implement. Six prior findings are fully resolved, six are only partially resolved, and several new shutdown/concurrency Highs were introduced.

Source checks confirm the public spawn seam and constants, early `gateway.ready`, `id:null` parse errors, the 20-method session registry, and the nine-method denylist. The remaining blockers are implementation-plan defects, not disagreements with the settled architecture.

## Re-review of prior findings

1. High — PARTIALLY RESOLVED: early-frame / `gateway.ready` loss

The two-phase startup is genuine: transport construction does not start stdout, registration precedes `start_reading()`, and one permanent callback observes then routes frames ([plan:58](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:58), [plan:118](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:118)). This correctly accounts for Hermes emitting ready before reading stdin ([entry.py:349](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/entry.py:349)).

However, the required full-path proof is weakened: the test explicitly expects the `session.create` response to be dropped from subscriber fan-out ([plan:493](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:493)). Routing also drops unmatched id-bearing responses ([plan:170](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:170), [plan:351](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:351)). That does not prove `ready → session response → following events` all remain visible in order, as required by the prior fix and the all-child-frame routing contract ([contract:71](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:71), [contract:83](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:83)).

Fix: fan out every unmatched response, including pool responses, and make the full-pool test assert the complete ordered sequence.

2. High — RESOLVED: pool lock

The global lock now protects only brief state mutations; spawn, readiness, RPC, waiting, and teardown occur outside it ([plan:95](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:95), [plan:106](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:106), [plan:111](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:111)). Same-employee calls coalesce on `_InitSlot`; different employees proceed independently. The former recursive `_lock` acquisition is gone, and shutdown can acquire the lock promptly.

There is a separate init-slot teardown defect discussed under finding 3 and the new issues, but the lock/self-deadlock finding itself is resolved.

3. High — PARTIALLY RESOLVED: executor and teardown ownership

Resolved portions:

- `_forward` tasks are tracked and settled during route teardown ([plan:184](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:184)).
- Readiness and session waits are intended to wake on death ([plan:68](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:68), [plan:128](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:128)).
- Pool shutdown is intended to run off-loop under the shared deadline ([plan:393](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:393)).

Unresolved portions:

- `_InitSlot` contains no partial transport, so shutdown cannot kill an initialization blocked in `wait_ready` or session RPC ([plan:95](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:95), [plan:129](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:129)).
- The writer sentinel does not coordinate with an active `send`; shutdown immediately calls `close_stdin()` from another thread ([plan:65](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:65), [plan:72](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:72)). The real seam writes and flushes at [gateway.py:70](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:70) and closes the same stream at [gateway.py:82](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:82).
- The shutdown invocation shown in `server.py` cannot call the declared keyword-only API; see new issue 1.

This High remains.

4. High — RESOLVED: atomic single-fire death

`_mark_dead` is now a lock-guarded boolean test-and-set returning the winner, with only the winner invoking `on_dead` ([plan:69](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:69)). The test uses a barrier to make EOF and write failure concurrent and asserts exactly one callback ([plan:507](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:507)). This genuinely fixes the previous race.

5. High — PARTIALLY RESOLVED: lifecycle denylist and registry completeness

The runtime policy is correct. I verified exactly 20 registered `session.*` methods, including `session.cwd.set`; the plan’s inventory matches the source ([plan:311](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:311)). The six binding methods plus `handoff.request`, `cli.exec`, and `slash.exec` form the correct nine-member denylist ([plan:276](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:276)).

The escape hatches are source-supported:

- `handoff.request`: [server.py:6323](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/server.py:6323)
- `cli.exec`: [server.py:11737](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/server.py:11737)
- `slash.exec`: [server.py:13049](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/server.py:13049)

Residual Medium: the “derived” test still compares two hand-maintained constants and hardcodes 20 ([plan:313](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:313), [plan:520](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:520)). A future 21st decorator does not fail anything until someone manually refreshes the snapshot.

Fix: mechanically generate or extract the registry snapshot and couple it to a pinned Hermes revision/hash, so an upstream checkout change forces snapshot refresh.

6. High — RESOLVED: rejection wire shape

Denied native requests now receive a JSON-RPC error using the original inner id, and never reach the child ([plan:301](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:301)). `{relay:"error"}` is reserved for malformed relay envelopes ([plan:307](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:307)). Tests cover all nine methods and distinguish the two shapes ([plan:516](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:516)).

7. Medium — PARTIALLY RESOLVED: uncorrelated frames

`id:null` parse errors and id-less events now fan out and are tee-observed. Hermes really emits parse errors with `id: null` at [entry.py:362](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/entry.py:362).

Pool responses, however, are deliberately dropped from fan-out ([plan:355](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:355)). Moreover, `ChildBinding` has no pool-request-id registry ([plan:150](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:150)), so it cannot distinguish a pool response from another unmatched id-bearing response.

Fix: the simplest contract-compliant rule is to fan out and tee every unmatched response.

8. Medium — RESOLVED: failure completion ownership

Generation-death processing is the sole completer after a failed write; `_forward` emits directly only for failures before pending registration ([plan:174](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:174)). The exactly-once test is explicit ([plan:508](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:508)).

9. Medium — RESOLVED for normal wire ordering

A single FIFO writer per child plus allocation-and-enqueue under the relay lock establishes normal `[1,2,3,…]` wire order ([plan:333](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:333), [plan:337](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:337)). The test checks actual fake-child send order ([plan:486](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:486)).

The writer/shutdown race is a separate new High; it does not invalidate ordinary FIFO ordering.

10. Medium — PARTIALLY RESOLVED: race tests

The plan now includes the full pool path, concurrent EOF/write barrier, blocked-send responsiveness, receive-side teardown, and registry coverage ([plan:492](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:492), [plan:501](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:501), [plan:507](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:507), [plan:528](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:528)).

Still incomplete:

- The full-pool test expects the session response to disappear.
- The registry test does not detect an unrefreshed upstream addition.
- There is no blocked-send-plus-shutdown test.
- There is no executor-saturation shutdown test.

11. Medium — RESOLVED: structural verbatim and outbound type

The plan explicitly defines “verbatim” as structural JSON equality modulo top-level id rewriting, not byte equality, and consistently uses `Queue[str]` with one serialization boundary ([plan:371](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:371), [plan:373](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:373)). Tests use deep equality.

12. Low — PARTIALLY RESOLVED: artifacts and inventory

The unused timeout/imports are gone; `SHORT` became the named `CHILD_CLEANUP_BUDGET_SECONDS`; six implementation files are consistently named.

The test count is still contradictory:

- “eight test files” at [plan:41](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:41)
- The test section actually describes seven files at [plan:454](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:454)
- The allowlist says eight but lists seven at [plan:548](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:548)

Fix: seven test files, thirteen substantive new files, fourteen including `__init__.py`.

## New High/Medium issues introduced

1. High — the lifespan shutdown call raises `TypeError`

`EmployeeChildPool.shutdown(self, *, deadline: float)` is keyword-only ([plan:129](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:129)), but `run_in_executor` passes `deadline` positionally ([plan:432](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:432)). No pool teardown would run.

Fix: use a keyword-preserving wrapper/`functools.partial`, then test the actual lifespan hook.

2. High — an in-progress initialization cannot be shut down and can publish after closing

`_InitSlot` does not expose its partial transport. Shutdown therefore cannot perform the kill that it claims will wake `wait_ready`/RPC. Publication also does not re-check `_closing`, so an initialization can finish after shutdown and insert a new live child into `_records` ([plan:95](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:95), [plan:115](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:115), [plan:129](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:129)).

Fix: publish the partial transport/generation into the slot immediately, give shutdown direct teardown ownership, and atomically re-check `_closing` before final publication.

3. High — writer shutdown still races `send` and is not bounded

Enqueuing a sentinel does not stop a writer already blocked in `child.send`. Calling `close_stdin()` immediately from another thread races that active write and can itself block outside all later `remaining()` bounds ([plan:72](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:72), [gateway.py:70](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:70)).

Fix: make the writer the sole graceful stdin owner, including close. At deadline, the shutdown owner kills the process to break a blocked send, then performs bounded joins. Add a blocked-send-during-shutdown test.

4. High — shutdown can starve behind initialization in the default executor

Every blocking initialization and pool shutdown use `run_in_executor(None, ...)` ([plan:29](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:29), [plan:435](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:435)). Enough different-employee initializations can occupy every default-executor worker, leaving shutdown queued and unable to apply its internal deadline.

Fix: use a bounded dedicated initialization executor and a separate reserved shutdown executor/thread. Test shutdown with the initialization executor saturated.

5. High — write-side death can overtake the final stdout frame

Frames are scheduled from the stdout thread, but failed-write death is scheduled independently from the writer thread ([plan:14](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:14), [plan:37](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:37)). Death can retire the binding before a final already-emitted stdout frame is read/scheduled; that later frame is dropped.

Fix: sequence frames and death through one per-child ingress owner. A write failure should force EOF/kill, while the stdout sequencer drains final frames before emitting death. Add a final-frame-versus-failed-write barrier test.

6. Medium — death between pool resolution and pending registration is unspecified

After `child_for_employee` returns, death may remove the generation binding before `_forward` registers its pending entry ([plan:169](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:169), [plan:174](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:174)). No under-lock binding validation or direct error path is defined, risking an unhandled task and no downstream completion.

Fix: validate generation/binding/aliveness under the relay lock before registration; if absent, complete exactly once or deliberately retry.

7. Medium — blocked outputs permit unbounded memory growth

Both child and downstream queues are unbounded, while blocked sends do not stop admission ([plan:15](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:15), [plan:139](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:139)). A blocked pipe or socket can grow queued frames and pending maps indefinitely.

Fix: define bounded queues/admission limits and an overload completion policy.

## Bottom line

No—the plan is not safe to implement yet.

The architecture and several important fixes are sound, but High blockers remain around:

- complete ordered visibility of pool responses;
- the invalid lifespan shutdown call;
- teardown of in-progress initialization;
- writer/send shutdown coordination;
- default-executor shutdown starvation;
- cross-thread frame/death ordering.

Resolve those Highs and strengthen the named tests before implementation.
