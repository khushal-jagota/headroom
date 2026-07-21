# ACP-04 implementation plan review — round 1

## Verdict

**NOT READY — bounded plan corrections required.** There is no frozen-contract or product-design blocker, and the restrained three-mount UI scope is appropriate. The plan should not enter implementation until the six corrections below are incorporated because the first two otherwise permit a production deadlock or an incomplete worker stream.

This is the one requested review round. These findings are corrections to the current plan, not a request for another architecture or design pass.

## Review basis

Reviewed `implementation-plan.md` against the frozen ACP-04 `contract.md`, the program plan, the settled ACP-01/02/03 APIs, and the current DB/server/runner/gateway/Svelte composition. The review concentrated on executable ownership, source identity, worker completion, durable binding semantics, lifecycle order, and frozen wire compatibility.

## Findings

### [P0] 1. The proposed employee lock can deadlock on normal ingress, and the rejection-only seam does not give valid ingress immutable source identity

The plan says all employee transitions share one employee lock (lines 114 and 137–140), while attach/actions await registry load, broker delivery/cancellation, or permission settlement under that serialization. Those owner calls can synchronously publish back through the hub before they return: ordered child ingress awaits its downstream callback, and broker publication also awaits the configured publisher. If that callback needs the same employee lock, the initiating call and callback wait on each other.

Separately, line 37 adds immutable source identity only to protocol rejections. Current `AcpEmployeeRegistry.guarded_ingress` sends every valid update to one global `conversation_ingress(payload)` callback, so the hub cannot prove that a valid update came from the still-bound child or correctly capture updates arriving before first/replacement binding publication. Mutable registry lookup after callback admission is not an equivalent proof.

**Required correction:**

- Define one guarded, source-aware registry ingress for both `SessionNotification` and `ProtocolUpdateRejectedPayload`. It must capture immutable employee ID, child generation, and runtime-record identity before publication.
- Make that callback bounded-enqueue the item and return; do not await hub publication while holding the registry employee gate.
- Give the hub one per-employee sequencer that drains those admitted items and allocates stream sequence numbers. Express first binding, replacement, and actions as sequenced transitions/barriers or watermarks rather than holding a non-reentrant lock across owner calls that can publish back.
- Route load/replay, prompt updates, protocol rejections, and pre-binding capture through that same sequencer. Do not create a separate order domain for capture flush.
- Add latch-based tests in which attach/load, prompt delivery, cancellation, and a protocol rejection re-enter publication before the initiating owner call returns. Each must complete without deadlock and preserve source/order.

Evidence: current registry ingress is source-erasing in `src/planner/conversation/employee_registry.py`; current ordered ingress awaits the downstream sink in `src/planner/conversation/ordered_ingress.py`.

### [P0] 2. A worker can be the first demand, but the plan creates the reset epoch only on browser attach

Lines 126–133 establish reset/load/snapshots/ready through attach and first-binding flows, but the gateway path at lines 164–168 only resolves/establishes a binding and delivers the tracked prompt. An automatic worker step can be the first demand with no browser connected. In that case, a browser joining mid-step has no guaranteed sequence-1 reset epoch or complete reset buffer to replay.

The tracked completion seam also needs an exact state/settlement boundary. The proposed attach query only says active/cancelling (line 41), although an owned turn can still be finalizing capture after the prompt has stopped. Lines 101–102 say capture failure is terminal, but the plan does not explicitly order protocol rejection/capture failure settlement ahead of collector teardown and the queued successor.

**Required correction:**

- Add a hub operation such as `ensure_stream_ready(employee_id, binding)` and require `AcpStepGateway` to await it before installing/delivering the first tracked worker prompt. With zero browsers it must still create and store the canonical reset epoch, bind/flush the exact source, perform typed load/replay, publish owner snapshots, and publish ready through the normal buffer/publisher path.
- Define the broker attach-state result as an exact phase including at least idle, running, cancelling, capture-finalizing, failed, and closed, plus owned queued-normal-turn state. Active replay includes capture-finalizing.
- Allocate the tracked handle and register the collector for the exact runtime identity, client message ID, and prompt epoch before the prompt task can emit its first update.
- Specify one terminal ordering: prompt consumption and required capture/compaction finish; protocol rejection or capture failure for that exact tracked epoch settles the worker result `errored`; then the collector is torn down; only then may a queued successor start. Settlement is once-only and a stale handle cannot affect the successor.
- Add one deterministic worker-first test with no browser, followed by a second browser joining mid-step and receiving a complete reset-based buffer. Add a rejection/capture-failure case proving the exact worker completion errors before the queued successor starts.

### [P1] 3. The worker permission guard needs immutable worker provenance, not a late lookup in the active-handle map

Line 45 distinguishes worker-source from browser permission requests but does not define where that provenance is captured. If the active worker handle is removed before settlement, a stale worker permission could be misclassified as non-worker and take the browser bypass.

**Required correction:**

