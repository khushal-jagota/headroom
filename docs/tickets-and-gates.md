# Tickets & the gates

A ticket is one piece of work small enough to hand to a single AI worker. It is the
correctness heart of the planner: everything about _how far a worker may go on its
own_ and _what waits for the human_ is decided here. A ticket moves through its stages
by filling one blank at a time, and no value ever becomes real except through one
door — the proposal resolver.

The Stage set is not fixed for all Tickets — it is declared by the Ticket's **Worker
type** (see `worker-types.md`). The lifecycle below is the **`coding`** Worker type's,
shown here as one concrete example; another Worker type walks its own Stages the same
way. Every Worker type shares the leading Kickoff, the `done`/`dropped` bookends, and
the single-door rule.

```
   THE CODING STAGES

   kickoff ──► success   ──►  approach  ──►  plan     ──►  implementation ──►  closeout ──► done
   approve     condition      (how,          (step-        (do the work,       (merge,
   intake      (what is        roughly)       by-step)      propose a           deploy,
   context      "done"?)                                    reviewable          follow-up,
   field                                                     package)            report)
                                   dropped: any point, direct operation only
```

## The Stages (the coding Worker type)

Every Worker type starts with **Kickoff** and ends at **done** (or **dropped**); the
Stages between are the Worker type's own. What follows is the `coding` lifecycle.

A ticket starts with **Kickoff**. Kickoff is the first ordinary Ticket field: the
human-approved intake context. The title is separate editable Ticket metadata, not
part of the Kickoff proposal. A new ordinary ticket parks a Kickoff field proposal
for review before any worker turn can start. Approving Kickoff settles the Kickoff
field, then the ticket enters the worker stages.

The Ticket also stores the Worker, Model, and Reasoning its conversation runs on.
Creation copies the Worker type's three starting values, and they are kept up to date
afterwards, so a fresh conversation starts from where the last one ended. The Kickoff
section shows the editable controls beside approval only while Kickoff is pristine and
no conversation exists yet. Hermes has no Reasoning control; Codex and Claude Code show
the Reasoning values supported by the selected model. Advancing Kickoff or starting the
Ticket's first conversation removes the controls.

After Kickoff, a ticket fills its blanks in order: a **success condition** (what
does done mean?), an **approach** (how, roughly?), a **plan** (concretely, step by
step), then
**implementation** (the plan is carried out and a reviewable work package is
proposed), then **closeout** (only the applicable merge, deploy, follow-up, and
bookkeeping happen, and a verified report is proposed), and finally it is **done**.
Each stage has exactly one blank to fill; filling it — and having that accepted — is
what moves the ticket one stage forward. A ticket can also be **dropped** at any
point through a direct product operation. Stages advance through approval or direct
completion of a current user-owned gate; there is no arbitrary Stage jump.

The **Kickoff field** preserves intake context: the user's original wording, source
context, boundaries, and advice. It stays readable beside the work so agents can
honor the user's direction without mixing that direction into success, approach,
plan, implementation, or closeout. After Kickoff is settled, later direct title edits
remain ordinary Ticket metadata edits, and Kickoff text edits use the ordinary field
value path.

_Code paths:_ `src/planner/tickets/` (the Ticket Stage and its fields).

### The Ticket's conversations

A Ticket points at one active conversation, and everything reaches the worker through it:
worker steps Panels starts on its own, what the human types in the pane, and revision
guidance sent back from Review. Starting a fresh one is deliberate — it stops the old
worker outright and discards anything it was still holding — and the new one starts from
the Ticket's last-chosen backend, model and reasoning.

The Ticket also keeps every conversation it has had. Reset clears only the active
pointer. The Ticket page shows only the active conversation, where new work can be sent.

The conversation system owns the transcript. Panels keeps no second message or
active-turn table. Pending worker context reaches the worker only when it is included
in a message that was actually sent — never because a row was written somewhere.

### Confirmed Worker failures

A Ticket can use `errored` as a durable marker that its Worker failed. The conversation
record keeps the failed turn and its detail. Operator logs keep the same failure for
diagnosis. The Ticket does not store a second copy of the error text.

A read or owner reply does not clear the error. Derived agent state also retains the
latest failed turn until a later start succeeds or an explicit restart resets it.
During the attention-state upgrade, Panels acknowledges failures older than 24 hours.
Newer failures and all later failures keep the normal persistent error behavior.

A refused Worker step gives its claim back. Workspace derives a failed Worker from the
Ticket status or the latest conversation turn.

### Work completed outside Panels

