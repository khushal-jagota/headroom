## Findings

1. **High — Early child frames, including `gateway.ready`, can be lost.**

   - **Plan:** [implementation-plan.md:44](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:44), [62](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:62), [82](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:82).
   - **Contract:** [contract.md:71](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:71), [83](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:83).
   - `RawFrameChildTransport.__init__` starts its reader before `relay.register_child`. Hermes emits `gateway.ready` before reading stdin ([entry.py:349](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/entry.py:349)). The callback can therefore run before the relay binding exists and be dropped. Separately, the “responder first, relay after binding” callback swap explicitly consumes early frames without routing them. Frames emitted immediately after the session response can also race ahead of the swap.
   - **Fix:** Make transport startup two-phase or buffer its ordered ingress until registration. Register the generation before starting stdout consumption, then have one permanent callback both satisfy the pool responder and route every frame. Do not swap callbacks or consume pool responses. Add a full `pool → transport → relay` test proving `gateway.ready`, session-binding response, and immediately-following events remain ordered and visible.

2. **High — The pool’s lock design self-deadlocks and cannot honor shutdown deadlines.**

   - **Plan:** [implementation-plan.md:78](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:78), [81](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:81), [82](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:82), [86](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:86).
   - **Contract:** [contract.md:52](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:52), [60](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:60), [107](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:107).
   - `child_for_employee` runs `_spawn_and_bind` “under `_lock`”, while `_spawn_and_bind` mints the generation “under `_lock`”. A non-reentrant `threading.Lock` deadlocks immediately. The same outer lock is held across stale-child shutdown, spawn, readiness, and session RPC. Consequently `shutdown()` can block indefinitely merely acquiring `_lock`, before it can apply the shared deadline.
   - **Fix:** Never hold the global pool lock across spawn, wait, RPC, or shutdown. Use a per-employee initialization slot/latch so concurrent callers share one spawn attempt while unrelated employees proceed independently. Publish or discard the completed record under the short global lock. Shutdown must be able to mark the pool closing and snapshot active/in-progress records without an unbounded lock wait.

3. **High — Outstanding executor work is neither tracked nor safely coordinated with teardown.**

   - **Plan:** [implementation-plan.md:25](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:25), [127](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:127), [139](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:139), [273](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:273), [330](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:330).
   - **Contract:** [contract.md:29](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:29), [60](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:60).
   - The route does not define an in-flight `_forward` task registry, despite claiming it waits for executor work. Cancelling an asyncio future returned by `run_in_executor` does not stop its underlying thread. `transport.shutdown` also closes/kills the child without coordinating with its send lock. A session responder is not specified to wake on child death, so its executor thread may wait the full request timeout. Finally, `pool.shutdown(...)` is called synchronously from the async lifespan, putting its blocking `wait()` and thread joins on the event loop.
   - **Fix:** Track every forward/spawn operation; stop admitting new work before teardown; wake pool responders on death; coordinate shutdown with active sends; and await pool shutdown off-loop using the same absolute deadline. Define what happens when the deadline expires while an executor thread is still active—timing out the asyncio await alone is insufficient.

4. **High — `_signal_dead` is not actually single-fire.**

   - **Plan:** [implementation-plan.md:27](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:27), [49](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:49).
   - **Contract:** [contract.md:77](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:77), [115](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:115).
   - `if event.is_set(): return; event.set()` is not an atomic test-and-set. The stdout thread and a failed-write executor thread can both observe false and both invoke `on_dead`.
   - **Fix:** Guard the dead transition with a dedicated lock and boolean, returning whether the caller won the transition. The test must synchronize EOF and failed write concurrently with a barrier, not merely trigger them sequentially.

5. **High — The lifecycle registry audit is incorrect and the denylist is under-guarded.**

   - **Plan:** [implementation-plan.md:206](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:206), [219](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:219), [221](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:221), [464](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:464).
   - **Contract:** [contract.md:52](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:52), [89](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:89).
   - The installed registry contains **20**, not 19, `session.*` methods. The six listed direct `session.*` lifecycle methods are reasonable, but other registered methods bypass them:
     - `handoff.request` explicitly causes another gateway process to rebind the stored session ([server.py:6323](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/server.py:6323)).
     - `cli.exec` permits arbitrary noninteractive Hermes CLI commands ([server.py:11721](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/server.py:11721)), including `sessions delete` ([main.py:13648](/Users/khushaljagota/.hermes/hermes-agent/hermes_cli/main.py:13648)) and commands that mint sessions.
     - `slash.exec` passes arbitrary commands to a resumed `HermesCLI` worker ([server.py:13122](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/server.py:13122)); `/new` and `/resume` are lifecycle commands in that worker.
   - **Fix:** Correct the registry inventory and add every method-level lifecycle escape hatch to the denylist—at minimum `handoff.request`, `cli.exec`, and `slash.exec` unless a narrower, contract-approved payload inspection policy is introduced. The denylist approach remains defensible for transparency, but only if generic escape methods are denied wholesale.

6. **High — Lifecycle rejection uses the wrong wire shape.**

   - **Plan:** [implementation-plan.md:219](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:219), [405](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:405).
   - **Contract:** [contract.md:90](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:90), [120](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:120).
   - The contract requires a JSON-RPC error response. The plan instead returns `{relay:"error", ...}`. A native JSON-RPC client waiting on the original inner id will not recognize that as completion.
   - **Fix:** Return `{"jsonrpc":"2.0","id":<original inner id>,"error":{...}}`. Relay-control errors remain appropriate for malformed relay envelopes, but not for a rejected native request.