- When the tracked worker hook is installed, register immutable worker provenance keyed by exact runtime identity and prompt epoch. Permission admission must copy a `worker`/`browser` origin marker into the pending permission record; settlement must never infer origin from whatever handle happens to be active later.
- For a pending request marked as worker-origin, the synchronous pre-settlement guard opens a short connection and `BEGIN IMMEDIATE`, validates the exact durable binding and Ticket mirror, Ticket `agent_running_step` status, current active worker handle/epoch, and active worker chat-turn session, then marks that same pending permission `settling` while the transaction is still held. It then commits and returns without awaiting.
- A worker-origin request with missing or stale provenance rejects. Only a request captured as non-worker at admission may use the non-worker bypass.
- Add the deterministic ticket-transition-versus-settlement latch test around this exact boundary.

### [P1] 4. Several proposed shapes conflict with frozen/current APIs

These are concrete corrections, not new seams:

- Line 153 invents `cursor_session_id`. The frozen attach action contains `employee_id`, optional `last_seen_binding_generation`, and optional `last_seen_sequence`; it does not carry a session ID. The server resolves the current session from the durable binding and validates the generation/sequence cursor against it.
- Lines 204 and 212 invent a stable browser ID. Socket identity is server-side; `crypto.randomUUID()` is only needed for client message IDs already supplied by the controller seam. Do not add a browser ID to the frozen action or wrapper.
- Line 85 makes binding CAS nullable. The settled callback returns a complete non-null winning `ConversationSessionBinding`; stale expected values return the actual winner, while impossible/malformed states fail closed.
- Line 168 names nonexistent `RunResult.completed`. Construct the existing dataclass directly: `RunResult("complete", text, None, session_id, None)`, `RunResult("interrupted", text, None, session_id, None)`, or `RunResult("errored", text, None, session_id, error)`.
- Preserve `StepGateway.run_ticket_step(..., require_existing_session=True)`: before any child spawn or prompt, require the supplied stored session to match the validated durable binding. Missing/mismatched binding or load failure cannot mint a session. Busy delivery raises `SharedGatewayBusy(session_key=session_id)`.

Update the focused tests to assert these exact current/frozen shapes.

### [P1] 5. Production Hermes resolution and shutdown order are not executable as written

Line 189 says to use the resolved Hermes Python executable as the backend executable. The settled Hermes backend requires an absolute executable whose basename is `hermes` and argv `(hermes_path, "acp")`; `resolve_hermes_python()` returns the venv Python, not the Hermes CLI.

Line 193 also stops hub writer/action tasks before broker/registry/permission shutdown. Those owners can still need the hub publisher while in-flight turns, cancellation, permission settlement, or child closure drain. Stopping their output path first reverses the dependency order.

**Required correction:**

- Resolve `python = resolve_hermes_python()`, derive `hermes_executable = python.with_name("hermes")`, and validate it is absolute, executable, and basename `hermes`. Pass that to the settled backend definition so argv is exactly `(hermes_executable, "acp")`. Use `hermes_src_root(python)` only to derive the Hermes source-root environment.
- The background-loop integration file is `src/planner/core/loops.py`, not `src/planner/runtime/loops.py`. Make the ACP gateway the explicit production runner gateway so production cannot fall through to constructing/defaulting to the legacy shared gateway.
- Under one absolute shutdown deadline: first close external WebSocket/action admission; stop discovery and the runner while worker interrupt remains available; stop broker/reverse services while the hub publisher and writers still drain; close registry children; then close permission/browser resources and finally publisher/writer tasks. Every stage receives only the remaining time.
- Keep the bind-once callback slots mandatory, bind exactly once, and fail closed before binding.

### [P2] 6. Preserve persistent rejection state and correct the focused command ledger

The controller correction in line 210 should also preserve the frozen persistent protocol-rejection status through a higher-sequence reset for the same binding. A same-binding replacement reset may clear recoverable sequence-gap state only after a contiguous ready; it must not erase the protocol rejection. A genuinely new binding may reset that status.

The focused commands reference paths/tests that do not match the current tree:

- use `src/planner/core/loops.py`, not `src/planner/runtime/loops.py`;
- use the existing `tests/unit/test_conversation_turn_broker.py`, `tests/unit/test_conversation_permission_broker.py`, and `tests/unit/test_hermes_acp_backend.py` names;
- extend the settled browser suites (`acp-browser-state`, `acp-browser-conformance`, `acp-browser-components`, and `acp-contracts`) instead of assuming `acp-controller` and `acp-transport` files exist; one new production-mount suite is appropriate;
- remove the browser-ID assertion.

## Accepted without further design review

- One production composition, one WebSocket route, one ACP gateway, and no legacy gateway runtime startup is the correct cutover boundary.
- The durable binding table/backfill direction and atomic domain-mirror ownership are consistent with the frozen contract once CAS returns the non-null winner.
- The three existing conversation locations should mount one shared, restrained ACP wrapper. The explicit prohibition on new panels, gradients, ornamental cards, token changes, and route redesign is sufficient; no additional UI design round is needed.
- One authoritative `./verify` after reviewed integration remains the correct completeness proof. No verification run was performed during this plan review.
