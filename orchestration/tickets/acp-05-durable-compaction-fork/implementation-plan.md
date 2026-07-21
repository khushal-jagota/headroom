# ACP-05 durable compaction fork implementation plan

## Design summary

Capture becomes one two-phase, same-child transaction owned by the employee registry:

1. reserve the exact live handle;
2. official-fork its bound session and privately load the fork;
3. let the existing Hermes strategy normalize the private typed replay;
4. commit the fork with the durable binding CAS only when normalization is structurally successful,
   otherwise abort by privately restoring the original binding;
5. move the existing broker actor and every browser to the committed binding before publishing
   `compacted`, idle, or a queued successor.

The registry remains the owner of child/binding state, the backend strategy remains the owner of
summary interpretation, the broker remains the owner of FIFO/turn settlement, and the hub remains the
owner of browser epochs. No new transcript or persistence owner is introduced.

## Phase 1 — expose the official child operation and observed capability

Extend `AcpEmployeeChild` with an exact `fork_session(ForkSessionRequest) -> ForkSessionResponse`
operation and a read-only `supports_session_fork` property. `SdkAcpEmployeeChild.initialize` records
`agent_capabilities.session_capabilities.fork is not None` from the successful initialize response;
it does not derive support from a backend key or command. `fork_session` rejects before the SDK RPC
when the child is dead or support was not advertised, then calls the pinned connection's official
method with the request's exact session ID, cwd, additional directories, MCP servers, and metadata.

Keep `capture_load_session` as the single ordered private replay boundary. The new operation does not
open a second ingress path and does not change the wire contracts.

Tests in `test_acp_employee_child.py` cover advertised/missing capability, exact request forwarding,
empty fork IDs failing closed, and fork followed by private load with the response-consumption barrier
closed before return.

## Phase 2 — replace retire-and-reload with a two-phase registry transaction

Replace `replace_runtime_for_capture` with one prepared compaction transaction in `runtime_ports.py`.
The internal prepared value carries only what the owners require: an opaque transaction identity, the
exact original handle, the fork candidate binding at N+1, and the privately captured typed replay.
The runtime port exposes prepare, commit, and abort operations; commit/abort are single-settlement and
reject a stale or foreign prepared value.

Preparation runs under the existing per-employee transition gate and:

- revalidates the complete lease/record/child identity;
- requires advertised fork support;
- forks the exact bound session with the existing employee cwd/directories/MCP request shape;
- validates a non-empty successor session ID different from the original;
- privately loads that successor on the same child; and
- returns only after the private response barrier and exact-handle revalidation.

The transaction reserves the employee through settlement. Refactor attach/new-conversation and any
other live-record child/session mutation to use that same employee gate, but never hold the registry's
global lock across an SDK call or a callback that can publish into the hub. A `finally` path always
settles/releases the reservation under the existing absolute service deadline.

Abort privately reloads the original durable session into the same child and discards its replay. It
then revalidates the exact original record and leaves binding/child generation/record identity intact.
If exact restoration cannot be proven, it retires/fails that generation rather than pretending N is
usable.

Commit performs the one repository CAS from original N to fork N+1. On the normal winner it confirms
the durable repository/mirror first, then publishes a replacement `AcpEmployeeRecord` with the same
child generation, child object, and record identity. If CAS raises, it resolves the durable row: an
exact candidate is treated as committed, exact N is restored and reported as a recoverable capture
failure, and any other valid binding is adopted as the winner. A CAS loser retires the orphan-loaded
child only when necessary, uses the existing spawn/load publication machinery for the exact winner,
and returns that complete handle. It never calls a private delete for the unused fork.

Registry tests prove normal N→N+1 same-child publication, mirror-before-record ordering, no ordinary
replay leakage, abort restoration, cancellation during prepare, post-load/CAS exception recovery,
candidate-after-ambiguous-CAS resolution, competing-winner adoption, stale handles, and one hard
deadline without a leaked gate.

## Phase 3 — normalize before commit and return one transition result

`GenerationBoundBackendTurnStrategy.capture_compaction` performs prepare, calls the existing
`capture_compaction_from_updates` normalizer on the prepared replay, and commits only for a trimmed
`state="compacted"` result with a summary. A failed/invalid normalized result aborts to N and returns
that existing visible failure. Exceptions after preparation run a shielded bounded abort before
propagating; an unprovable abort becomes runtime failure.

Replace the loose `take_replacement_handle` side channel with one single-consumption internal capture
transition value containing the replacement handle, the exact fork replay to publish, and whether this
fork won the CAS. A competing durable winner supplies its own privately loaded replay and is marked
non-compaction so the broker transitions to it but fails the pending compaction boundaries. No result
ID or backend trigger can override broker-owned boundary IDs/triggers.

Delete the old planned-retirement/new-child capture implementation and its generation-N→N+1 tests.
Keep planned retirement for actual cancellation/new-conversation/shutdown ownership.

The broker actor is the deadline owner. Immediately before hub transition begin it creates one
absolute `capture_transition_deadline` from an injectable capture timeout whose production default is
the existing ACP conversation-service timeout. That exact monotonic deadline is stored with the
capture and passed unchanged through hub begin/commit/abort, strategy normalization, registry
prepare/commit/abort, official fork, private load/response barrier, repository CAS/resolve, exact-N
restore, winner spawn/load/adoption, and actor rekey. Every blocking await uses only
`max(0, deadline - loop.time())`; no component creates a fresh sub-budget and no shield extends the
absolute boundary. The injected timeout lets tests use milliseconds without adding another config
owner.

