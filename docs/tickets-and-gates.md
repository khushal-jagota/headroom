# Tickets & the gates

A ticket is one piece of work small enough to hand to a single AI worker. It is the
correctness heart of the planner: everything about _how far a worker may go on its
own_ and _what waits for the human_ is decided here. A ticket moves through fixed
stages by filling one blank at a time, and no value ever becomes real except through
one door — the resolution engine.

```
   THE STAGES (one blank fills each step)

   success  ──►  approach  ──►  plan  ──►  in progress  ──►  needs review  ──►  done
   condition     (how,          (step-      (the work         (result waits       │
   (what is       roughly)       by-step)    happens)          for checking)       │
    "done"?)                                                                        │
        └──────────────────  the human may jump a ticket anywhere  ─────────────────┘
                             dropped: any point, human only
```

## The stages

A ticket fills its blanks in order: a **success condition** (what does done mean?),
an **approach** (how, roughly?), a **plan** (concretely, step by step), then the work
happens (**in progress**), then the result waits for checking (**needs review**), and
finally it is **done**. Each stage has exactly one blank to fill; filling it — and
having that accepted — is what moves the ticket one stage forward. A ticket can also
be **dropped** at any point, only by the human. The human can always jump a ticket
anywhere; workers never can.

A ticket also has a **user note**. This is not one of the four blanks and it does not
advance the ticket. It preserves intake context: the user's original wording, source
context, boundaries, and advice. It stays readable beside the work so agents can honor
the user's direction without mixing that direction into success, approach, plan, or
result.

_Code paths:_ `src/planner/tickets/` (the ticket state and its fields).

Standalone tickets may point at a project by `project_id`. API responses also include
`project`, the display name, for compatibility. A ticket under a sprint item does not
store its own project because the parent item owns that classification.

## The one rule: proposals and the single door

Workers never change the record directly. A worker that wants to move work forward
files a **proposal** on the blank the current stage gates. The resolution engine is
the only thing that can turn a proposal into a real value or advance the stage. Only
one proposal can be pending on a blank at a time — a newer one replaces the older,
and the replacement is recorded.

Each field also has a **field user note**. It is step-specific user guidance, not
agent scratchpad and not a canonical value. A worker may write one when the user gives
guidance that should survive for the relevant step.

The **recap** is different from both kinds of user note. It is a short cold-reader
orientation line that works beside the title: what the ticket is, where it stands now,
and the key fact for the current step. It is not a detailed log.

_Code paths:_ `src/planner/core/loops.py` (the resolution engine), `src/planner/core/server.py`.

## How far a worker may go: the scope

Every ticket carries a permission with two parts — together, its **scope**:

- **The ceiling** — how far along the stages a worker may push this ticket on its own.
- **At the cap** — what a worker may do once the ticket reaches that ceiling: either
  **stop** (don't even suggest anything) or **propose** (draft the next step and park
  it for approval).

Below the ceiling, a worker's proposal is accepted automatically and the ticket
advances. At the ceiling, the at-cap rule decides. New tickets start with the
tightest sensible scope: the worker may draft a success condition, and nothing moves
without approval. One special ending: an accepted result goes to **needs review**
unless the ceiling was already **done**, in which case it lands straight in done.

## The approval gate, and the scope row

Whenever the human approves a step, they must say in the same breath how far the
worker may go next — the system refuses an approval that doesn't answer that
question. That same scope is shown and editable right on the ticket header as a plain
row: "approved until [a stage] then propose" — or "then stop", rendered as pills you
can tap to change any time. A fresh approval starts on "then propose" so the worker
keeps drafting the next gated step unless the human changes it. The stages it offers
are always the current one and the
ones after it, never an earlier one, so you can't hand back ground the ticket has
already covered. One shared source of the allowed stages feeds both the header row
and the approval screen, so the two can never disagree.

The Review screen can also send a ticket back instead of accepting it. The human
writes short guidance in the review card. Panels sends that guidance directly to the
ticket's existing Hermes session as the user message that starts a worker turn. It is
not copied into ticket chat and no later generic worker prompt is sent. The ticket's
stage never changes: a pending gated proposal is cleared, settled values remain, and
the ticket leaves Review while its control status is **agent running step**. A result
can therefore be revised while the ticket remains at **needs review**; it returns to
Review when the worker submits the revision.

_Code paths:_ `web/src/routes/TicketRoute.svelte` (the scope row),
`web/src/lib/ui.ts` (the shared ceiling options), `web/src/routes/ReviewRoute.svelte`
(the approval walk).

## Permanent deletion

Dropping a ticket keeps its record. Permanent deletion is different: it is a
human-only action for a ticket created by mistake. The action is blocked while
ticket activity is still running. After confirmation, one transaction removes the
ticket from days, sprint views, links, Review, Workspace, and Panels chat. Other
tickets and day ordering stay intact.

The deletion also replaces that ticket's old event history with one small deletion
record containing its identity, the human actor, and the time. This is the only
exception to normal append-only event history. The separate stored Hermes session is
outside Panels' record and is not erased; once the ticket row is gone, Panels no
longer has a route that resolves or resumes it.

_Code paths:_ `src/planner/tickets/data.py`, `src/planner/tickets/api.py`,
`web/src/routes/TicketRoute.svelte`.

## The event log

Every normal change writes a permanent line into the event log. The records it
describes _do_ change — a ticket's fields update, taking a ticket off a day's list
removes that link — but the event lines remain. A permanent ticket deletion is the
one deliberate exception: that ticket's old lines are replaced by its minimal
deletion audit. The front end treats the log as a doorbell, not as data: a new line
tells screens to refetch.

_Code paths:_ `src/planner/core/events.py`.

## Handoffs

- **The employee runtime** (`employee-runtime.md`) — the worker that files the
  proposals and does the drafting; the runtime pokes itself when an approval lands so
  the ticket advances at once.
- **The command-line tool** (`cli.md`) — how a worker files proposals, recaps, and
  notes; it deliberately holds no accept/approve/grant verb.
- **The front end** (`frontend.md`) — the Ticket, Review, and Board screens that
  render a ticket's story and carry the human's decisions.
- **Projects** (`projects.md`) — the catalog used by standalone ticket project fields.

## Deferred

- **Ideas → tickets** happens only through the day chat capturing new work, never by
  promoting an existing idea. No promote path exists. Trigger: a product decision
  that ideas should convert in place.

---

_Last verified: 2026-07-09._
