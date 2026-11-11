# Worker steps

A Worker step is one prompt that asks a Ticket's Worker to fill the current stage, or to revise a proposal that was sent back. The step loop's job is narrow: notice that a Ticket is ready, claim it, and send that prompt into the Ticket's conversation. It does not watch the turn, decide the proposal, or advance the stage.

That narrowness is the design. A Worker can run for a long time, a person can talk to it in the same conversation, and the stage moves only when a proposal is filed or a parked proposal is decided. See [Ticket lifecycle](tickets.md#how-a-stage-advances).

## What it is made of

- **The readiness decision.** A read-only check of one Ticket that answers "may a step start now?" or names the reason it may not.
- **The worker-step claim.** The only control fact a Ticket stores about automatic work: at rest, out, or errored. See [Ticket lifecycle](tickets.md#how-status-is-read).
- **The opener fact.** On a stage the person shares with the Worker, a record that this entry onto the stage has had its opening turn.
- **The loop.** A background pass over the Tickets that could start, least recently updated first, that runs readiness, takes the claim, and sends. It skips backlogged Tickets, Tickets at a final Stage, and Tickets whose step is out, failed, or waiting on a proposal before it reads them.

```
 change signal or timer
          │
          ▼
 readiness ──no──► skip
          │yes
 conversation busy? ──yes──► skip this pass
          │no
 one guarded write: take the claim (and the opener fact)
          │
          ▼
 send the opener in Queue mode ──► fate decides the claim
```

## When a Ticket is ready

All of these must hold:

- It is not backlogged.
- It can run here: its locale is this machine, or it is a Mac Ticket and the [Mac executor](executor.md) is present (its lease is fresh). Until then the step shows "Waiting for the Mac".
- The stage is not terminal and has a field for the Worker to fill.
- The Ticket's status is empty. That one check keeps out parked proposals, blockers, a claim already out, and errors.
- On a user-owned stage, this entry onto the stage has not had its opener yet.
- The Ticket's model is enabled. A disabled model shows as the blocker until it is enabled or the choice changes. See [Usage](usage.md).

The ceiling is not a readiness question. It decides what a finished proposal does, not whether the Worker may start. Manager settlement does not gate a step either.

One more question goes to the conversation itself: is a turn already running? A busy conversation is left alone this pass, so automation never steers into an unrelated turn.

Automatic starts share a launch gate with Manager wakes and recovery notices. The gate permits one new start per second and four starts awaiting acceptance. Workers use at most three slots, so Chief and Managers retain capacity. A slot ends on positive agent activity or definite refusal. A wire write or a local queue position alone does not prove acceptance. Running turns have no limit from this gate. Explicit restart requests bypass automatic launch pacing.

An absent executor holds no shared slot. Its pending starts retain their claims and command identities until receipts and ownership evidence settle them. On return, pending starts pass through the gate again. Durable bindings preserve this rule across a home restart. Control commands can pass unfetched deferred work without a new prompt or a change to conversation order.

## How a step is sent

One guarded write takes the claim, re-running every readiness check inside it, so two passes cannot both win. On a user-owned stage the same write records the opener fact. The loop then starts or reuses the Ticket's conversation and sends an opener built from the current Ticket and its saved workflow, any revision guidance from a sent-back proposal, and a memory-loss note when the Worker was compacted mid-step. It sends in Queue mode, which waits for a free turn.

| Delivery fate | What happens to the claim |
| --- | --- |
| started, queued, or injected, Worker-owned stage | The step counts as started. The claim stays out. |
| started, queued, or injected, user-owned opener | The claim returns to rest. The opener fact stays, so no second automatic opener is sent. |
| refused because the backend did not start, Worker-owned stage | The claim becomes errored. The failed conversation stays for an explicit restart. |
| any other refusal, or suppressed | The claim returns to rest, and a tentative opener is forgotten, so a later pass can try again. |
| uncertain | The claim becomes errored. Headroom does not resend a prompt that may have arrived. |

On a Worker-owned stage the claim stays out until the Worker files a proposal or someone decides the parked one. A turn that merely ends does not give it back. A Worker that was compacted while its step was still out also gets a short memory-loss note, because nothing in its shortened conversation says that it forgot.

Every Ticket receives its complete workflow guide at kickoff, from its own copy, so a later edit to its Worker type does not change what it reads. Context recovery restores access to that guide. Ordinary later stage prompts identify the current step.

## Where it shows up

A running turn or a running background task shows a working mark, and that mark comes from the conversation, not the claim. An idle Ticket with its claim out can look quiet while it waits for a proposal. An errored claim shows as an error. It also sends one notice to the Ticket's assigned supervisor: the Chief for a Chief ticket, its Manager otherwise. The notice names the reason, for example "could not start (backend_did_not_start)" or "delivery uncertain". A sender whose message is refused sees the reason too, as in `message refused: backend_did_not_start`. Restarting the Worker is the explicit repair. The loop never does it by itself.

## Decisions that shape it

- Start work, then stop watching. Nothing listens for a turn to end.
- Readiness is computed from the record, never stored as its own state.
- A send that may have reached the Worker is never repeated automatically.
- The loop wakes on the [change signal](live-updates.md) and on a timer. The database is the answer, so a missed signal only delays a check.

## Code

- `src/planner/runtime/worker_step_readiness.py`: the readiness decision.
- `src/planner/runtime/worker_step_readiness_loop.py`: the loop and one step's start.
- `src/planner/runtime/worker_memory.py`: the memory-loss note.
- `src/planner/tickets/data.py`: the claim writers.
- `src/planner/runtime/conversation_start.py`: starting the conversation and sending.

_Last verified: 2026-11-12._