Work completed elsewhere uses the same ordinary Ticket operations as all other work.
Create a Ticket through the canonical creation action when no aligned Ticket exists.
Then use ordinary field-value, recap, scope, placement, and Day operations.

A direct user can settle only the unset gate of the current user-owned Stage. That one
transaction stores the value, advances one Stage through the canonical transition, sets
the entered Stage as the ceiling, and keeps the direct principal as holder. Pending
proposals, active work, future fields, and worker-owned Stages reject this path.
There is no bulk prefix import or arbitrary Stage jump.

### Blockers

An ordinary Ticket create may name any number of existing blocker Ticket ids. Panels
creates the dependent Ticket and every directed
`blocks` link in one transaction. A missing, invalid, or repeated blocker rejects the
whole create with a structured error. Nothing is saved. A successful create commits
once, so readiness is nudged once.

A blocker is **live** while the Ticket doing the blocking is neither done nor dropped.
Blocking shows up in exactly one place: the dependent Ticket's status. When a Ticket
comes to rest with nothing running, it lands on **blocked** instead of **empty** if a
live blocker remains. `blocked` only ever stands in for `empty`, so a Ticket that is
running a step, waiting for approval, asking for help, or held by the user keeps that
status untouched. Blocking still writes no Blocked Stage and changes nothing about the
Ticket's real Stage, ownership, scope, proposal, or direct user controls. It only keeps
automatic work from starting, because the runtime starts `empty` Tickets and nothing
else.

Clearing happens inside the action that removes the cause. When a blocking Ticket is
finished, dropped, or deleted, or a blocking link is removed, that same write deletes
the links it held and rewrites every Ticket it was blocking in the same transaction —
back to `empty`, or left on `blocked` when another live blocker remains. A Ticket that
stays blocked is not rewritten at all, so nothing is announced for a change that did
not happen. A Ticket may also block a Sprint item; a Sprint item carries no Ticket
status, so only Ticket targets are rewritten.

A newly created dependent Ticket parks its Kickoff proposal first, so it waits for
approval before it can rest anywhere. It becomes `blocked` the first time it comes to
rest with its blockers still live. On the Workspace screen a blocked Ticket then sits
in the **Blocked** group, which starts collapsed.

Ticket detail shows only direct blockers that are active now. Each row links to the
blocker and can remove that one link, during Kickoff or later. The section is absent
when no active blocker remains. Panels does not show reverse, cleared, transitive, or
graph views. A Ticket may still block a Sprint item through the same existing directed
link engine.

Any Ticket worker can add or remove these supported blocking links. The worker must
send its own existing Ticket id with its worker identity. Direct callers keep the same
access. The link writer still validates every endpoint, rejects active cycles, updates
blocked Ticket status, and commits the complete change once.

### Ordinary Ticket edits

One ordinary edit may change a Ticket's title, priority, deadline, Project, Sprint, and
optional Sprint Item together. Panels checks the whole request before saving any of it. All requested
changes succeed together or none do, and the history records
only fields that really changed. Sending values the Ticket already has leaves it
unchanged.

### Who owns the current Stage

Every non-terminal Stage has one declared owner: **worker** or **user**. The immutable
Worker type definition is the sole authority. Terminal Tickets have no current owner.

- **Worker-owned** Stages rest at `empty`, ready for Panels to start the next step —
  or at `blocked` while a live blocker remains. The other readiness, proposal, and
  scope conditions must still allow it.
- **User-owned** Stages get one automatic opening turn when the Stage becomes ready, then
  rest at `empty`. A durable opener fact belongs to that Stage entry, and readiness
  checks it before dispatch. The user and worker carry the Stage forward in the same
  Ticket conversation. A real proposal always parks for approval, regardless of scope.
  The user can also write the unset current field through the ordinary value operation.
  Panels stores that value and advances exactly one Stage. Leaving the Stage clears the
  opener fact.

Ownership and scope answer different questions. Ownership says who drives the current
Stage. Scope says how far a worker may advance autonomously and what it may do at the
ceiling. The Worker type chooses the specialist skill used for that work.

_Code paths:_ `src/planner/tickets/logic/machine.py`, `src/planner/tickets/data.py`,
and `src/planner/tickets/api.py`.

## The one rule: proposals and the single door

Workers never change settled values or advance Stages directly. Only a Ticket's own
Worker principal can file that Ticket's proposal; a supervisor, holder Ticket, or other
Worker cannot file on its behalf. A worker that wants to
move work forward files a **proposal** on the blank the current Stage gates. The
proposal resolver is the only thing that can turn a proposal into a real value or
advance the Stage. A Ticket has at most one pending proposal, always for its current
Stage. Filing another proposal replaces that pending draft. Saved field values remain
separate, including a saved value beside a pending revision of the same field.

