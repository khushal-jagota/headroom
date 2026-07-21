# ACP-05 durable compaction fork — focused implementation review

## Findings

### P1 — The hub releases transition admission before compaction completion and idle publication

`ConversationHub.commit_compaction_transition` publishes reset, replay, ready, queued human echoes, and
the queue snapshot, then immediately settles/removes the transition token
(`src/planner/conversation/hub.py:585-599`). A prompt action blocked in
`_wait_for_compaction_transition` is therefore released and can publish its human echo immediately
(`src/planner/conversation/hub.py:398-428`, `1183-1193`). The broker does not publish the normalized
compaction boundary, settle the tracked turn, or publish idle until after the hub commit has returned
(`src/planner/conversation/turn_broker.py:715-777`).

This permits an N+1 ordinary human echo (and other newly admitted attach/action work) to interleave
between the queue snapshot and the compaction completion. It violates the frozen atomic browser order
in contract items 6-8 and the requirement that successor intent starts only after capture publication
and idle settlement. The hub unit test only asserts the five envelopes emitted inside hub commit; it
does not race an admitted action against the broker-owned completion, so it does not cover this gap.
The transition barrier must remain closed until the normalized completion and idle settlement are
published, with waiters released only after the whole transition is complete.

### P1 — Generation-fatal registry failures are treated as recoverable capture failures by the broker

The registry correctly invalidates the exact runtime when post-CAS durable state cannot be resolved or
when exact N cannot be restored (`src/planner/conversation/employee_registry.py:917-926`, `953-969`,
`990-1010`). However, no fatal disposition reaches the actor. The generation-bound strategy propagates
only a generic exception and suppresses any secondary abort result
(`src/planner/conversation/runtime_ports.py:290-308`). `_Actor._finish_capture` maps every such exception
to the ordinary failed-capture result (`src/planner/conversation/turn_broker.py:636-654`); because there
is no replacement transition, it aborts the hub token, publishes failed boundaries and idle on the old
handle, and advances the FIFO (`src/planner/conversation/turn_broker.py:742-777`).

Consequently, a CAS failure followed by an unprovable N restore, or a committed/ambiguous CAS followed
by failure before the replacement transition is returned, leaves an open actor and an apparently idle
old browser epoch even though the registry has invalidated that runtime (and durable state may already
be N+1). That contradicts contract item 9's generation-fatal restoration rule and the post-CAS
fail-closed requirement. Direct registry tests prove invalidation, and the broker test proves fatality
only after a replacement transition already exists; there is no integrated test for a fatal registry
exception before `take_capture_transition()` can return a replacement. The broker needs an explicit
fatal outcome (or equivalent exact-runtime revalidation) and must fail the actor/generation and settle
all queued/tracked intent instead of publishing idle and advancing it.

## Other reviewed areas

The advertised official `session/fork` call is made on the exact initialized leased child; capability
retention, exact request forwarding, ordered private load, N to N+1 mirror-before-record publication,
CAS ambiguity and loser adoption, queue retargeting, old-session filtering, and the normal same-child
identity path are implemented consistently with the contract. The official-SDK e2e scenarios use the
real SDK child-process boundary and separately prove durable restart replay, automatic queue succession,
and missing-capability continued use of N; they are not mirror-only tests.

No additional test run was needed: the supplied focused evidence is green, and both findings follow
from deterministic cross-owner control flow not exercised by those tests.

## Verdict

**NOT READY** — the two P1 transition/failure-ownership defects above must be corrected and covered by
focused broker/hub tests before ACP-05 can be integrated.

## Focused correction check

### P1 transition admission — CORRECTED

Hub commit and recoverable abort now stage the selected outcome without settling the transition;
admission remains closed until the broker explicitly completes the token. The broker performs that
completion only after normalized boundary publication, tracked settlement, activity/idle publication,
and FIFO advance. The same bounded deadline governs completion, while hub expiry and explicit failure
settle blocked waiters, so the transition has no permanent timeout/deadlock path.

The supplied deterministic tests cover the corrected order: the commit waiter stays blocked until
explicit completion, the abort waiter also stays blocked until completion, deadline expiry and fatal
failure release waiters, and broker observers prove tracked settlement, boundary publication, idle,
and successor start all precede completion. This finding is closed.

### P1 generation-fatal disposition — NOT FULLY CORRECTED

The registry now emits `ConversationRuntimeGenerationFatal` for the named unprovable restore and
post-CAS dispositions, and the broker correctly treats a directly propagated fatal before transition
return as generation-fatal: it fails the hub transition, tracked turn, actor, and queued FIFO without
abort, idle, or successor advance. The new integrated unrestorable-original test deterministically
covers that direct commit/CAS path.

One path from the original finding remains. If normalization first raises and the required cleanup
abort then discovers that exact N cannot be restored, `capture_compaction_with_deadline` suppresses
the abort's `ConversationRuntimeGenerationFatal` and re-raises the original non-fatal exception
(`src/planner/conversation/runtime_ports.py:319-322`). The broker consequently takes its recoverable
no-transition branch: hub abort, failed boundary, tracked settlement, idle, and FIFO advance
(`src/planner/conversation/turn_broker.py:658-665`, `778-817`) even though the registry has invalidated
the generation. None of the named new tests combines an initial normalization exception with a fatal
registry abort/restore disposition, so deterministic coverage does not close this case.

## Focused correction verdict

**NOT READY** — the admission/settlement P1 is corrected, but the generation-fatal P1 remains open for
the suppressed fatal-abort path and needs one deterministic broker/registry regression test. No suite
was rerun for this narrow correction check; the supplied evidence was inspected as requested.

## Final fatal-abort precedence check

### Remaining generation-fatal path — CORRECTED

`capture_compaction_with_deadline` now gives a mandatory abort's
`ConversationRuntimeGenerationFatal` precedence over the initial normalization exception
(`src/planner/conversation/runtime_ports.py:319-326`). Non-fatal abort errors still preserve the
original exception, but an abort that proves exact N cannot be restored reaches the broker as the
explicit generation-fatal disposition.

The broker consequently takes its pre-transition fatal branch: it fails the hub transition, settles
the tracked turn as errored, fails the actor/generation, and rejects the queued FIFO without calling
the recoverable abort seam, publishing idle, or starting the queued successor
(`src/planner/conversation/turn_broker.py:649-657`, `762-777`).

`test_fatal_abort_after_normalization_failure_fails_actor_and_queued_intent` is a deterministic
integrated regression for the exact missing combination. Its strategy first raises during
normalization; restoring the original session during mandatory abort then fails. The assertions prove
the generation-fatal tracked error, failed-not-aborted transition, failed actor and invalidated
runtime, rejected queued intent, no successor prompt, and no idle activity. The supplied focused
evidence records the test red before the production change and green afterward, plus green scoped
static and affected-unit checks.

## Final correction verdict

**READY** — the final fatal-abort precedence finding is corrected and deterministically covered. With
the previously closed transition-admission finding, both implementation-review P1 findings are now
resolved. No suite was rerun for this final narrow check; the supplied evidence was inspected as
requested.
