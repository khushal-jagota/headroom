## Findings

1. **High — relay shutdown is not bounded by the shared deadline**

   - **Plan:** [implementation-plan.md:521](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:521), lines 524–529 cancel and await `hermes_relay_task` without a timeout before shutting down the child.
   - **Violates:** [contract.md:24](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:24): the one `shutdown_grace_seconds` budget must govern all new components.
   - **Why:** Cancellation can stall in an upstream `recv`, downstream send, adapter close, or observer. The supervisor may receive the deadline only after it has expired.
   - **Fix:** Give the relay a deadline-aware shutdown that closes its upstream socket and bounds task completion by `max(0, deadline - monotonic())`; then pass that same deadline to the supervisor.

2. **High — readiness timeout can orphan the spawned child**

   - **Plan:** [implementation-plan.md:106](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:106), lines 106–117 spawn before `wait_ready`; [implementation-plan.md:494](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:494), lines 494–509 catch readiness failure and replace the supervisor with `None`.
   - **Violates:** [contract.md:39](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:39), especially ownership and bounded failure/shutdown at lines 39–42.
   - **Why:** On timeout, the child may still be alive. Clearing the only supervisor reference prevents lifespan shutdown from terminating it.
   - **Fix:** On any post-spawn readiness failure, retain the supervisor and terminate its child within a bounded cleanup deadline before reporting unavailable.

3. **High — reconnect permanently strands downstreams, and connect failures are not fully specified**

   - **Plan:** [implementation-plan.md:448](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:448), lines 448–453 end `run()` after six failures while downstreams stay connected; lines 455–462 only define the `ConnectionClosed` path, not exceptions thrown by `connect_upstream`.
   - **Violates:** [contract.md:52](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:52): the relay holds an upstream connection with bounded reconnect/backoff; [contract.md:81](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:81): subsequent requests must flow.
   - **Why:** After attempt six, the route still considers `app.state.hermes_relay` available, but it can never recover without restarting Panels. “Requests get no upstream” is not a defined calm retry state.
   - **Fix:** Prefer retrying until shutdown with a capped delay. If finite attempts are owner-required, explicitly mark the route unavailable and close downstreams with 1013 rather than leaving live, inert connections. Define handling and counting for both connection failures and established-socket drops.

4. **High — requests can race initial connection and reconnect without a defined upstream state**

   - **Plan:** [implementation-plan.md:510](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:510), lines 510–515 publish the relay before `run()` establishes upstream; [implementation-plan.md:349](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:349), lines 349–363 insert mappings before sending, with no send-failure rollback.
   - **Violates:** [contract.md:52](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:52) and the reconnect acceptance at lines 81–82.
   - **Why:** A downstream can send while `_upstream` is absent, stale, or closed. A failed send leaves an orphan mapping unless a later reset happens.
   - **Fix:** Specify an explicit upstream-availability/generation state. Reject or defer sends while disconnected, and remove exactly the newly allocated mapping if the upstream send fails. Add coverage for sends during initial connection and backoff.

5. **High — dropping every downstream frame without `id` is not a transparent native JSON-RPC relay**

   - **Plan:** [implementation-plan.md:323](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:323), lines 323–327 drop non-control frames without `id`.
   - **Violates:** [contract.md:5](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:5) (“forwards native frames verbatim”) and [contract.md:66](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:66) (interpret/rewrite only ids and routing metadata).
   - **Why:** An id-less JSON-RPC notification is valid traffic. The actual upstream passes parsed id-less requests into dispatch; `id` is optional at [server.py:1228](/Users/khushaljagota/.hermes/hermes-agent/tui_gateway/server.py:1228).
   - **Fix:** Forward every non-relay-control JSON-RPC frame. Namespace only frames with a correlatable request id; drop any resulting `id: null` response as uncorrelatable.

6. **Medium — send-lock discipline is self-contradictory, and the acceptance test is not concretely concurrent**

   - **Plan:** [implementation-plan.md:356](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:356), lines 356–363 first claim natural serialization, then reverse course and require `_upstream_send_lock`; [implementation-plan.md:588](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:588), lines 588–604 describe interleaved direct calls but not simultaneous tasks or a forced scheduling interleave.
   - **Violates:** [contract.md:58](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:58) and the explicitly concurrent test requirement at lines 76–78.
   - **Fix:** State one normative rule: every upstream write is under `_upstream_send_lock`, separate from `_id_lock`. Exercise it with `asyncio.gather` and a barrier/blocking fake send that forces interleaving between two downstream handlers.

