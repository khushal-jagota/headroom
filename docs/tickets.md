# Ticket lifecycle

A Ticket is one piece of work small enough to hand to a single AI Worker. It is the unit Headroom can stage, pause, approve, and come back to. Its lifecycle answers one product question for every piece of work: how far may this Worker go before a person has to look?

## What a Ticket is made of

- **A Worker type and its ladder.** The type is chosen at creation and never changes. It declares the stages, from Brief to Done, and which stages are collaborative. See [Worker types](worker-types.md).
- **Stage text.** Each stage before Done has one blank. Filling it, and having that fill settle, moves the Ticket one stage forward. Brief holds the intake: the original ask, its boundaries, and advice that should stay readable beside the later stages.
- **A ceiling and a holder.** The ceiling is the stage at which the Worker's proposals start waiting for a person. The holder is who that person is.
- **An assignment** to Chief or one ordinary Manager. This is who the work sits under.
- **A conversation**, the Worker's only input. See [Conversations](conversations.md).
- **A launch setup**: backend, model, and reasoning, copied from the Worker type at creation and fixed once the conversation exists.
- **A locale**: the VPS or Khushal's Mac, chosen at creation.
- **Blockers**, other Tickets that must be Done before this one may start a step.
- **A recap**, the Worker's short status for a person, and **artifacts**, the plans, documents, images, and prototypes the work produced.

## How a stage advances

```text
Worker files a proposal for the current stage
                │
        proposal resolver
         │                 │
  stage before the     stage at or past
  ceiling              the ceiling
         │                 │
  text settles,        proposal parks for the holder
  next stage           │
                       ├─ approve: text settles, next stage,
                       │  and the approver sets the next ceiling and holder
                       └─ send back: stage stays, optional guidance
                          goes out with the Worker's next step
```

Only the Ticket's own Worker files proposals. Khushal, Chief, and Managers decide them, and never write them. The proposal resolver is the only writer that makes stage text canonical and moves a stage. Every approval moves exactly one stage and names how far the Worker may go next, so the ceiling is chosen again at each gate.

By default a new Ticket's ceiling is on Brief and its holder is whoever created it. The Brief then waits for that person, and their approval sets the first real ceiling. A creator can set a later ceiling up front, but never Done.

On a collaborative stage, the person can also complete the gate directly, without a Worker proposal. That uses the same resolver and still moves one stage. A reply typed into the conversation never decides a proposal.

Each settled stage remembers who approved it: a named person or agent, or auto-accepted when it settled below the ceiling. Editing the text clears that approval.

## Ceiling, holder, and assignment

These are three separate facts.

- The **assignment** says where the work sits in the Workspace, and so which Manager stands above it.
- The **holder** says who is on the hook to look when a proposal parks: Khushal, Chief for a Chief-assigned Ticket, or the assigned Manager. A parked proposal for Khushal appears in [Review](review.md). One for Chief or a Manager arrives in that agent's conversation as a real prompt. See [Managers and Chief](managers.md).
- **Who may approve** is wider than the holder. Anyone who stands above the Ticket may approve or send back. See [Who may act](authority.md).

Moving a Ticket to another Manager can make the old holder illegal. Then the holder returns to Khushal unless the move names a new one.

## How status is read

A Ticket stores one control fact, the worker-step claim: at rest, out, or errored. [Worker steps](worker-steps.md) owns it. Everything a person calls status is derived on read, in this order: errored, awaiting approval, agent (a step is out), blocked (at rest behind an unfinished blocker), and empty (ready for the next step). Whether the agent is working right now is a separate question, answered by the conversation.

An errored claim stops automatic progress. Restarting the Worker is the explicit repair.

## Backlog and delete

Backlog pauses the next automatic Worker step. The stage, any parked proposal, the conversation, and the artifacts stay. Messages and approvals still work, and a turn already running keeps running. Resume clears the pause and nothing else.

Delete removes the Ticket's record and leaves its files. It is available from the command line and the API, not the browser, to anyone who stands above the Ticket. It refuses while the Worker is still busy, unless the caller forces it or is the Ticket's own Manager. Then the Worker is stopped first, so it never outlives its Ticket.

## Where the Worker runs

The locale is chosen at creation and does not change. A VPS Ticket's agent runs on the VPS. A Mac Ticket's agent runs on the Mac, and starts only while the Mac is connected and ready. Either way the VPS keeps the record and decides when to start. See [Mac execution](executor.md).

## Where it shows up

The Ticket page is the inspector: the stage ladder, the current gate with its proposal card, blockers, artifacts, and the conversation beside them. The Workspace rail and each Manager page list Tickets by the attention they need. Creation happens from the command line, the API, or by asking Chief or a Manager. Tickets cannot be created on a timer. Daily schedules for measure sources remain separate. The browser has no new-Ticket form.

## Decisions that shape it

- **The ceiling is the product choice.** A person decides, Ticket by Ticket and gate by gate, how much the Worker may do alone.
- **One door moves a stage.** The resolver is the only stage writer in normal operation. The one exception is a Worker type amendment, which may move Tickets backward when their stage is removed. See [Worker types](worker-types.md).
- **Status is derived, not stored.** The claim, a parked proposal, and live blockers are enough to say what a Ticket is doing.
- **Holding a review is not authority.** The holder says who must look. It grants no private approval route, and a Worker, which stands above nothing, can never decide its own proposal.

## Code

- `src/planner/tickets/`: the record, derived status, and writers (`data.py`).
- `src/planner/tickets/logic/`: the stage machine, proposal routing, and the resolver (`machine.py`, `proposal_routing.py`, `resolution.py`).
- `web/src/routes/TicketRoute.svelte` and `web/src/components/WorkerProposalCard.svelte`: the Ticket page and its gate.

_Last verified: 2026-11-04._
