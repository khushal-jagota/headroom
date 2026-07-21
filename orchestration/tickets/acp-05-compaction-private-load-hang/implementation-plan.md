# ACP-05 asynchronous compaction handoff implementation plan

## Outcome

Replace the disproven same-child fork/load transaction with one session-aware, cross-process handoff:

1. the exact live source child forks its compacted session;
2. a fresh unpublished child generation privately loads and validates that fork;
3. durable CAS chooses the winner;
4. short publication quiescence installs the fresh child as N+1; and
5. the hub emits reset, raw replay classified exactly once with the summary replaced in place by its
   persisted typed boundary or current-capture boundaries, pre-publication updates, ready, and queue.

ACP and repository I/O run under a lifecycle reservation, never under the publication/update gate.
The existing 300-second absolute deadline and exact diagnostics do not change.

## Reload/replay correction addendum

This addendum supersedes every later phrase that calls registry/transition replay "normalized" or
"classified", or places a successful compaction boundary after the queue:

- Schema v24 remains version 24. Add `compaction_boundaries_json` to canonical DDL and idempotently
  add/backfill it for already-created dogfood v24 databases. The binding CAS compares N's exact persisted
  provenance and atomically writes only the ordered, unique boundary IDs/triggers settled by the current
  capture on N+1. It does not append older-capture provenance; a loser adopts only the durable winner.
- Every SDK child, registry capture, `PreparedCompactionCapture`, `CompactionCaptureTransition`, ordinary
  attach, requested-cancel replacement, and transition-port replay stays raw typed ACP replay.
- One private Hub complete-batch helper is the only replay classifier. It invokes the bound backend
  strategy exactly once, using the existing Hermes summary parser, and immediately publishes the result.
  At the raw summary's exact replay position it emits the persisted completed boundary or ordered
  current-capture boundaries. The raw summary is never an ACP browser message.
- A successful broker capture publishes `compacting` before the prompt but does not publish completed
  boundaries again after Hub commit. The browser order is exactly
  `reset -> replay with in-place typed replacement -> winning quarantine -> ready -> human echoes -> queue`.

## Phase 1 — force both production races red

Extend `tests/support/acp_scripted_agent.py` with deterministic lifecycle scheduling controls, used only
by focused tests:

- after returning `session/fork`, schedule a candidate `available_commands_update` so the SDK can
  observe it before the next outgoing `session/load` observer;
- permit an older source-session update to remain at the ordered FIFO head; and
- schedule exact candidate metadata after the load response so transition-owned pre-publication
  quarantine is exercised.

Add an official-SDK child regression in `test_acp_employee_child.py` that completes source load, forks,
then privately loads the candidate. Before the correction it must fail with the exact
`AcpSessionUpdateCallbackMismatch`; after the correction it must prove:

- the pre-request candidate update and load replay reach only the private sink;
- a valid source-session update reaches only ordinary ingress;
- the response barrier waits for every raw update observed before the load response; and
- no child/epoch/ordered slot remains failed or pending.

Add one production-shaped registry regression in `test_acp_employee_registry.py` using the real SDK
child/ordered ingress and registry gate topology. Hold an older ordinary source update ahead of the
candidate replay and assert under a short test-only external timeout that preparation completes,
ordinary N ingress drains, the durable binding stays N until commit, and no publication gate or task is
stranded. This is the canonical red/green proof for the 300-second live failure.

Add hub integration coverage for both legal candidate origins: the post-fork update emitted by the
original source child and a post-load update emitted by the fresh unpublished child. With two browsers,
pin ordinary N before reset, private replay then both permitted candidate origins once before ready, and
an admitted post-commit N+1 update after ready. Prove the transition quarantine is empty after a normal
win, external winner, pre-CAS failure, abort/cancellation, expiry, and shutdown.

Add narrow unit cases for a malformed/missing session identity during private capture, a valid
different-session update, and ingress capacity/fingerprint parity. Do not weaken the existing fatal
checks for genuinely malformed or unmatched callbacks.

## Phase 2 — make private response epochs session-aware

In `ordered_ingress.py`, give `AcpResponseConsumptionEpoch` an optional exact
`private_session_id`. Enforce the shape when an epoch begins:

- a private sink requires one non-blank expected session ID;
- a public response epoch has neither; and
- only one private response epoch remains active at a time.

When raw `session/update` arrives, read and validate `params.sessionId` before choosing its downstream.
Route it to the private sink only when it equals the private epoch's exact session ID. A matching update
is legal before the outgoing request observer has assigned `request_id`; a valid update for another
session stays on ordinary ingress. The request ID continues to identify only the response that freezes
the private epoch's already-observed wire prefix.

