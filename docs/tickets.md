# Ticket lifecycle

A Ticket is one piece of work small enough to hand to a single AI Worker. It is the unit Headroom can stage, pause, approve, and come back to. Its lifecycle answers one product question for every piece of work: how far may this Worker go before a person has to look?

## What a Ticket is made of

- **Its own workflow.** The Ticket holds its ordered stages, result fields, ownership, and guidance. Creation copies a [preset](worker-types.md) or accepts a custom workflow.
- **Stage text.** Each stage before Done has one blank. Filling it, and having that fill settle, moves the Ticket one stage forward. Brief holds the intake: the original ask, its boundaries, and advice that should stay readable beside the later stages.
- **A ceiling and a holder.** The ceiling is the stage at which the Worker's proposals start waiting for a person. The holder is who that person is.
- **An assignment** to Chief or one ordinary Manager. This is who the work sits under.
- **A conversation**, the Worker's only input. Workers send ordinary messages to an explicit recipient, separate from the ceiling holder. See [Conversations](conversations.md).
- **A launch setup**: backend, model, and reasoning, copied from a selected preset or explicitly chosen for custom work, and fixed once the conversation exists.
- **A machine**: the execution destination, chosen at creation and changed only by an explicit owner move.
- **Blockers**, other Tickets that must be Done before this one may start a step.
- **A recap**, the Worker's short status for a person, and **artifacts**, the plans, documents, images, and prototypes the work produced.

A recap is 80 characters at most. The server refuses a recap that names an internal ID: a Ticket or Manager ID, a Git commit hash, or a UUID. The refusal is one line that says what to rewrite. Manager and Chief recaps follow the same rule. Recaps stored before the rule stay as they are, and an empty recap is always allowed.

## How a stage advances

```text
Worker files a proposal for the current stage
                    │
             proposal resolver
                 ceiling decides
                  │         │
             auto-settle   review
                  │         │
                  next stage
```


A proposal waits briefly for a busy database before it changes the record. If that wait expires, the sender receives a retryable refusal. Headroom never repeats a proposal commit after an uncertain result.

Only the Ticket's own Worker files proposals. The owner, Chief, and Managers decide them, and never write them. The proposal resolver is the only writer that makes stage text canonical and moves a stage. Each approval moves one stage.

Approvals name the next ceiling and holder. The Worker performs authorized follow-through in Consequences and submits a verified report. If the ceiling is Done, the report settles automatically and the Ticket reaches Done. Otherwise the ceiling holder reviews the report, and acceptance moves the Ticket to Done.

By default a new Ticket's ceiling is on Brief and its holder is whoever created it. The Brief then waits for that person, and their approval sets the first real ceiling. A creator can set a later decision ceiling up front, including Consequences, but never Done.

If creation supplies Brief text, the first stage must gate the `brief` field. The server refuses the text otherwise. A custom workflow can omit Brief when creation supplies no Brief text.

On a collaborative stage, the person can also complete the gate directly, without a Worker proposal. That uses the same resolver and still moves one stage. A reply typed into the conversation never decides a proposal. Approval and Send back enforce any supplied proposal revision in the same write as the decision. Protected decision receipts retain an exact command and its outcome for the Ticket's lifetime. Repeating that command returns its saved result, even after the Ticket advances.

The browser and iPhone use protected decisions on Review and Ticket pages. The older accept and reject routes remain available to the CLI and existing callers through the same proposal writers. Those routes do not provide receipt recovery.

Each settled stage remembers who approved it: a named person or agent, or auto-accepted when it settled below the ceiling. Editing the text clears that approval.

## Ceiling, holder, and assignment

These are three separate facts.

- The **assignment** says where the work sits in the Workspace, and so which Manager stands above it.
- The **holder** says who is on the hook to look when a proposal parks: the owner, Chief for a Chief-assigned Ticket, or the assigned Manager. A parked proposal for the owner appears in [Review](review.md). One for Chief or a Manager arrives in that agent's conversation as a real prompt. See [Managers and Chief](managers.md).
- **Who may approve** is wider than the holder. Anyone who stands above the Ticket may approve or send back. See [Who may act](authority.md).

Moving a Ticket to another Manager can make the old holder illegal. Then the holder returns to the owner unless the move names a new one.

## How status is read

A Ticket stores one control fact, the worker-step claim: at rest, out, or errored. [Worker steps](worker-steps.md) owns it. Everything a person calls status is derived on read, in this order: awaiting approval, errored, agent owned (a step is out), blocked (at rest behind an unfinished blocker), and empty (ready for the next step). Ticket reads show the name and recap first, then status, live Worker state, stage, and any gate or unanswered question. The API value `agent_owned` and the human label “Agent owned” mean that a step is out. Native and desktop clients must update before this server release. Updated clients also accept the former value `agent` during rollout and in historical decision receipts. The conversation supplies the live state, so a step can remain out while its Worker is idle. Consequences uses these same activity and status facts. It has no separate waiting group.

