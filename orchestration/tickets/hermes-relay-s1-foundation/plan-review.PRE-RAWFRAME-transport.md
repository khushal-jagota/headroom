## Findings

1. **High — Native request forwarding cannot work through the stated `GatewayChild` API.**  
   **Plan:** [§3 lines 150–158](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:150), [§4 lines 164–166](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:164), [§5 line 194](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:194).  
   **Contract:** [lines 14–17](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:14), [78–79](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:78), plus the no-`minds/`-changes rule at [23–25](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:23).  
   `GatewayChild.begin_request(method, params)` reconstructs a new frame and allocates its ID; it does not accept an opaque native frame ([gateway.py:335](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:335)). Unknown top-level fields are lost, and there is no public raw/id-less notification send. This directly contradicts “forward the inner frame verbatim” and “never reads method/params.”  
   **Fix:** Specify a concrete in-scope raw-frame adapter—potentially a `ChildProcess`/`SpawnFn` wrapper that preserves exact stdin frames and serializes raw sends—or obtain an owner-approved `GatewayChild` API/contract change. Add end-to-end tests for unknown top-level request members and an id-less notification through the real `GatewayChild` path.

2. **High — The response path assumes `handle.wait()` returns a full response frame; it does not.**  
   **Plan:** [lines 19](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:19), [166–178](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:166), [184](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:184), [316](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:316).  
   **Contract:** [lines 68–79](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:68).  
   `GatewayRequestHandle.wait()` returns only the `result` object and raises `GatewayRpcError` for error responses ([gateway.py:139](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:139)). The plan therefore cannot recover the child ID, preserve unknown top-level response/error fields, tee the original response, or rewrite only its ID. The synthetic JSON-RPC errors proposed at lines 89/178/210 also invent payload semantics not authorized by the contract.  
   **Fix:** The raw-frame adapter/API resolution must expose complete response frames correlated to handle identity. Test full success and error-frame opacity, including error `data` and unknown top-level fields. Remove synthetic error schemas unless added to the binding contract.

3. **High — `gateway.ready` never reaches `next_process_event()`.**  
   **Plan:** [lines 13–17](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:13), [182](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:182), [test line 314](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:314).  
   **Contract:** [lines 64–67](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:64), acceptance [92–94](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:92).  
   `GatewayChild` consumes `gateway.ready` to open its readiness gate, then `continue`s without queueing it ([gateway.py:270](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:270)). A test that directly invokes `deliver_child_frame` can pass while production loses the frame.  
   **Fix:** Capture the actual raw stdout frame in the transport adapter. The acceptance test must run fake spawn → `GatewayChild` → drain → subscriber. Synthesizing a replacement `gateway.ready` would violate payload opacity.

4. **High — The three-path bridge cannot preserve child emission order.**  
   **Plan:** [lines 13–19](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:13).  
   **Contract:** Native protocol outcome at [14–17](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:14).  
   Responses, session events, and process events are split into independent queues/futures by `GatewayChild` ([gateway.py:270](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:270), [307](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:307)). Alternating two queue polls cannot reconstruct their stdout order, and executor completions can reorder responses relative to events—or two responses relative to each other.  
   **Fix:** Use one ordered raw-frame ingress per child and perform routing/correlation in that order. Add an `events_before → response → process event → events_after` test asserting exact downstream order.

5. **High — Blocking spawn/session/send work remains on the FastAPI loop.**  
   **Plan:** [pool lines 52–54](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:52), [relay line 86](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:86), versus the rule at [21](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:21).  
   `child_for_employee` performs `wait_ready()` and `child.request()` synchronously while holding a thread lock. Only `handle.wait()` is explicitly offloaded. `begin_request()` also performs a synchronous child write ([gateway.py:352](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:352)).  
   **Fix:** Explicitly offload child lookup/spawn/session initialization and synchronous sends. State how shutdown coordinates outstanding executor work before process teardown, and add an event-loop responsiveness test.