Keep the single connection-wide ordinal queue, bounded capacity, raw/typed fingerprint matching, and
serial consumer unchanged. Do not introduce an RPC-origin field ACP does not provide, a second ingress,
or a delay.

In `sdk_child.py`, pass `LoadSessionRequest.session_id` when opening the private load epoch. Preserve the
current rule that `capture_load_session` returns only after the load response has been observed and the
complete previously observed prefix has reached its selected sinks.

## Phase 3 — split lifecycle exclusion from publication quiescence

In `employee_registry.py`, replace the overloaded `_employee_gates` authority with two explicitly named
per-employee locks:

- a **lifecycle-mutation gate** serializes attach, new conversation, planned retirement,
  requested-cancel replacement, compaction preparation/settlement, and competing winner adoption; and
- a **publication-update gate** is shared by guarded ordinary ingress/death and short record-generation
  validation/publication changes.

Allocate both gates with each child generation. Preserve one lock order everywhere: lifecycle mutation
before publication-update, and never wait for lifecycle mutation while holding publication-update or
the registry's global metadata lock.

Audit every current `_employee_gate` use, not only compaction:

- `guarded_ingress`, unreserved death settlement, lease validation, generation discard/retirement, and
  the final record swap use the publication-update gate only for bounded in-memory work and the existing
  downstream publication call;
- attach, new conversation, requested-cancel replacement, planned retirement, unpublished retirement,
  and compaction use lifecycle mutation for the long transaction;
- no ACP initialize/new/load/fork/close call, repository resolve/CAS, response barrier, or child-death
  settlement is awaited while publication-update is held; and
- existing reserved/planned child-death paths continue settling without reacquiring a lock their owner
  holds.

Keep exact-handle revalidation before and after external work. The lifecycle gate prevents another
in-process mutation; the short publication gate supplies N-before-N+1 quiescence against callbacks.

Update requested-cancel and competing-winner paths to mark every unpublished generation in
`_reserved_callback_generations` before spawn and clear it exactly once on publication or cleanup. Add
tests for death before load, during load, and immediately before publication.

## Phase 4 — prepare on a fresh unpublished child

Keep `PreparedCompactionCapture` as the strategy-facing immutable value: original handle, candidate
binding, raw typed ACP replay, ordered current-capture boundary provenance, deadline, and timeout. Extend
the registry-private prepared state with the exact
candidate child, child generation, record identity, and held lifecycle gate. Those implementation
owners do not escape through the public port.

`prepare_compaction_capture` performs this order under the one original deadline:

1. acquire lifecycle mutation and briefly use publication-update to validate the complete leased N
   handle;
2. require advertised fork support and call official `session/fork` on the source child outside
   publication-update;
3. validate a non-empty, distinct fork ID and form binding N+1;
4. allocate and reserve a fresh candidate child generation using the same backend definition;
5. initialize that child, privately load the fork with the exact-session response epoch, and capture the
   raw typed ACP replay; and
6. revalidate source and candidate liveness, store the private prepared state, and retain only the
   lifecycle reservation through normalization/settlement.

The source child is never loaded to the fork. The fresh load is both the typed replay boundary and the
cross-process durability proof. Candidate post-fork notifications from the source child and post-load
notifications from the unpublished child are admitted into the active compaction transition's dedicated
quarantine. Do not rely on the general `_source_capture`: while N is ready, its current behavior drops a
non-current source and drops a current-source payload whose session ID differs from N.

On fork/spawn/load failure, discard the candidate generation and close its child. Because `/compact`
already mutated the source process, invalidate/retire the source generation and raise the exact phase as
generation-fatal. Do not call the deleted same-child original-restore path.

## Phase 5 — settle CAS and publish the fresh winner

Run repository CAS/resolve outside publication-update while lifecycle mutation remains reserved. The
compaction CAS compares the exact expected N binding plus persisted provenance and atomically writes N+1
with only the ordered unique boundary provenance settled by this capture. Never append N's older tuple;
the new summary has compacted it. An ambiguous/losing result adopts only the durable winner's tuple.

For candidate N+1 winning normally or after ambiguous-CAS resolution:

1. resolve and validate the exact durable candidate;
2. acquire publication-update, which lets every already-admitted N sink finish first;
3. revalidate the exact N source and live reserved candidate;
4. install an `AcpEmployeeRecord` with the candidate child generation/object/identity, set it as the only
   accepted generation, and clear its reserved marker;
5. release publication-update; and
6. detach/close the source child and return the complete N+1 handle plus private replay.