7. **Medium — the reverse id index neither provides the claimed complexity nor always remains bijective**

   - **Plan:** [implementation-plan.md:343](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:343), lines 343–347 claim O(k) cleanup; [implementation-plan.md:387](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:387), lines 387–390 still scan every reverse-map key.
   - **Violates:** [CLAUDE.md:26](/Users/khushaljagota/.hermes/planning-v2/CLAUDE.md:26), “everything earns its existence.”
   - **Why:** Cleanup remains O(n). Also, two pending requests from the same downstream with the same original id overwrite the reverse entry while leaving two forward entries, contradicting “both tables never disagree.”
   - **Fix:** Either remove the reverse map and scan the authoritative forward map, or use an actual per-downstream index such as `downstream_id -> set[upstream_id]`.

8. **Medium — fan-out is not safe against downstream mutation or send failure**

   - **Plan:** [implementation-plan.md:393](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:393), lines 393–399 await each downstream send while claiming the single coroutine alone establishes safety.
   - **Violates:** [contract.md:10](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:10), which requires many downstream connections, and routing at lines 61–63.
   - **Why:** During an awaited send, another coroutine can unregister a downstream. Iterating a live registry can then fail due to mutation. One downstream send exception can also abort the sole upstream reader and affect every client.
   - **Fix:** Snapshot recipients before awaiting, preserve order within that snapshot, and isolate/unregister failed downstream sends without treating them as an upstream failure.

9. **Medium — the config-off acceptance test can pass for the wrong reason**

   - **Plan:** [implementation-plan.md:582](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:582), lines 582–584 allow testing through `create_app` in test mode; [implementation-plan.md:651](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:651), lines 651–655 leave the composition helper optional.
   - **Violates:** acceptance area 1 at [contract.md:73](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:73).
   - **Why:** Test mode independently suppresses all spawning, so it does not prove that `hermes_backend_enabled=False` is the operative gate.
   - **Fix:** Specify one injectable composition test that holds `test_mode=False`, varies only the enable flag, and proves the fake spawn is called only when enabled.

10. **Medium — payload opacity is asserted but not covered**

   - **Plan:** [implementation-plan.md:354](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:354) promises only id changes; [implementation-plan.md:606](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:606), lines 606–618 contain no opaque-payload preservation case.
   - **Violates:** [contract.md:66](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:66).
   - **Fix:** Add frames with nested, unknown payload fields in requests, responses, and events; assert semantic identity except for the top-level request id. Frames requiring no id rewrite should retain the original raw frame for forwarding.

11. **Low — public spawn environment attributes are redundant and expose the generated token**

   - **Plan:** [implementation-plan.md:112](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:112), lines 112–113 retain argv/env publicly for tests, while the fake spawn already captures them at lines 570–575.
   - **Violates:** [CLAUDE.md:26](/Users/khushaljagota/.hermes/planning-v2/CLAUDE.md:26).
   - **Fix:** Remove the public attributes and assert through the injected fake spawn. Do not retain the dashboard token and full process environment on a public object merely for testing.

## Defensible choices

- Off-by-default production configuration, test-mode suppression, the file allowlist, and test-only tee consumers match the contract.
- Clearing both id tables and resetting the allocator after a successful reconnect is correct. Skipping the reset event on the first successful connection is also correct.
- Disconnect cleanup is correctly scoped by `downstream_id`, subject to fixing the reverse-index structure.
- A single upstream reader can preserve per-downstream upstream-frame order, once recipient snapshots and send-failure isolation are specified.
- `/api/relay` has the same middleware-only authentication posture as `/api/events`; `TrustedIngressMiddleware` covers both HTTP and WebSocket scopes.
- The import claims are accurate: `asyncio` is absent, and the `contextlib` module is absent even though `asynccontextmanager` is imported directly.
- No worker-chat-boundary violation is proposed.

## Bottom line

The plan is **not safe to implement as-is**. The High findings—unbounded shutdown, orphaned child on readiness timeout, permanent reconnect abandonment, undefined disconnected-send behavior, and dropping native id-less JSON-RPC traffic—must be resolved before implementation.
