# Worker steps

A Worker step is one prompt that asks the Ticket’s Worker to fill the current blank, or to revise a proposal that was sent back. Headroom’s job here is narrow: notice that a Ticket is ready, claim it, and send that prompt into its conversation. The loop does not watch the turn, does not decide the proposal, and does not advance the stage.

That narrowness is the product. The Worker can run for a long time, the person can talk to it in the same conversation, and the stage moves only when a proposal is filed or a parked proposal is decided.

## When a step starts

The loop looks at ordinary Tickets in the order they were last updated, least recently updated first, then by id. A Ticket is ready when all of these are true together:

- It is not backlogged, and its locale is this machine.
- The stage is not Done and still has a blank.
- The derived status is empty. That single check keeps out parked approvals, live blockers, claims already out, and errors.
- If the stage is user-owned, this entry onto the stage has not yet had its opening turn.

The ceiling is not one of these questions. The ceiling decides what a finished proposal does. It does not decide whether the Worker may start.

One more question is asked of the conversation itself, because the record cannot answer it: is this Worker’s turn already running? A busy conversation is left alone for this pass. Automation does not steer into an unrelated turn.

If the Ticket is ready and idle, one guarded write takes the worker-step claim. The same write records the user-owned opener fact when this stage needs one. Every readiness check runs again inside that write, so two loops cannot both win. While the claim is out, the Ticket reads as agent.

The loop then starts or reuses the Ticket’s conversation, composes the opener from the current Ticket, the Worker type, any revision guidance, and a memory-loss note when one is owed, and sends it in queue mode. Queue mode waits for a free turn instead of injecting into whatever is running.

## What the send does to the claim

| Delivery fate | What the claim does |
| --- | --- |
| started, queued, or injected, worker-owned stage | The step counts as started. The claim stays out. |
| started, queued, or injected, user-owned opener | The step counts as started. The claim returns to rest. The opener fact stays, so readiness will not send a second automatic opener for this entry onto the stage. The turn may still be running. |
| refused or suppressed | The claim returns to rest. A tentative user-stage opener is forgotten, so a later pass can try again. |
| uncertain | The claim becomes errored and stays there until `panels ticket restart-worker`. Headroom does not retry an uncertain send. |

Queued is success because the prompt will run. On a worker-owned stage the claim stays out until the Worker files a proposal or someone accepts, rejects, or completes the gate. Those writers give the claim back. A turn that merely ends does not. On a user-owned opener the claim is already back once the send is accepted, which is why a collaborative stage can read as empty while its opening turn is still going. The opener fact, not the claim, is what stops a second automatic prompt.

The loop wakes on the contentless change signal and on a timer. The timer and the database remain the canonical answer. A missed signal only delays a check.

## What a step feeds, and what feeds it

Ticket state, blockers, backlog, locale, and the conversation’s busy bit feed the decision. The step feeds one real prompt. That prompt is the Worker’s input. The Worker’s proposal, or a person’s decision on a parked proposal, feeds the next time the Ticket can read empty and be eligible again.

Manager settlement does not gate this. A settled Manager’s children still run. Backlog does.

## Where it shows up

On the Ticket and the rail, a running turn shows a working mark. That mark comes from the conversation, not from the claim. An idle Ticket whose claim is still out can look quiet and still read as agent, because a worker-owned step was handed off and has not been answered with a proposal yet. After a user-owned opener is accepted, the claim is already at rest, so the Ticket can read as empty while that opening turn is what the pane shows. An errored claim is visible as an error and is how a supervisor learns that automatic progress has stopped.

Restarting the Worker is the explicit repair for an errored claim. It is not something the loop does by itself.

## Pointers

The read-only decision is `src/planner/runtime/worker_step_readiness.py`. The poll and `start_ready_worker_step` are `src/planner/runtime/worker_step_readiness_loop.py`. The claim writers are in `src/planner/tickets/data.py`. Opening and sending against the conversation contract is `src/planner/runtime/conversation_start.py`.

_Last verified: 2026-10-30._
