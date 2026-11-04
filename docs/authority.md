# Who may act

One sentence decides almost every guarded write in Headroom: you may act on anything strictly below you.

Khushal, Chief, Managers, Workers, and goal agents all call the same routes. There is no separate interface for agents. Who the caller is, and where the target sits, decides whether the call is admitted.

## Who can call

The server knows five kinds of caller: the **owner** (Khushal), **Chief**, a **Manager**, a **Ticket** (its Worker), and a **goal** (its agent). The browser and an ordinary `panels` call are the owner. An agent's `panels` calls carry its identity, set by the conversation that launched it.

## Position

```text
Khushal ─┬─ everything
Chief ───┘

Manager ─ its current child Tickets

Ticket ─── nothing
Goal ───── nothing
```

A Manager stands above a Ticket because the Ticket is assigned to it. Move the Ticket and the old Manager no longer stands above it. A Ticket stands above nothing, so its Worker cannot decide its own proposal. That is the rule answering, not an extra ban. (A Worker type could declare something its Tickets stand above. No current type does.)

The same rule covers owner-level configuration: schedules, skills, Worker types, and projects sit at the top, so only Khushal and Chief stand above them. A Manager does not gain control of a schedule by being assigned the Tickets it creates.

## Your own record

Acting as yourself is a different question from standing above something. A Worker files proposals, writes its recap, and asks for help on its own Ticket. A Manager writes its own goal, recap, and artifacts. A goal agent edits its own goal and page. The chain does not decide these.

Proposing is the Ticket speaking about its own work, so nobody, including Khushal, proposes on a Ticket's behalf.

## The exceptions

- **Re-parenting.** Only Khushal and Chief may move a Ticket to another Manager. At the moment of the call, the Ticket really is below the Manager trying to hand it away, so the plain rule would allow it. The exception exists so authority cannot be given away by whoever currently holds it.
- **Creation.** Making a Ticket acts on nothing that exists yet, so the rule cannot refuse it. The pieces it arrives with are checked one at a time instead. You can place it under a Manager in your own chain. Only someone above the new Ticket may set its ceiling and holder.
- **Doors about who you are.** A small set of routes is not about any target. They ask only what kind of caller this is. Some admit only Khushal: his own reads and sends in a conversation, answering an agent's permission question, saving a credential, installing an update, and settling a Manager. Others admit Khushal or Chief, such as Chief's recap and the defaults for new Managers. These are deliberate and few.

Who must look at a parked proposal, the holder, is a narrower fact owned by the [Ticket lifecycle](tickets.md). Holding a review grants nothing the rule does not already give.

## What this is not

The rule decides what a caller may do. It does not prove who the caller is. The browser and the command line state an identity and the server believes it. It is not a login system.

Reading Tickets and Managers is open to every caller. The rule guards writes.

## Decisions that shape it

- **One rule instead of per-route lists.** Adding a route means asking where its target sits, not writing a new list of who is allowed.
- **Self-approval is impossible by structure.** It falls out of position, so no route can forget to forbid it.
- **Authority cannot be handed away.** Re-parenting is reserved for the top.
- **Identity is trusted, not proven.** Keeping strangers out is the host's job, not this rule's.

## Code

- `src/planner/core/authority/logic.py`: the rule, the own-record check, and who may hold a review.
- `src/planner/core/authority/service.py`: the door routes call.
- `src/planner/core/authctx.py`: turns a request into a caller, and holds the owner-only doors.

_Last verified: 2026-11-04._
