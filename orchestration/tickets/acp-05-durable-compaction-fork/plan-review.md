# ACP-05 durable compaction fork plan review

## Verdict

`NOT READY`

One concrete blocker remains. This was one bounded pass over only the frozen contract and
implementation plan.

## Finding

### P1 — The transaction deadline is asserted but not defined or propagated

The plan says preparation's `finally` path releases the employee reservation under “the existing
absolute service deadline,” says exceptions run a “shielded bounded abort,” and asks tests to prove
“one hard deadline.” It never identifies who creates that deadline or requires it to cross the full
transition:

```text
hub begin → official fork → private load/barrier → normalization → CAS/resolve →
restore or winner load → actor rekey → hub reset/replay/ready commit → hub token release
```

That leaves the implementation free to bound only shutdown or abort while an ordinary hung fork,
private load, repository resolution, restore, or hub commit holds the per-employee registry
reservation and the hub transition token indefinitely. Attached actions, attaches, and worker demand
would then wait forever, contrary to frozen behavior 11.

Required plan correction:

- Name the owner that creates one absolute capture-transition deadline before hub `begin`.
- Pass that exact deadline through hub begin/commit/abort, registry prepare/commit/abort, official
  fork, private load/barrier, winner adoption, and original-session restoration. Every blocking await
  must use only the remaining budget; no shield may extend it.
- Specify expiry settlement: attempt exact-N restoration only within the remaining budget; if exact
  restoration or a post-CAS N+1 transition cannot be proven, fail the owning generation; in all
  cases settle the hub token and wake every waiter once.
- Add deterministic tests for a hung fork and hung private restore/commit proving the hard deadline,
  generation disposition, released registry gate, released hub waiters, and no pending task.

Confidence: 9/10. This follows directly from the plan's named multi-owner transaction and the
contract's explicit hard-deadline/no-pending-waiter requirement.

## Named-seam disposition

- Official fork capability, exact request forwarding, and private ordered replay: covered.
- Prepare → normalize → commit/abort ownership and exact-N restoration: covered apart from the
  deadline blocker above.
- Ambiguous CAS candidate, exact-N recovery, competing-winner adoption, and orphan policy: covered.
- Actor rekey, complete FIFO handle/session retarget, client-ID/order preservation, and conflict
  rejection: covered.
- Hub admission barrier and reset → replay → ready → queue/compaction ordering: covered.
- Explicit/automatic, refresh/restart, failure, queue, Send Now, and missing-capability proofs:
  covered apart from the required hung-operation deadline proofs above.

No second finding was raised in this bounded pass.

## Correction check

`READY`

The amendment resolves the single P1 finding without reopening any approved seam:

- The broker actor now creates one monotonic `capture_transition_deadline` before hub `begin`, from
  an injected timeout with the existing conversation-service timeout as production default.
- That exact deadline is passed unchanged through hub begin/commit/abort, strategy normalization,
  registry prepare/commit/abort, fork, private load/barrier, CAS/resolve, original restoration,
  winner adoption, and actor rekey. Every await uses only the remaining budget, and shielding cannot
  extend it.
- Expiry has an idempotent hub-token wake path. Pre-CAS expiry attempts exact-N restoration only
  inside the remaining budget and otherwise fails generation N; post-CAS expiry fails replacement
  generation N+1. Registry reservations release in local `finally` blocks, and timeout-owned tasks
  are detached with result consumption rather than awaited beyond the deadline.
- Deterministic tests now hold fork, exact-N restoration, and hub commit forever, asserting the
  pre/post-CAS generation disposition, released registry gate, every waiter awakened once, and no
  pending owned task.

The original P1 is closed. No new finding was opened.
