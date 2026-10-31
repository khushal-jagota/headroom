# Who may act

One sentence decides every guarded call: you may act on anything strictly below you.

Khushal, Chief, a Manager, and a Ticket’s Worker all call the same route. Who they are decides whether the call is admitted. There is no second address reserved for agents, and the general rule does not carry a list of principal kinds that are allowed through a particular door.

## Position

```text
Khushal ─┬─ everything
Chief ───┘

Manager ─ its current child Tickets

Ticket ─── nothing
```

A Manager stands above a Ticket because the Ticket’s assignment says so. Move the Ticket, and the previous Manager no longer stands above it. A Ticket stands above nothing, so its Worker cannot decide its own proposal. That is the rule answering, not an extra ban written beside it.

Retired planning types keep their history. They do not regain a shared-plan write through the old type name.

## Your own record is a different question

A Ticket proposes, recaps, and asks for help. A Manager writes its goal, recap, and artifacts. Those are the principal acting as itself. The chain does not decide them. Proposing is the Ticket speaking about its own work. Nobody, including Khushal, proposes on a Ticket’s behalf.

A goal agent acts on its own goal, page, completion, active state, and managed files. Source and measurement permissions remain separate. A goal stands above no Ticket and cannot hold a Ticket review. Goal agents ask Chief to route Ticket work.

## The re-parent exception

No principal may move a Ticket out of its chain except Khushal and Chief. Changing the Manager changes who stands above the Ticket. At the moment of the call the Ticket really is below the Manager trying to hand it away, so the ordinary rule would allow the handoff. The exception exists so authority cannot be given away by the person who currently holds it. A Ticket cannot set its own Manager either. Being the Ticket does not include choosing who stands above you.

Deletion has no special case. If you stand above a thing, you may delete it. The Ticket page still hides delete. The command and the API enforce the rule.

## Making a Ticket, and holding a review

Making a Ticket acts on nothing that exists yet, so the rule does not refuse creation. What the new Ticket arrives with is asked piece by piece. You can put it under a Manager you stand above, a Manager you are, or the Manager you are under. Workers that spin up child Tickets cannot set the ceiling and holder at creation. Someone above the new Ticket does that.

The holder of a parked proposal must be Khushal, Chief for a Chief-assigned Ticket, or the assigned Manager. Anyone strictly above the Ticket may approve. Holding and approving are different. An assignment move that does not name a new holder returns the holder to Khushal when the old holder would no longer be legal.

## Schedules, skills, and settlement

Schedules, skills, Worker types, and projects are owner-level configuration. Khushal and Chief stand above them. Managers and Workers do not gain schedule control by being assigned the Tickets a schedule will create. A Manager’s Project edit does update the Project on schedules already assigned to that Manager. That derived write changes the Project and the timestamp. It does not enable a schedule or rewrite the rest of the template.

Settlement is tighter than the general rule. Only the owner may settle or reopen an ordinary Manager. Chief cannot settle, and Chief can never be settled. Settlement changes the rail. It does not stop children. See [Managers and Chief](managers.md).

## What this is not

The rule decides what a caller may do. It does not decide who the caller is. The browser and the CLI say who they are, and the server believes that claim. Update actions and other owner doors inherit that trust. They are not a login system.

Reads of the Ticket and Manager lists are open. A route that only reads is not protected by this rule.

## Where it shows up

A refused call is the product surface: a Worker that tries to approve, a Manager that tries to reassign a Ticket, a Worker that tries to edit a skill or request an update. Review, the Ticket gate, and Settings succeed for the principals the rule allows and fail for the rest with the same writers.

## Pointers

`src/planner/core/authority/logic.py` is the rule. `src/planner/core/authority/service.py` is the door routes call. `src/planner/core/authctx.py` turns a request into a principal.

_Last verified: 2026-10-30._
