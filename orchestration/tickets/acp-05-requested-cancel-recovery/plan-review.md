# ACP-05 requested-cancel runtime recovery — focused plan review

## Findings

### P1 — Recovery suppression begins too late to exclude the late output that defines the ticket

The plan installs the hub recovery barrier only inside
`_recover_after_requested_cancel_prompt_exception`, after the prompt task has raised and after the
actor has observed successful cancel and permission-cancel settlement
(`implementation-plan.md:228-249`). That does not order the barrier ahead of old-child source ingress.
Today a child update enters the hub independently through `registry_conversation_ingress`, which merely
enqueues `_ingest_source` (`src/planner/conversation/hub.py:231-242`), while prompt/cancel completion
callbacks enqueue separate actor commands (`src/planner/conversation/turn_broker.py:421-427`,
`880-900`). An old process can therefore emit the late message after accepting cancel but before the
actor's recovery-begin command reaches the hub; it still matches the old ready stream and is rendered
and collected.

The proposed official-SDK fixture has the same proof gap: it schedules a late update and then raises
(`implementation-plan.md:344-353`), but provides neither an ordering handshake nor audit evidence that
the update attempt occurred after suppression was installed. The e2e can pass because the update ran
early or because child retirement killed it before it ran, rather than because the production barrier
rejected it.

The plan must add a deterministic quarantine/order seam that is established before any post-cancel old
source payload can publish, while preserving the unchanged normal `PromptResponse(cancelled)` path. The
official-SDK test must prove the old process actually attempted the uniquely marked late update after
that seam was active, then prove it was absent from browser output and worker collection.

### P1 — Old-child death bypasses the proposed transition serialization and can fail the actor or emit a duplicate error

Phase 3 describes suppressing the old key in `_ingest_source` and cross-rejecting transition begins, but
does not assign the existing death path. `registry_child_died` independently enqueues
`_publish_child_death` and immediately schedules `broker.child_died`
(`src/planner/conversation/hub.py:244-263`). Before replacement commit, the stream still has the old
source key, so `_publish_child_death` emits `connection(error)` and marks it non-ready
(`src/planner/conversation/hub.py:1048-1053`); the broker callback can also fail the actor. In the
registry, a matching planned retirement with a non-null close error settles the retirement and then
continues into the source-aware death callback (`src/planner/conversation/employee_registry.py:491-538`).
That conflicts with the proposed single hub failure settlement and can produce an error before commit
plus another from `fail_requested_cancel_recovery_transition`.

The plan must explicitly route exact old-record death through the recovery token: planned retirement
death should settle registry ownership without invoking ordinary hub/broker death, and an unexpected
old death while the token is open must be consumed by the recovery failure owner exactly once. The
named hub test should hold this production death path between begin and commit and assert one actor
settlement and at most one visible connection error, rather than testing only a direct source-key helper.

## Other reviewed areas

The proposed same-binding fresh-child registry operation, one actor-owned absolute deadline, private
ordered load, durable-binding re-read, complete-handle replacement, reset/replay/ready/queue ordering,
Stop versus Send Now settlement, tracked-work delay, failure cleanup, and separate
new-conversation/shutdown ownership otherwise fit the current production seams. The registry and broker
test matrix is appropriately contract-oriented once the two callback-ordering gaps above are added.

No checks were run because this is a plan review; the findings are direct ownership/order conflicts with
the current source.

## Verdict

**NOT READY** — resolve the old-source ingress ordering and death-callback ownership gaps before
implementation begins.

## Correction check

### P1 resolution — pre-cancel quarantine and late-send proof

**Resolved.** The amended contract and plan now make begin a precautionary exact-source quarantine,
not a post-exception recovery action. `_begin_cancel` creates the one actor deadline, awaits
`begin_requested_cancel_recovery_transition`, stores the token, and only then starts permission
cancellation and sends ACP cancel (`implementation-plan.md:277-282`). The hub holds all subsequent
exact-old-source payloads in a bounded ordered deque without publishing, recording prompt settlement,
or collecting worker text (`implementation-plan.md:163-172`). A normal terminal `PromptResponse`
resumes that exact token and drains the deque through ordinary publication in source order before the
prompt-ingress barrier closes; an exceptional unwind reuses the token and commit/failure discards the
held payloads (`implementation-plan.md:174-180`, `265-290`).

The proof is no longer absence-only. Broker tests hold begin and assert ACP cancel was not invoked,
then inject marked source after cancel acceptance (`implementation-plan.md:324-329`). The official-SDK
fixture synchronously attempts the marked update only after receiving cancel, writes an external audit
only after that send returns, and the e2e must assert the audit exists while the text is absent
(`implementation-plan.md:389-405`). Because production cannot send cancel until begin returns, this is
deterministic evidence of a post-quarantine send attempt.

### P1 resolution — planned and unexpected old-child death ownership

**Resolved.** The registry plan now states that a matching planned retirement never invokes the
ordinary hub death callback for either successful close or close error; it settles only the replacement
transaction (`implementation-plan.md:95-104`). A dedicated registry test pins the non-null-error case
(`implementation-plan.md:140-141`).

For unexpected death, `registry_child_died` is explicitly refactored to classify the complete source
identity under the hub employee sequencer. An exact quarantined old source performs the one-shot hub
transition failure instead of ordinary `_publish_child_death`, then calls `broker.child_died` only for
the matching actor/generation settlement; stale post-commit death is ignored
(`implementation-plan.md:215-220`). The hub and broker tests drive that production callback disposition
and assert one transition/actor settlement with at most one visible connection error
(`implementation-plan.md:241-243`, `337-338`).

## Final correction-check verdict

**READY** — both previously reported P1 findings are fully addressed in the amended contract, owner
flow, and production-seam tests. No other areas were reopened in this narrow correction check.