The navigation index stores each Ticket's derived status with its other facts, in the same transaction as each write. Ticket and Manager list reads take status from there instead of deriving it again for every Ticket. See [Live screens](live-updates.md).

Lists and the command line also give each Ticket one need word: what it waits on, and from whom. It maps the browser's group for the Ticket, except that backlog and done win over every other fact, so a deferred or finished Ticket never reads as waiting on anyone. See [the command line](cli.md).

A parked proposal remains actionable even if an older claim is errored. An errored claim stops automatic progress. Restart the Worker for explicit repair. A restart first kills the Ticket's conversation. After a successful kill, it unlinks the conversation, gives back any claim, and starts a fresh step. The claim does not determine whether a restart can proceed. A revision also starts in a fresh conversation. Its undelivered decision notes carry over.

If new launch settings fail after the old Worker ends, restart clears its old link and marks the claim errored when the database permits cleanup. The settings error remains visible. The Ticket stays stopped until an explicit restart, so old launch settings cannot start another Worker automatically.

If cleanup cannot establish that the old Worker ended, restart refuses before the claim, conversation link, or launch settings change. Use Recheck Worker cleanup before another restart. A user-owned Stage refuses a restart unless there is failed decision feedback and the user explicitly confirms retrying it. That confirmed restart stops the conversation and retries the feedback. A Mac Ticket refuses while the Mac is away, because the kill cannot reach it.

## Backlog and delete

Backlog pauses the next automatic Worker step. The stage, any parked proposal, the conversation, and the artifacts stay. Messages and approvals still work, and a turn already running keeps running. Resume clears the pause and restores eligibility under the ordinary checks. A Manager's counts by need put deferred children in their own backlog count. Their direct records retain the proposal and conversation facts.

Tickets come from the owner, Chief, or Managers. Tickets cannot be created on a timer. Daily schedules for measure sources remain separate.

Delete removes the Ticket's record and leaves its files. It is available from the command line and the API, not the browser, to anyone who stands above the Ticket. It refuses while the Worker is still busy, unless the caller forces it or is the Ticket's own Manager. Then the Worker is stopped first, so it never outlives its Ticket.

## Where the Worker runs

The machine destination defaults to the home at creation. The owner can move a Ticket to another registered machine. The source must prove that repository work is clean and pushed, and that the old agent stopped. A fresh conversation starts from the same Ticket record and the published work branch. The stage, proposal, ceiling, assignment and backlog state stay. Private agent memory does not transfer. Settings → Machines edits its name, icon, and color. These display edits never move work. Each destination's machine code pulls its own work, and the work waits while that machine is away. The Chief and Managers record their own machine; see [Managers](managers.md).

The browser accepts a move before it completes. Panels stores its progress, so the owner can leave and return. The move shows each phase, elapsed time, and any refusal or startup failure with a next step. A server interruption ends the progress with an explicit failure rather than replaying a partial move. The command line keeps its final-result response.

The browser and CLI use `machine_id`. Legacy `locale` requests remain compatible: `vps` names only the original home, and `macos` names only the original Mac. Requests that supply both must agree. The owner registers extra destinations in Settings → Machines. See [Extra machines](executor.md).

## Where it shows up

The Ticket page is the inspector: the stage ladder, the current gate with its proposal card, blockers, artifacts, and the conversation beside them. The Workspace rail and each Manager page list Tickets by the attention they need. Creation happens from the command line, the API, or by asking Chief or a Manager. Tickets cannot be created on a timer. Daily schedules for measure sources remain separate. The browser has no new-Ticket form. A new Ticket starts from a preset or custom stages, and its stages belong to that Ticket.

## Decisions that shape it

- **The ceiling is the product choice.** A person decides, Ticket by Ticket and gate by gate, how much the Worker may do alone.
- **One door moves a stage.** The resolver moves the Ticket through its saved stages. A preset amendment never moves an existing Ticket.
- **Status is derived, not stored.** The claim, a parked proposal, and live blockers are enough to say what a Ticket is doing.
- **Holding a review is not authority.** The holder says who must look. It grants no private approval route, and a Worker, which stands above nothing, can never decide its own proposal.

## Code

- `server/tickets/`: the record, derived status, and writers (`data/`).
- `server/tickets/logic/`: the stage machine, proposal routing, and the resolver (`machine.ts`, `proposal_routing.ts`, `resolution.ts`).
- `web/src/routes/TicketRoute.svelte` and `web/src/components/WorkerProposalCard.svelte`: the Ticket page and its gate.

_Last verified: 2026-11-24._