6. **High — The pending index breaks on downstream-ID reuse across children.**  
   **Plan:** [lines 68–83](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:68), [168–172](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:168), [378](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:378).  
   **Contract:** Per-downstream namespacing at [68–70](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:68).  
   `pending_by_downstream_request_id` alone cannot represent the same downstream ID concurrently used for child A and child B. The stale `child_request_id` and undefined `employee_bucket_marker` declarations contradict the later handle-identity design.  
   **Fix:** Key the per-downstream pending collection by `GatewayRequestHandle` identity, storing child identity and original downstream ID in each value. If duplicate IDs must be rejected, scope that check to `(child, downstream_request_id)`. Add one downstream issuing ID `1` concurrently to two employees.

7. **High — Child death is keyed only by employee, creating a stale-generation race.**  
   **Plan:** [lines 23](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:23), [89](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:89), [210](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:210).  
   **Contract:** [lines 68–75](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:68).  
   A delayed EOF callback from an old child can arrive after respawn, emit a reset for the replacement, and cancel the replacement’s pending requests. EOF and waiter failure can also report the same death twice.  
   **Fix:** Carry the exact dead `GatewayChild` or generation token into death delivery. Retire only a matching pool record, cancel only pending entries whose child is that instance, and deduplicate reset emission. Add delayed-old-death-after-respawn and duplicate-detection tests.

8. **High — Dead and partially initialized children can be orphaned before replacement.**  
   **Plan:** [lines 52–55](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:52).  
   **Contract:** Exclusive session ownership and lifecycle at [47–55](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:47).  
   On ready/session-create failure, the plan merely avoids storing the child. On respawn it “drops” the dead record. But `alive=False` means stdout EOF was observed, not that the OS process was waited or reaped ([gateway.py:409](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:409)). A still-running old process can overlap a replacement resuming the same stored session.  
   **Fix:** Boundedly shut down every partially initialized child, and shut down/join the old child and drain before replacement resumes its session. Test ready failure, session-create failure, and stdout EOF from a process that remains alive.

9. **High — Arbitrary forwarded session RPCs bypass the one-session/own-session invariant.**  
   **Plan:** Generic forwarding at [lines 150–158](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:150), while pool ownership is claimed at [208](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:208).  
   **Contract:** [lines 47–50](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:47).  
   An authenticated downstream can envelope `session.create` or `session.resume` for a live role-child session. The pool’s internal bookkeeping does not prevent the child from executing it. That can give one child multiple sessions or resume a role-owned session.  
   **Fix:** Resolve this contract tension explicitly before implementation. Either the binding contract must authorize rejecting/controlling session-lifecycle methods, or the downstream protocol must expose only operations bound to the pool-owned session. Silent reliance on a cooperative browser does not prove the invariant.

10. **High — The proposed barrier fake deadlocks under `GatewayChild._send_lock`.**  
    **Plan:** [test line 308](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:308).  
    `GatewayChild` holds `_send_lock` while calling `ChildProcess.send()` ([gateway.py:352](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:352)). If the first fake `send()` waits for the second send, the second can never enter—even from another executor thread.  
    **Fix:** Let both sends return immediately while withholding responses; after the second request has been observed, release/queue responses, preferably in reverse order. Keep `asyncio.gather` and add a fail-fast timeout.

11. **Medium — The stated `asyncio.Lock` discipline is incompatible with synchronous relay callbacks.**  
    **Plan:** Synchronous callback at [17](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:17), lock at [84](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:84), synchronous snapshot at [196](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:196), synchronous route calls at [97](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:97).  
    A plain callback cannot acquire an `asyncio.Lock` without awaiting.  
    **Fix:** Since map mutation is loop-affine and fan-out uses `put_nowait`, remove the lock and keep short synchronous callbacks; alternatively make every affected method async and schedule/await it consistently.

12. **Medium — Ordinary drain timeout is not EOF.**  
    **Plan:** [lines 17–23](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:17).  
    Both feed APIs raise `GatewayError` when a timed poll is empty; only a sentinel returns `None` ([gateway.py:197](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:197), [375](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/gateway.py:375)). A literal implementation dies after 0.2 seconds of quiet.  
    **Fix:** Specify catch-and-continue for polling timeout and treat only sentinel/`alive=False` as death. Test an idle live child that emits a later event.