## Phase 4 — put an admission barrier around the binding change

Add one internal, composition-bound compaction transition port between the broker and hub, with exact
begin/commit/abort ownership. Begin is called after prompt/reverse-service settlement and before the
registry preparation. Under the hub's employee sequencer it validates the complete old handle and
installs a one-shot employee transition token without emitting a reset or changing visible state.

While the token is open, attach, ensure-ready, worker demand, and actions on an attached connection
wait on that token and then re-resolve the stream/binding. A prompt action already admitted for the
same connection/employee is retargeted only in its `PromptRequest.session_id` to the committed logical
continuation; its client message ID, content, delivery choice, and attachments remain exact. Other
actions are revalidated against the resulting binding. This prevents an old-session action, attach
load, or worker prompt from entering between the durable CAS and the N+1 reset. No wait occurs while
holding the hub employee sequencer, and shutdown/failure settles every waiter.

Abort validates that N is still the stream and releases waiters without a browser epoch change.

The transition token records the same absolute deadline. Expiry has one idempotent synchronous wake
path that marks the token failed and releases every attached action/attach/worker waiter exactly once,
even if the hub sequencer or registry operation is cancellation-resistant. Pre-CAS expiry attempts
exact-N restoration only inside the remaining budget; inability to prove restored N is generation-
fatal. Post-CAS expiry is replacement-generation-fatal because durable N+1 cannot be rolled back.
Registry reservations are released in unconditional local `finally` blocks, and timed-out tasks are
detached with result consumption under the existing no-cancellation-grace rule rather than awaited
past the deadline.

## Phase 5 — rekey the actor, retarget FIFO intent, and publish one N+1 epoch

When a capture transition returns a replacement handle, the broker owns the following order inside
the capture-finalizing actor command:

1. Under the broker lock, move the same actor from `(employee, binding N)` to the exact replacement
   key. Reject a conflicting live actor instead of coalescing it.
2. Replace `actor.handle`; retarget every queued `PromptRequest.session_id`, `QueuedPrompt`, and
   complete queued runtime handle to the replacement. Preserve client IDs, enqueue sequence/time,
   origin, order, claimed-ID history, and tracked hooks.
3. Call the hub commit hook. One synchronous employee-sequencer operation replaces the stream while
   retaining attached browser subscriptions, emits N+1 `reset`, publishes the private typed replay in
   order, emits `ready`, re-emits exactly one human echo for each still-queued prompt, and publishes the
   exact retargeted queue snapshot. Sequence starts in the new binding epoch; no N publication follows.
4. Release transition waiters only after all those envelopes are enqueued.
5. Publish the broker-owned normalized compaction boundary on N+1, settle tracked success, publish
   idle, and start the FIFO head once.

For a CAS loser, perform steps 1–4 for the durable winner but publish the pending boundaries as failed.
If actor rekey or hub transition fails after durable commit, fail the replacement generation; never
roll browser state or the repository back to N. If capture has no replacement, abort the hub token,
publish failed boundaries and idle on N, then advance the unchanged FIFO.

Broker/hub unit tests pin this exact order for explicit and automatic capture, two attached browsers,
old-source rejection, an action and attach racing the barrier, tracked worker settlement, queue human
echo/snapshot survival, queued and Send Now single delivery, actor-key uniqueness, CAS loser, missing
capability, invalid summary, transition publication failure, child death, cancellation, and shutdown.
Deterministic timeout cases hold official fork forever, hold exact-N private restore forever, and hold
hub commit forever. At the one absolute boundary they assert the correct pre/post-CAS generation
disposition, released registry gate, every transition waiter awake exactly once, and no pending owned
task.

## Phase 6 — production composition and deterministic official-SDK proof

Bind the broker/hub transition port in `composition.py` before any actor/stream exists, alongside the
existing prompt-ingress hooks. Shutdown closes hub admission/transition waiters before broker/registry
owners and keeps the publisher alive until their final settlements drain.

Extend `tests/support/acp_scripted_agent.py` only for the compaction scenarios:

- advertise official fork;
- maintain separate live and durable histories in a test-owned store path so a fresh process can
  prove persistence;
- make explicit/automatic compaction update only the live source history with one Hermes-shaped
  structural summary;
- implement official fork by deep-copying that live history to a durable new session; and
- make load replay only durable history.

The e2e test runs the production composition and official SDK boundary. It proves explicit compacting
→ N+1 reset/replay/ready → compacted, queue succession, browser refresh, deliberate child death, and
fresh-process load of the same unique summary. A second scenario proves automatic compaction on the
identical path. Missing fork capability proves visible failure and continued use of N. The fixture is
not a product fallback and writes only its temporary test store.

## Phase 7 — focused evidence and handoff

Run, once after the implementation settles:

1. scoped Ruff over all allowed Python source/tests;
2. strict Mypy over the affected conversation source;
3. affected child, registry, strategy, broker, hub, composition, and official-child e2e tests;
4. ACP browser state/component tests and Svelte diagnostics (the transition uses the frozen wire);
5. a production frontend build directed to a temporary directory; and
6. scoped allowed-file/diff inspection.

Record exact output in `implementation-report.md`. One independent focused implementation review
checks transaction settlement, CAS ambiguity/loser ownership, actor rekey/admission ordering, queue
retargeting, and restart durability. A second round occurs only for a concrete finding. Do not run
canonical `./verify`; ACP-10 owns the one final run.
