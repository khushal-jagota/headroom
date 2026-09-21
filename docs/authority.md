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

Three planning Worker types are the one place position is declared rather than derived.
The thing they were created to produce is the shared plan itself, which no chain can
express, so each type declares exactly what it stands above and reaches nothing else.

- A `planning-day` Ticket writes a Day's morning overview and composes the Day — which
  Tickets are on it. It does not reach into those Tickets.
- A `planning-midday-check` Ticket writes one afternoon field, and can move work on and
  off today.
- A `planning-sprint` Ticket shapes a Sprint, its Outcome commitments, and the brief of
  any Outcome — its title, body, priority, deadline and project.

A declaration that names fields reaches those fields and nothing else. It does not reach
an operation on the whole object, because that names no field: sprint planning writes an
Outcome's brief, and cannot delete the Outcome, reset its manager's conversation, or
write its files.

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

That is asked of the caller alone, at both doors that write the column — editing the
Ticket, and Outcome membership. It includes the Ticket itself. A Ticket that could set
its own Outcome could leave one, or leave every Outcome, and so choose who is allowed to
act on it. Being a thing does not include choosing who stands above you.

Deletion gets no special case: if you stand above a thing you may delete it.

## Making a Ticket

Making a Ticket acts on nothing that exists yet, so nobody is refused for making one. What
the new Ticket arrives with is a different matter, and each part is asked about on its own.

**Its Outcome.** A created thing belongs to its creator's chain. You can name an Outcome you
stand above, an Outcome you are, or the one you are under. Putting work under the Outcome
you already answer to adds nothing to your reach, because the supervisor that gains a child
already stands above you. Naming any other Outcome is borrowing a position you do not hold,
and that is refused. This is not the exception above: nothing is moved out of a chain,
because the Ticket was in none.

**Its ceiling, and who holds it.** These are the two values editing a Ticket reserves for a
caller above it, so stating either one at creation takes the same answer. Khushal, the
Chief, and the Outcome the Ticket will sit under can state them. A Worker that spins off a
Ticket cannot, so the new Ticket gets its Worker type's ordinary ceiling and parks its first
field instead of running to the end.

A stated holder is an address for an approval, so it has to be one that can answer: the
caller itself, or a principal that stands above the Ticket. That is asked wherever a holder
is named — making a Ticket, editing its holder, and approving with a new one. A holder
already stored on a Ticket is left alone.

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