13. **Medium — The config-gate tests do not test server composition.**  
    **Plan:** [lines 325–329](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:325).  
    **Contract:** Composition and gating at [26–30](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:26), [56–58](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/contract.md:56).  
    Manually returning `None` from `pool_provider`, or directly composing a pool, can pass even if `_lifespan` ignores `relay_backend_enabled`.  
    **Fix:** Enter the actual `create_app` lifespan twice with `test_mode=False`, varying only the flag while replacing all role/relay children with fakes; or extract and test the exact composition helper invoked by `_lifespan`.

14. **Medium — Disconnect, death, send-failure, and mutation tests do not prove their full claims.**  
    **Plan:** [disconnect line 310](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:310), [routing lines 317–318](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:317), [death lines 321–323](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:321).  
    Disconnect only covers one child, not every child touched by A. Death does not prove all-and-only dead-child mappings are canceled while another child survives. The “bad send” test makes `put_nowait` fail, whereas real failure occurs in `websocket.send_text`; the route does not watch writer termination, so it can leave receive and registration parked indefinitely. A synchronous fan-out also cannot be mutated “mid-delivery” without a re-entrant hook.  
    **Fix:** Expand these tests across two employees; make one actual writer raise from `send_text`; have the route terminate and unregister when either receive or writer ends; force subscription mutation through a re-entrant/scheduled test hook.

15. **Medium — The “exact” identity environment can inherit stale identity/skill keys.**  
    **Plan:** [lines 201–207](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:201), tests [298–299](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:298).  
    Starting with `dict(base_env)` means “do not set `PLAN_TICKET_ID`” and “do not set `HERMES_TUI_SKILLS`” do not remove inherited values. Production passes `os.environ`. A Chief child can inherit a ticket ID.  
    **Fix:** Explicitly remove `PLAN_TICKET_ID` and `HERMES_TUI_SKILLS` before applying the employee-specific identity. Test with a deliberately polluted `base_env`.

16. **Low — Several structures have not earned their existence.**  
    **Plan:** Future-only `live_session_id` at [43](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:43), always-true `available()` at [56](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:56), stale pending structures at [74–83](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/hermes-relay-s1-foundation/implementation-plan.md:74).  
    **Rule:** CLAUDE.md naming/restraint [lines 24–26](/Users/khushaljagota/.hermes/planning-v2/CLAUDE.md:24).  
    `live_session_id` is explicitly justified by later-stage use, `available()` always returns true while the route checks pool presence, and `employee_bucket_marker` is neither descriptive nor used by the final correlation design.  
    **Fix:** Remove them from S1. Keep only state directly consumed by S1 behavior.

## Defensible choices

- The absence of WebSocket/reconnect/`hermes serve` machinery is correct for the revised contract.
- The explicit `{relay:"request", employee_entity_id, frame}` envelope cleanly separates routing from the native child payload.
- A distinct session source plus retaining only IDs created by this pool is a sound internal resume-own-only mechanism.
- Subscribe-as-routing-only, no idle reaping, and no relay-selected `HERMES_TUI_SKILLS` are restrained S1 choices.
- Handle identity is the correct correlation token; the problem is the contradictory/index keying around it.
- One default-false config flag, reuse of the already-resolved planner home, the `elif not config.test_mode` gate, and reuse of the single shutdown deadline match the contract.
- `/api/relay` using middleware-only authentication and accept-then-close 1013 when unavailable matches `/api/events`.
- The allowlist correctly makes no changes under `minds/`, `chat/`, or `runtime/`, and the tee has no production observer.
- Per-downstream outbound queues plus recipient snapshots are a sound isolation approach once the lock/signature and writer-lifecycle issues are corrected.

## Bottom line

**The plan is not safe to implement as-is.** Findings 1–10 must be resolved first. Most importantly, the current public `GatewayChild` surfaces do not provide the raw request, response, notification, ordered-frame, or `gateway.ready` access that the binding contract and plan assume. That architecture/API mismatch needs an explicit contract-compliant adapter or an owner-approved contract/API revision before implementation begins.