The pending proposal has its own editor. Any authorized actor can replace its text.
The proposal stays pending and addressed to the same ceiling holder. The edit keeps its
author, creation time, settled value, Ticket status, Stage, and scope. Direct edits
of saved values use a separate editor and remain limited to passed fields and direct callers.

The archive keeps earlier unapproved drafts and text from retired fields. Dropping a
Ticket moves its pending draft here without approval. Historical text cannot be approved
or resumed. The Ticket page does not show the archive. The CLI exposes it as `archive`,
and copy text and search include it.

Each Ticket has one **guidance** document for durable user corrections and constraints.
It is separate from settled field values and is never approved as a proposal. Review
shows this document, while the Ticket page does not. The CLI reads it with
`panels worker my-ticket guidance` and writes it with `panels worker note <id>` from
stdin; `--append` preserves the existing text. Copy text, Ticket search, and supervisor
context include it too.

The next automatic Worker step includes current guidance in its actual prompt. A direct
edit keeps the existing generic Ticket-changed notice. Saving guidance is not an
immediate conversation intervention: ordinary chat and return-for-revision keep their
existing send behavior. A Worker continuing those conversations can read current
guidance from the Ticket.

The **recap** is a short cold-reader
orientation line that works beside the title: what the ticket is, where it stands now,
and the key fact for the current step. It is not a detailed log. It can be written or
updated at any stage — recap is never gated.

_Code paths:_ `src/planner/tickets/logic/resolution.py` (the proposal resolver), `src/planner/tickets/data.py`.

## How far a worker may go: the scope

Every ticket carries a **scope** with two controls, plus a holder for its ceiling:

- **The ceiling** — how far along the stages a worker may push this ticket on its own.
- **At the cap** — what the worker may do when it gets there. **Stop** prevents a
  proposal at all. **Propose** lets the worker file one, and that proposal parks for the
  holder's approval.
- **The holder** — the principal who can decide the proposal at that ceiling.

The holder is a full principal kind and ID, not a display label or current conversation.
Existing Tickets receive the owner principal when the holder column is introduced.
A Ticket cannot hold its own ceiling: its Worker is the proposal author, never its own
reviewer. Holder Tickets and Sprint Items must exist when the scope is written.

Below the ceiling, a worker-owned Stage's proposal is accepted automatically and the
ticket advances. At the ceiling, the cap decides whether a worker-owned Stage can propose
at all. The cap says nothing about who owns a Stage: user-owned Stages still do not
dispatch automatically after their opening turn. They rest at `empty` with their opener fact.
New tickets start leashed right at
**Kickoff**: the ceiling is `needs_kickoff` for every Worker type, so nothing advances past
the human-approved intake until the human grants scope onward — review before agents
start.

A creator can state the scope instead, at creation, with `ticket create --ceiling` and
`--at-cap`. Whoever was given the authority to grant scope says so in the same breath as
the Ticket, so work the user has already authorized does not sit waiting for a second
approval. The kickoff is then judged by the stated scope exactly as a later proposal is:
it settles and the Ticket starts at the next Stage when the stated ceiling is past
kickoff, and it parks for approval otherwise. State nothing and the default leash holds,
which is the ordinary case for intake the human wants to sense-check.

Every later stage behaves the same way, including the last two: an accepted
implementation advances to **needs closeout**, and an accepted closeout advances
straight to **done**. (The threshold used by sprint-in-progress behavior is the
*second* stage, held distinct from this start ceiling; see `worker-types.md`.)

## The approval gate and Ticket leash

The addressed holder or the owner can decide a parked proposal. Whenever either approves
a step, they must name the next ceiling, cap, and holder. The system refuses an approval
that omits any part. The Ticket details disclosure shows
the same scope as a readable leash:
"approved until [a stage], then [stop or propose]." The disclosure includes only the
ceiling and cap selects. A fresh approval starts on **Propose**, so
the worker runs to the new ceiling and parks there for the named holder unless **Stop** is
chosen instead. At Kickoff, an unchosen
ceiling starts from that Worker type's managed suggestion. Other approvals start from
their normal next Stage. `No further` remains a one-off choice. The stages it offers
are always the current one and the
ones after it, never an earlier one, so you can't hand back ground the ticket has
already covered. One shared source of the allowed stages feeds both the Ticket leash
and the approval screen, so the two cannot disagree.
While a proposal is pending, the Ticket page hides the leash because scope cannot change
without silently changing the proposal's stable address.

