# Who may act

One sentence decides every call in Panels: **you may act on anything strictly below
you.**

Every operation has exactly one address. Khushal, the Chief, an Outcome and a Ticket's
Worker all call the same route for the same thing, and who you are decides whether you
are admitted. There is no second address for an agent, and no list of principal kinds
anywhere that says who is allowed through a particular door.

## Position

```
Khushal ─┬─ everything
Chief ───┘

Outcome ──── its own current Tickets

Ticket ───── nothing
   └── a planning Ticket: the part of the plan its Worker type was created to write
```

An Outcome stands above a Ticket because the Ticket says so: its record names the
Outcome it sits under. Move the Ticket to another Outcome and the old one stands above it
no longer, from that moment. Nothing else is stored, and nothing is granted by hand.

A Ticket stands above nothing, so a Worker cannot decide its own proposal. That is the
rule answering, not an exception written beside it: nothing is strictly below itself.

Three planning Worker types are the one place position is declared rather than derived. A
`planning-day` Ticket writes a Day's morning overview, and a `planning-sprint` Ticket
shapes a Sprint and the Outcomes in it. The thing they were created to produce is the
shared plan itself, which no chain can express, so each type declares exactly which plan
fields it stands above and reaches nothing else.

## Your own record is not authority

A principal writes its own record through its own operations, and that is a separate
question from the rule. A Ticket proposes, recaps, notes and asks for help. An Outcome
writes its own body and artifacts. Neither is standing above anything, so the chain does
not decide it — being the thing does.

**Proposing is not a permission at all.** It is the Ticket speaking about its own work.
Nobody proposes on a Ticket's behalf, including Khushal.

## The one exception

**No principal may move a thing out of its own chain.** Changing a Ticket's Outcome
changes who stands above it, which is handing authority around rather than using it, and
the rule cannot refuse it: at the moment of the call the Ticket really is below the
Outcome that is moving it away. Re-parenting stays with Khushal and the Chief.

Creation and deletion get no special case. A created thing belongs to its creator's
chain, and if you stand above a thing you may delete it.

## What this is not

The rule decides what a caller may do. It does not decide who a caller is. A position is
a truthful local claim, exactly as it was before: the CLI and the browser say who they
are, and Panels believes them. What changed is that the claim must now name the thing
being acted on.

Reads are open. `GET /api/tickets` and `GET /api/items` ask the rule nothing, so an
address that only reads is not protecting anything and never was.

_Code paths:_ `src/planner/core/authority/` (the rule, the chain, and the planning
declarations), `src/planner/core/authctx.py` (turning a request into a principal).

---

_Last verified: 2026-09-21._
