# Reviewing Closeout order

Use this when the user asks what should be approved, integrated, or closed out first after time away.

## Evidence to inspect

1. Read the full records for every Ticket at Closeout: accepted fields, recap, Implementation evidence, control (`Continue`/`Stop`), status, `updated_at`, and blocker summary.
2. Read the accepted upstream exploration or initiative when the Tickets came from one. Its accepted ordering and handoff notes outrank title/priority guesses. A missing blocker edge does not erase an explicitly recorded dependency.
3. For coding Tickets, inspect the live repository state read-only: current integration branch, ticket branch/worktree, clean versus staged/uncommitted implementation, branch base, overlapping files, and stacked-branch instructions.
4. Distinguish **business sequence** (foundation → dependent capability) from **merge-risk sequence** (shared subsystem or broad base before consumers).
5. Read the live prerequisite's current Closeout state before releasing downstream Success/Approach work. Earlier clean Implementation evidence does not mean the foundation is integrated. If its merge failed verification and was reverted, keep dependents parked even when their own first gate could be discussed. After the repair lands, replace any hard-coded base revision or stacked-branch handoff in dependent Ticket notes with the actual final revision before release.

## Closeout-lane pitfall

A Closeout-lane scheduler can only serialize work after that implementation is integrated and the live runtime is using it. If the lane feature itself is waiting at Closeout, do not release several same-lane Closeouts together first. Close out the lane feature alone, verify it is live, then release the selected next Ticket.

If the scheduler chooses the oldest waiter, that is deterministic fairness—not priority. When the desired order differs, keep later Tickets at Stop and release only the next chosen Ticket. Do not say “the lane will prioritize P0” unless the live policy actually does.

## Current queue mechanics and the control-update subtlety

Verify these rules against the live code before relying on them, but the current implementation behaves as follows:

- A lane is effective project + Worker type. Different lanes may run concurrently.
- A candidate must be on today, non-terminal, at a gated Stage, unblocked, free of a parked proposal or active run, and allowed by its Stage ownership/control. At Closeout, `Continue` can be eligible while an empty `Stop` Ticket is parked.
- Any matching Closeout with non-empty `ticket_status` occupies the lane—including running, paired, errored, or awaiting approval—until it leaves that condition.
- In a free lane, discovery takes the eligible waiter with the oldest `updated_at`; Ticket id breaks an exact tie. Priority and Workspace position do not select the next waiter.
- Marking the active Closeout Done releases its lane. Marking a waiting Ticket itself Done removes it rather than putting it last.

Changing a waiting Closeout from Stop to allowed-through-Done/Continue is itself a Ticket update. It normally makes that Ticket the newest waiter, so if the lane is already occupied it will fall behind older eligible waiters. This is timing-sensitive: the control change rings readiness, so if the lane is free it may start immediately; subsequent updates to other waiting Tickets can reorder them again.

For deliberate sequencing, release the intended first Ticket alone. While it occupies the lane, release the intended second Ticket so it waits safely; keep later Tickets at Stop. Explain this operationally instead of merely reciting the oldest-first rule.

## Recommendation shape

Give the user:

- a short cold-user reorientation;
- one numbered Closeout sequence with a one-line reason per Ticket;
- the downstream dependency chain;
- which Tickets should remain parked;
- any concrete integration wrinkle, such as a stacked branch or staged-but-uncommitted implementation.

Avoid treating every carried ticket as a simultaneous priority. Do not mutate controls merely because you recommended an order; wait for explicit approval.