7. **Medium — Uncorrelated child frames have no routing path.**

   - **Plan:** [implementation-plan.md:128](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:128), [239](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:239).
   - **Contract:** [contract.md:71](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:71), [83](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:83).
   - The plan handles mapped responses and id-less notifications only. Hermes can emit parse-error responses with `"id": null` ([entry.py:362](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/entry.py:362)); pool-issued session responses also have no downstream mapping. The plan does not say where either goes, despite requiring every child frame to remain employee-scoped and observable.
   - **Fix:** Define unmatched responses—including `id:null`—as employee-scoped frames: tee and fan them out in ingress order. A pool responder may observe a matching frame but must not consume it.

8. **Medium — Failed writes can complete one downstream request twice.**

   - **Plan:** [implementation-plan.md:127](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:127), [129](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:129).
   - A failed `send_frame` causes `_forward` to emit an error while `_signal_dead` schedules `deliver_child_death`, which finds the still-registered pending entry and emits a second error.
   - **Fix:** Give one path ownership of failure completion. Either atomically remove the pending entry before `_forward` emits, or let generation death processing emit the sole error.

9. **Medium — The claimed strictly increasing child wire order is not provided by executor sends.**

   - **Plan:** [implementation-plan.md:23](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:23), [225](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:225), [380](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:380).
   - IDs can be allocated as 2 then 3 while two default-executor workers acquire the send lock in the reverse order. The lock prevents partial-line interleaving; it does not preserve allocation/submission order.
   - **Fix:** If wire order must be `[1,2,3]`, use one per-child outbound writer queue. Otherwise weaken the test and wording to require one collision-free per-child id namespace, which is all correlation needs.

10. **Medium — Several tests still bypass the races they claim to prove.**

   - **Plan:** [implementation-plan.md:386](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:386), [391](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:391), [397](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:397), [407](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:407), [413](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:413).
   - The ready test skips `EmployeeChildPool`, where the callback handoff bug lives. Death dedup does not require concurrent EOF/write failure. Loop responsiveness covers blocked readiness/session work but not a blocked raw pipe write. Writer-side teardown is tested, but route teardown from receive-side disconnect is not. The registry test compares constants to another literal, so it cannot catch the current 20-vs-19 error or a future upstream addition.
   - **Fix:** Exercise the full pool composition for ready/order; use barriers for the death race; add a blocked-`send` responsiveness test; add receive-side route teardown; and derive or explicitly maintain a checked registry snapshot rather than comparing two local literals.
   - The requested two-child disconnect, all-and-only dead-child cancellation, id reuse across employees, re-entrant subscription mutation, real writer failure, and mixed emission-order cases are otherwise covered.

11. **Medium — “Byte-identical” and the outbound queue type are internally inconsistent.**

   - **Plan:** [implementation-plan.md:48](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:48), [98](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:98), [128](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:128).
   - `json.loads` followed by `json.dumps` preserves JSON structure and unknown fields, but not bytes, whitespace, escaping, or lexical key representation. Also `outbound` is declared `Queue[str]`, while routing prose and tests enqueue dictionaries.
   - **Fix:** Pin one serialization boundary and type it consistently. If “verbatim” means structural JSON equality modulo top-level id—as the proposed design supports—state and test that explicitly. Literal byte preservation would require carrying the original inner serialized text, which the object envelope currently does not provide.

12. **Low — Some unearned or contradictory plan artifacts remain.**

   - **Plan:** [implementation-plan.md:31](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:31), [37](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:37), [81](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:81), [117](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:117), [288](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:288).
   - **Rule:** [CLAUDE.md:24](/Users/khushaljagota/.hermes/planning-v2/CLAUDE.md:24).
   - The layout says five files but later adds a sixth. `EmployeeChildRelay.request_timeout` is never consumed. `SHUTDOWN_GRACE_DEFAULT` is imported but the plan instead uses undefined `SHORT`. The proposed direct `EmployeeChildPool`/`EmployeeChildRelay` imports in `server.py` are unused if composition is delegated to `composition.py`.
   - **Fix:** Remove unused parameters/imports, name the actual operational cleanup budget, and make the file inventory consistent. The previously criticized `live_session_id`, `available()`, and `employee_bucket_marker` are correctly gone.

## Defensible choices

- `RawFrameChildTransport` is buildable solely on the public `ChildProcess`/`SpawnFn` seam at [gateway.py:43](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:43) and [101](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:101). No `GatewayChild` internals or `minds/` edits are needed.
- One stdout reader plus a separately drained stderr pipe is the correct transport shape. Once initialization is fixed, it preserves child emission order and does not swallow `gateway.ready`.
- The envelope discriminator, per-employee subscriptions, generation-keyed correlation, recipient snapshots, and per-connection writer tasks are sound.
- A method denylist is consistent with a transparent relay; the problem is the incomplete inventory, not the denylist decision itself. Blocking `session.activate` is contract-mandated even though its current implementation only focuses an already-live session.
- Default-off composition, the existing `elif not config.test_mode` gate, one shutdown deadline, middleware-only WebSocket authentication, and 1013 when unavailable are correct. `asyncio` is genuinely a new `server.py` import.
- The pool’s `session.create`/`session.resume` parameter shapes match [shared_gateway.py:670](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:670) and [691](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:691).
- The absence of `hermes serve`, upstream WebSockets, and reconnect machinery is correct.

## Bottom line

**The plan is not safe to implement as-is.** High findings 1–6 must be resolved first: ordered initialization without frame loss, nonblocking pool coordination, real executor/shutdown ownership, atomic death deduplication, a complete lifecycle guard, and JSON-RPC-shaped lifecycle rejection. The remaining Medium findings should be incorporated into that revision because several directly affect the acceptance tests’ ability to prove the High fixes.