If durable state remains exact N, close/discard the candidate, invalidate the compacted source
generation, and raise a generation-fatal failure naming durable CAS/resolve. Normal later demand fresh-
attaches the authoritative N binding. A normalization failure follows the same cleanup through
`abort_compaction_capture`; abort no longer pretends to restore the source and therefore does not return
an ordinary recoverable failed result on that stale handle.

If another valid binding won, refactor the existing adoption helper to use the same reserved fresh-child
load and short publication swap. Return that complete winner transition with `fork_won=False`; never
publish or delete the unused fork. If the durable result cannot be proven before the deadline, invalidate
the uncertain runtime and preserve the existing exact `unresolved` diagnostic.

Every success/failure/cancellation path removes the prepared state, releases lifecycle mutation once,
clears accepted/reserved candidate identity as appropriate, consumes planned death settlement, and
detaches child close without creating another budget.

Delete `_restore_original_under_reservation` and the same-child normal-winner branches once callers and
tests use the fresh-child path. Replace tests that assert unchanged child generation/record identity or
successful same-child restore; do not preserve them as compatibility behavior.

## Phase 6 — own and drain the compaction notification quarantine

In `hub.py`, add one transition-private FIFO to `_CompactionTransitionState`. Each entry retains the exact
`ConversationIngressSource` key, the validated `SessionNotification.session_id`, and the typed payload;
one aggregate `_ingress_capacity` bound applies to the whole transition. Overflow fails the transition
through its existing exact-source failure owner rather than silently dropping traffic.

While a compaction transition is active and not yet committed, `_ingest_source` classifies traffic before
the ready-stream fast path:

- an exact original-source notification naming N continues through ordinary publication, so already
  admitted N remains before reset;
- a notification naming another session from the original source, including Hermes's post-fork candidate
  metadata, enters the transition FIFO;
- a notification from a non-current/unpublished source enters the transition FIFO; and
- malformed traffic without a routable session identity keeps the ordered-ingress/protocol rejection
  failure behavior and is not guessed into a candidate session.

`commit_compaction_transition` is already serialized by the employee sequencer. After installing the
replacement stream and publishing reset, pass the complete raw private replay once through the Hub's sole
strategy-owned batch classifier. Publish its non-summary items unchanged and replace the raw summary at
its exact position with the durable completed boundary or ordered current-capture boundaries. Then consume the entire transition
FIFO once. Publish only entries whose session ID equals the replacement binding and whose source key is
the exact original source or exact replacement source permitted by this transition; discard every loser
entry. Then publish ready, retained FIFO human echoes, and the queue snapshot as today. Mark the transition
committed before later ingress can re-enter quarantine; an admitted N+1 update after commit follows the
ordinary stream and therefore appears after ready.

This produces:

`ordinary N -> reset -> load replay with typed in-place replacement -> winning quarantined updates -> ready -> human echoes -> queue -> later N+1`

Normal commit discards non-winning FIFO entries. Abort, generation-fatal failure, external-winner cleanup,
expiry, cancellation, shutdown, and final settlement synchronously clear the whole transition-owned FIFO
and leave no capacity/waiter state. The general `_source_capture` remains owned by ordinary attach/load
and is not repurposed. Ordinary attach/load and requested-cancel recovery use the same sole Hub batch
helper. Delete the successful broker compaction publication after the commit call; failure publication
is unchanged. No wire or Svelte change is needed.

Update broker/strategy failure tests so a normalization/pre-CAS abort that invalidates the source is
generation-fatal and releases the compaction transition, actor, tracked turn, and FIFO consistently. The
normal N+1 path still rekeys and retargets pending prompts once.

## Phase 7 — focused proof and handoff

Run after the implementation settles, without canonical `./verify`:

1. scoped Ruff over changed Python source/tests;
2. strict Mypy over the affected conversation modules;
3. focused ordered-ingress/SDK child, registry, strategy, broker, hub, composition, and official-SDK e2e
   suites;
4. the affected ACP browser suites and Svelte diagnostics only if hub ordering assertions touch their
   fixtures; and
5. one no-model real-Hermes harness proving the formerly red fork/update/load order now completes in
   seconds.

Record exact commands/output in `implementation-report.md`. One independent focused diff review checks
session routing, lock order, candidate death/cleanup, CAS ambiguity, N-sink quiescence, source-buffer
drain, and deadline diagnostics. Address one concrete review round; repeat only for a real finding.

Then restart the real `127.0.0.1:8767` server and use Computer Use to prove `/compact` commits N -> N+1,
the summary expands, hard reload reconstructs the N+1 transcript, server restart loads the same binding,
and a later prompt continues it. Only that proof opens ACP-06 legacy deletion.
