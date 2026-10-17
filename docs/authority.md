# Who may act

One sentence decides every call in Panels: **you may act on anything strictly below
you.**

Every operation has exactly one address. Khushal, the Chief, a Sprint Item and a Ticket's
Worker all call the same route for the same thing, and who you are decides whether you
are admitted. There is no second address for an agent, and no list of principal kinds
in the general authority rule that says who is allowed through a particular door.

## Position

```
Khushal ─┬─ everything
Chief ───┘

Sprint Item ─ its own current Tickets

Ticket ───── nothing
```

A Sprint Item stands above a Ticket because the Ticket says so. Its record names the
Sprint Item it sits under. Move the Ticket to another Sprint Item, and the old one no
longer stands above it. Nothing else is stored, and nothing is granted by hand.

A Ticket stands above nothing, so a Worker cannot decide its own proposal. That is the
rule answering, not an exception written beside it: nothing is strictly below itself.

Retired planning Worker types have no shared-plan declarations. Their records and
proposals remain historical. They cannot regain calendar write authority through their
old type name.

A Worker assignment explicitly names Chief or an ordinary orchestrator. The stored kind
and Item reference must agree. Assignment does not grant approval authority to a holder,
and Chief's global reach does not substitute for assignment.

Settlement is the owner-controlled action for an ordinary orchestrator. Chief cannot
settle. This one owner control does not replace the ordinary authority rule elsewhere.

## Speaking between supervisors

Existing ordinary orchestrator supervisors may send messages to one another through
`panels send-message --sprint-item ITEM_ID`. This permission is only for speaking. It
does not put either supervisor above the other or above the other's Workers. Approvals,
ceiling changes, assignments, and other writes still ask the ordinary authority rule.
Workers may speak to their own orchestrator and principals above them. They cannot use
Send Message or a help-recipient override to reach an unrelated supervisor.

## Your own record is not authority

A principal writes its own record through its own operations, and that is a separate
question from the rule. A Ticket proposes, recaps, notes and asks for help. A Sprint Item
writes its own body and artifacts. Neither is standing above anything, so the chain does
not decide it — being the thing does.

**Proposing is not a permission at all.** It is the Ticket speaking about its own work.
Nobody proposes on a Ticket's behalf, including Khushal.

## The one exception

**No principal may move a thing out of its own chain.** Changing a Ticket's Sprint Item
changes who stands above it, which is handing authority around rather than using it, and
the rule cannot refuse it: at the moment of the call the Ticket really is below the
Sprint Item that is moving it away. Re-parenting stays with Khushal and the Chief.

That is asked of the caller alone, at both doors that write the column — editing the
Ticket, and Sprint Item membership. It includes the Ticket itself. A Ticket that could set
its own Sprint Item could leave one, or leave every Sprint Item, and so choose who is allowed to
act on it. Being a thing does not include choosing who stands above you.

Deletion gets no special case: if you stand above a thing you may delete it.

## Making a Ticket

Making a Ticket acts on nothing that exists yet, so nobody is refused for making one. What
the new Ticket arrives with is a different matter, and each part is asked about on its own.

**Its Sprint Item.** A created thing belongs to its creator's chain. You can name a
Sprint Item you stand above, a Sprint Item you are, or the one you are under. Putting
work under the Sprint Item you already answer to adds nothing to your reach. The
supervisor that gains a child already stands above you. Naming any other Sprint Item
borrows a position you do not hold, and that is refused. This is not the exception
above: nothing is moved out of a chain, because the Ticket was in none.

**Its ceiling, and who holds it.** These are the two values editing a Ticket reserves for a
caller above it, so stating either one at creation takes the same answer. Khushal, the
Chief, and the Sprint Item the Ticket will sit under can state them. A Worker that spins off a
Ticket cannot, so the new Ticket gets its Worker type's ordinary ceiling and parks its first
field instead of running to the end.

Who holds a ceiling is an address, not a permission. A Ticket can be addressed to its own
Worker, to a Sprint Item, or to Khushal, and holding one grants nothing: the approval door
asks the rule again, of whoever turns up. So the question at creation is only who may
choose that address, and the answer is the one above.

## What this is not

The rule decides what a caller may do. It does not decide who a caller is. A position is
a truthful local claim, exactly as it was before: the CLI and the browser say who they
are, and Panels believes them. What changed is that the claim must now name the thing
being acted on.

Reads are open. `GET /api/tickets` and `GET /api/items` ask the rule nothing, so an
address that only reads is not protecting anything and never was.

_Code paths:_ `src/planner/core/authority/` (the rule and the explicit assignment chain), `src/planner/core/authctx.py` (turning a request into a principal).

---

_Last verified: 2026-09-21._