Review's single, oldest-first walk shows today's owner-addressed proposals. Non-owner
holders inspect canonical Ticket state through their normal Chief, Sprint Item, and Ticket
views; no proposal wake, retry, failure surfacing, or owner fallback remains.
A parked proposal keeps its approval and revision controls. A Worker help request is an
addressed conversation message. Its unread state feeds the shared attention projection,
and the answer belongs in that conversation.

Replying to the worker does not decide its proposal. The proposal stays pending and
addressed to its holder until a decision or a replacement proposal arrives. A non-owner
holder inspects the canonical Ticket through the normal Chief, Sprint Item, and Ticket
views. Panels does not wake the holder, retry proposal delivery, surface a delivery
failure, or fall back to the owner. Owner-held proposals remain on Review.

The Review screen can also send an owner-addressed ticket back instead of accepting it,
whatever field is currently gated. The owner writes short guidance in the review card.
The Ticket transaction validates authority and route. It stores the exact attributed
comment as one-use feedback for the current Stage, clears the pending proposal, and
returns the Ticket to its resting control status. Ticket guidance is unchanged. The next
normal worker-step prompt carries the feedback, which is consumed only after that prompt
is accepted. The ticket's stage never changes. Settled values remain. The gated field can
therefore be revised
while the ticket remains at its current stage; it returns to Review when the worker
submits the revision. A holder rejection keeps that holder. If the owner uses the
override, the owner becomes the ceiling holder for the revised proposal.

There is one approval gate. Every parked proposal waits on `awaiting_approval` for its
holder. The holder or owner approves it or rejects it with focused revision guidance,
and either way the canonical proposal resolver does the work. Review contains
owner-addressed proposals. Other holders use their scoped controls.
Scope cannot change while a proposal waits, so its address stays stable.

Owner-held proposals appear in Review and produce the owner's needs-approval notification.
Other holders read proposals from canonical Ticket state through their normal Ticket and
supervisor views. Filing a proposal does not send a separate alert.

_Code paths:_ `web/src/routes/TicketRoute.svelte` (the Ticket leash),
`web/src/lib/ui.ts` (the shared ceiling options), `web/src/routes/ReviewRoute.svelte`
(the approval walk), `web/src/components/ReviewProposalCard.svelte` (one waiting
proposal, shared by every screen that shows one).

## Permanent deletion

Dropping a ticket keeps its record. Permanent deletion is different: it is for a ticket
created by mistake. The user deletes any ticket, and a Sprint Item supervisor deletes a
current child ticket of its own item. Nobody else can. The ticket UI intentionally
has no delete control; deletion remains a manual API or CLI operation, and the CLI
requires `--yes`.

The user's ordinary delete is refused while the Ticket's status says a worker step is
out, and also while its conversation has a turn running. `--force` deletes it anyway. A
supervisor can delete its own child Ticket without that activity guard. No actor can
delete a Ticket or Sprint Item that is the ceiling holder for another Ticket. Any delete
that goes ahead over a running worker kills that worker's turn first, so nothing keeps
talking into a conversation whose ticket is gone.

One transaction removes the ticket
from days, sprint views, links, Review, Workspace, and pending worker context. Other
tickets and day ordering stay intact.

Blocker links are removed in the same transaction, and the delete response lists the
surviving Ticket and Sprint-item endpoints those links pointed at.

The conversations live outside the Ticket record and are not erased. Deletion removes
their Ticket associations, so Panels no longer assigns those transcripts to that Ticket.

The whole deletion is one transaction, so it announces one change — not one per removed
day or link.

_Code paths:_ `src/planner/tickets/data.py`, `src/planner/tickets/api.py`,
`src/planner/cli/main.py`.

## Handoffs

- **Worker types** (`worker-types.md`) — the registry that declares this Ticket's Stage
  set, its gates, fields, ownership, and worker. The six Stages above are the
  `coding` Worker type's.
- **Worker orchestration** (`worker-orchestration.md`) — how the worker that files
  these proposals gets asked to take the next step; committing a write is what tells the
  readiness loop to look again at once.
- **The command-line tool** (`cli.md`) — how a worker files proposals, recaps, and
  notes. The direct `ticket approve` command exists, but the `worker` subgroup and a
  Worker identity hold no approval verb or authority.
- **The front end** (`frontend.md`) — the Ticket, Review, and Board screens that
  render a ticket's story and carry the human's decisions.
- **Projects** (`projects.md`) — the catalog that Tickets, Sprint Items, and Ideas use
  directly.

## Deferred

- **Ideas → tickets** has no in-place promotion path. Trigger: a product decision
  that an existing idea should convert directly into a Ticket.

---

_Last verified: 2026-08-14._