---

## Orchestrator assessment of the review (verified against src/planner/minds/gateway.py)

I independently checked the load-bearing High findings against the actual gateway source. Findings 1–4 are correct and, together, are an ARCHITECTURE-LEVEL blocker, not a plan-revision item:

- **#1 correct** — `GatewayChild.begin_request(method, params)` (gateway.py:335-358) rebuilds the frame `{"jsonrpc","id",method,params}` and allocates its own id. It cannot forward a native frame verbatim; unknown top-level fields are dropped; an id-less notification cannot be sent at all (id is always allocated).
- **#2 correct** — `GatewayRequestHandle.wait()` (gateway.py:139-155) returns only the `result` dict and RAISES `GatewayRpcError` on an error frame. The relay never sees the full response frame, so it cannot preserve unknown/error fields, cannot tee the raw response, and cannot read the child id off the response (the plan's §4 line 166 correlation trick fails).
- **#3 correct** — `gateway.ready` is consumed by the readiness gate and `continue`d (gateway.py:273-277); it never reaches `_route_event`/`next_process_event`. The contract REQUIRES routing `gateway.ready` to the employee's subscribers, so a `deliver_child_frame`-level test passes while production loses the frame.
- **#4 correct** — responses (Event-keyed `_pending`), session events, and process events land on three independent structures; polling two queues + resolving `handle.wait()` on an executor cannot reconstruct the child's true stdout emission order (needed for a transparent stream relay).
- **#12 correct** (minor) — `ChildSessionEventIngress.next_event(timeout)` / `next_process_event(timeout)` RAISE `GatewayError` on an empty poll (gateway.py:197-209, 375-387); only the death sentinel returns `None`. The plan's "loop with 0.2s timeout" needs catch-and-continue.

**Root cause.** `GatewayChild` is a typed JSON-RPC CLIENT (method+params in, `result` dict out, demuxed event queues). The contract demands a TRANSPARENT native-frame RELAY (verbatim forward, unknown-field preservation, ordered stream, `gateway.ready` delivery). These are incompatible through `GatewayChild`'s public API, and the contract forbids modifying `minds/`.

**Resolution requires an owner decision** (the ticket names `GatewayChild` as the mechanism but that mechanism cannot meet the ticket's own behavioral requirement):
1. Build a raw-frame relay transport IN `hermes_backend/` directly on the existing `SpawnFn` / `ChildProcess` (`send(line)`, `read_stdout()`) / `spawn_popen` seam — spawning `hermes_python -m tui_gateway.entry` and reading/writing raw newline-delimited stdout/stdin lines itself. This gives verbatim relay + emission order + `gateway.ready` + a single ordered ingress, touches nothing in `minds/`, and reuses the exact stdio transport the contract points at — but it does NOT go through `GatewayChild`'s methods (`begin_request`/`claim_session_event_ingress`/`next_process_event`), so the contract wording ("through GatewayChild") must be read as "through the GatewayChild stdio transport / SpawnFn seam." RECOMMENDED.
2. Authorize a bounded, additive change to `minds/gateway.py` exposing a raw-frame relay path (violates the current no-minds-edit rule; needs explicit owner sign-off).
3. Accept a non-verbatim, retyped relay (contradicts the contract's "forwards native frames verbatim").

Findings 6, 7, 8, 9 are real design issues that survive any transport choice — notably #9: an authenticated downstream can envelope `session.create`/`session.resume` and escape the one-session/own-session invariant; the plan cannot enforce that invariant while forwarding arbitrary native session RPCs, so the contract must say whether the relay controls session-lifecycle methods. Findings 4, 5, 10 largely dissolve under resolution path 1 (single ordered stdout reader; blocking sends off-loop; no `_send_lock` barrier hazard). Findings 11–16 are ordinary fixable plan issues.

**Status: BLOCKED pending owner decision on the transport/contract tension (path 1 recommended) and the session-lifecycle-method question (#9).** Not advanced to implementation.
