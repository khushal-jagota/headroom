# Tickets & the gates

A ticket is one piece of work small enough to hand to a single AI worker. It is the
correctness heart of the planner: everything about _how far a worker may go on its
own_ and _what waits for the human_ is decided here. A ticket moves through its stages
by filling one blank at a time, and no value ever becomes real except through one
door — the proposal resolver.

The Stage set is not fixed for all Tickets — it is declared by the Ticket's **Worker
type** (see `worker-types.md`). The lifecycle below is the **`coding`** Worker type's,
shown here as one concrete example; another Worker type walks its own Stages the same
way. Every Worker type shares the leading Brief, the `done` ending, and the
single-door rule.

```
   THE CODING STAGES

   Brief   ──► Success   ──► What     ──► Plan     ──► Implementation ──► Consequences ──► Done
   approve     Condition     Changes      (step-       (do the work,      (merge,
   intake      (what is      (how,        by-step)     propose a          deploy,
   context      "done"?)     roughly)                  reviewable         follow-up,
   field                                               package)           report)
```

## The Stages (the coding Worker type)

Every Worker type starts with a **Brief** and ends at **done**; the
Stages between are the Worker type's own. What follows is the `coding` lifecycle.

A ticket starts with its **Brief**. The Brief is the first ordinary Ticket field: the
human-approved intake context. The title is separate editable Ticket metadata, not
part of the Brief proposal. A new ordinary ticket parks a Brief field proposal
for review before any worker turn can start. Approving the Brief settles the Brief
field, then the ticket enters the worker stages.

The Ticket also stores the Worker, Model, and Reasoning its conversation runs on.
Creation copies the Worker type's three starting values, and they are kept up to date
afterwards, so a fresh conversation starts from where the last one ended. The Brief
section shows the editable controls beside approval only while the Brief is pristine and
no conversation exists yet. Hermes has no Reasoning control; Codex and Claude Code show
the Reasoning values supported by the selected model. Advancing the Brief or starting the
Ticket's first conversation removes the controls.

After the Brief, a ticket fills its blanks in order: a **Success Condition** (what
does done mean?), **What Changes** (how, roughly?), a **Plan** (concretely, step by
step), then
**Implementation** (the plan is carried out and a reviewable work package is
proposed), then **Consequences** (only the applicable merge, deploy, follow-up, and
bookkeeping happen, and a verified report is proposed), and finally it is **done**.
Each stage has exactly one blank to fill; filling it — and having that accepted — is
what moves the ticket one stage forward. **done** is the only ending a Ticket has.
Stages advance through approval or direct completion of a current user-owned gate;
there is no arbitrary Stage jump.

The **Brief field** preserves intake context: the user's original wording, source
context, boundaries, and advice. It stays readable beside the work so agents can
honor the user's direction without mixing that direction into Success Condition,
What Changes, Plan, Implementation, or Consequences. After the Brief is settled, later
direct title edits remain ordinary Ticket metadata edits, and Brief text edits use the
ordinary field value path.

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
active-turn table. A worker sees only what a delivered prompt contained. There is no
second store of context waiting to be picked up.

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
Then use ordinary field-value, recap, ceiling, placement, and Day operations.

A direct user can settle only the unset gate of the current user-owned Stage. That one
transaction stores the value, advances one Stage through the canonical transition, sets
the entered Stage as the ceiling, and keeps the direct principal as holder. Pending
proposals, active work, future fields, and worker-owned Stages reject this path.
There is no bulk prefix import or arbitrary Stage jump.

### Blockers

An ordinary Ticket create may name any number of existing blocker Ticket ids. Panels
creates the dependent Ticket and every Ticket block in one transaction. A missing,
invalid, or repeated blocker rejects the whole create with a structured error. Nothing is saved. A successful create commits
once, so readiness is nudged once.

A blocker is **live** while the Ticket doing the blocking is not done.
Blocking shows up in exactly one place: the dependent Ticket's status. When a Ticket is
at rest with nothing running, it reads as **blocked** instead of **empty** if a live
blocker remains. Only rest is called blocked, so a Ticket that is running a step or
waiting for approval reads as that instead. Blocking still writes no Blocked Stage and
changes nothing about the Ticket's real Stage, ownership, ceiling, proposal, or direct
user controls. It only keeps automatic work from starting, because the runtime starts
resting Tickets and nothing else.

Nothing is written to clear it. When a blocking Ticket is finished or deleted, or a
Ticket block is removed, that same write removes the blocks it held, and
every Ticket it was blocking stops reading as blocked from that moment. A Ticket that
another live blocker still holds keeps reading as blocked. There is no stored value to
repair, because the answer is worked out each time it is asked for.

A newly created dependent Ticket needs no first moment of rest before its blockers
count. The create commits the Ticket and its blocks together, and every read works the
answer out again. A Ticket whose stated ceiling accepts its Brief inside the create reads
as `blocked` at once. A Ticket whose Brief parks reads as `awaiting_approval` until that
is settled, because a parked proposal outranks a blocker, and as `blocked` from then on.
On the Workspace screen a blocked Ticket sits in the **Blocked** group, which starts
collapsed.

Ticket detail shows only direct blockers that are active now. Each row links to the
blocker and can remove that Ticket block, during the Brief or later. The section is
absent when no active blocker remains. Panels does not show reverse, cleared, transitive, or
graph views.

Any Ticket worker can add or remove Ticket blocks. The worker must send its own
existing Ticket id with its worker identity. Direct callers keep the same access. The Ticket block writer validates both Tickets, rejects active cycles, and commits the
complete change once. It writes nothing to the blocked Ticket, because what that Ticket
reads as is worked out from the block itself.

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
  or at `blocked` while a live blocker remains. The other readiness and proposal
  conditions must still allow it.
- **User-owned** Stages get one automatic opening turn when the Stage becomes ready, then
  rest at `empty`. A durable opener fact belongs to that Stage entry, and readiness
  checks it before dispatch. The user and worker carry the Stage forward in the same
  Ticket conversation. A real proposal always parks for approval, whatever the ceiling.
  The user can also fill the unset current field through the gate completion operation.
  Panels stores that value and advances exactly one Stage. Leaving the Stage clears the
  opener fact.

Ownership and the ceiling answer different questions. Ownership says who drives the
current Stage. The ceiling says how far a worker may advance on its own; at the ceiling it
proposes and waits, always. The Worker type chooses the specialist skill for that work.

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

A parked proposal has exactly two outcomes: it is approved, or it is rejected. There is
no third way to change it while it waits. A reader who wants different text approves the
proposal with their own text in place of the author's, which is part of approving it, or
rejects it and says what is wrong.

Nothing keeps a withdrawn draft. A rejected proposal is gone, and deleting a Ticket
takes whatever was parked on it.

Each Ticket has one **guidance** document for durable user corrections and constraints.
It is separate from settled field values and is never approved as a proposal. Review
shows this document, while the Ticket page does not. The CLI reads it with
`panels ticket show guidance` and writes it with `panels ticket edit --input-json -`
using `guidance` or `guidance_append`. Ticket search and supervisor
context include it too.

No Worker step prompt carries this document. The Stage instruction names the command
that reads it, and the Worker reads it at the start of every step. A direct edit keeps
the existing generic Ticket-changed notice. Saving guidance is not an immediate
conversation intervention: ordinary chat and rejection keep their existing send
behavior.

The **recap** is a short cold-reader
orientation line that works beside the title: what the ticket is, where it stands now,
and the key fact for the current step. It is not a detailed log. It can be written or
updated at any stage — recap is never gated.

_Code paths:_ `src/planner/tickets/logic/resolution.py` (the proposal resolver), `src/planner/tickets/data.py`.

## How far a worker may go: the ceiling

Every ticket carries a **ceiling**, plus a holder for it:

- **The ceiling** — the last thing a worker is allowed to do on this ticket on its own.
  The worker does that thing, proposes it, and waits.
- **The holder** — who the proposal at that ceiling is addressed to.

The holder is an address. It says who a parked proposal is for, and that is what Review,
the attention marks and the needs-approval notification read. It does not say who may
decide: that is the one rule, in `authority.md`, and anyone standing above the Ticket can
decide a proposal whoever holds it.

The holder is a full principal kind and ID, not a display label or current conversation.
A Ticket cannot hold its own ceiling: its Worker is the proposal author, so addressing a
proposal to itself addresses it to nobody. Holder Tickets and Sprint Items must exist
when the ceiling is written.

**The holder must stand above the Ticket.** An address that cannot decide is not an
address. Authority comes from the Ticket's current Sprint Item, so moving a Ticket to
another Sprint Item — or out of every Sprint Item — can leave the Sprint Item that holds
the ceiling below it.
When a move does that, the ceiling returns to the user in the same transaction, and the
Ticket's guidance gains one line naming the move that sent it back. The user stands above
everything, so the ceiling always has somewhere valid to go, and only the user or the
Chief can move a Ticket between Sprint Items in the first place. It is the same question on
every move, whether or not a proposal is parked right now: a wrong address is wrong before
anything arrives at it. A holder that still stands above the moved Ticket keeps the
ceiling. A Ticket that was already stranded stays as it is until something moves it.

Below the ceiling, a worker-owned Stage's answer settles the field and the ticket
advances, and no proposal is recorded at all. At the ceiling it files a proposal, and the
ticket parks. So every proposal in the system is one somebody is going to look at. The
ceiling says nothing about who owns a Stage: user-owned Stages still do not
dispatch automatically after their opening turn. They rest at `empty` with their opener fact,
and their answer always parks.
New tickets start leashed right at
the **Brief**: the ceiling is `needs_brief` for every Worker type, so nothing advances past
the human-approved intake until the human raises the ceiling — review before agents
start.

A Ticket created **without** a Brief parks nothing. A blank intake body means nothing was
written, not a Brief whose text is empty, so the Ticket rests at its Brief stage with the
worker-owned Kickoff ready to start. This is one rule at one door, so every way of creating
a Ticket gets it: the API defaults an absent intake body to blank, and so does a form field
nobody typed in.

A creator can state the ceiling instead, at creation, with `ticket create --ceiling`.
The same breath names who holds it, with `ticket create --holder`. A creator can name any
holder, including the user, without holding anything itself — that is how a Ticket is
opened for somebody else to review. Name nobody and the creator holds it, which is the
ordinary case: a Sprint Item that opens a Ticket holds it.
Whoever was given the authority to set the ceiling says so in the same breath as
the Ticket, so work the user has already authorized does not sit waiting for a second
approval. The Brief is then judged by the stated ceiling exactly as a later proposal is:
it settles and the Ticket starts at the next Stage when the stated ceiling is past
the Brief, and it parks for approval otherwise. State nothing and the default leash holds,
which is the ordinary case for intake the human wants to sense-check.

### Changing a ceiling on a running Ticket

Both halves can change later, and they change separately.

- **How far it may go** — `ticket set <id> ceiling`. This is refused while a proposal is
  parked, because moving the ceiling under a filed proposal changes what was proposed.
  Setting it never moves the holder: raising or lowering a ceiling must not move a Ticket
  into somebody else's queue.
- **Who is asked** — `ticket set <id> ceiling-holder`. This is allowed while a proposal is
  parked. Re-addressing a parked proposal does not change what was proposed, so it is the
  way a proposal sitting in the wrong queue reaches the right one. Anyone standing above
  the Ticket can do it.

Everywhere tooling takes a holder — creation, either change, and approval — it takes one
`--holder`, and reads the kind from what it is given: `me`, `chief`, a Sprint Item id, or
a Ticket id. Approving without naming one keeps the ceiling where it is: you hold it.

Every later stage behaves the same way, including the last two: an accepted
**Implementation** advances to **Consequences**, and accepting Consequences advances
straight to **done**. (The threshold used by sprint-in-progress behavior is the
*second* stage, held distinct from this start ceiling; see `worker-types.md`.)

## The approval gate and Ticket leash

Anyone standing above the Ticket can decide a parked proposal, whoever it is addressed
to. Whenever they approve a step, they must name the next ceiling and holder. The system
refuses an approval that omits either. Approving is a ceiling-setting moment like any
other, so the approve row carries the same control the Ticket page does, and the approver
can hand the Ticket onward rather than only keeping it.

The Ticket details disclosure shows the same permission as a readable leash:
"Until [a stage] · then [who]", where who reads `me`, `Chief`, or the Ticket's Sprint Item
by its name. The stage name is the Worker type's own label, so a renamed stage reads
correctly with no code change. While a proposal is parked the disclosure drops its stage
select and keeps the holder one, which is the split refusal made visible. A Ticket can hold
another Ticket's ceiling and tooling can set that; it is not offered on screen.

The worker runs to the new ceiling and parks there for the named holder. At the Brief, an
unchosen ceiling starts from that Worker type's managed suggestion. Other approvals start
from their normal next Stage. `No further` remains a one-off choice. The stages it offers
are always the current one and the
ones after it, never an earlier one, so you can't hand back ground the ticket has
already covered. One shared source of the allowed stages feeds both the Ticket leash
and the approval screen, so the two cannot disagree.
While a proposal is pending, the leash drops its ceiling select and keeps its holder one.
The ceiling cannot change without silently changing what was proposed, and the proposal can
still be re-addressed.

Review's single, oldest-first walk shows today's owner-addressed proposals. A proposal
routed to a Sprint Item manager creates a durable manager wake. Other holders inspect
canonical Ticket state through their normal Chief and Ticket views. A parked proposal
keeps its approval and revision controls. A Worker
help request is an addressed conversation message. Its unread state feeds the shared
attention projection, and the answer belongs in that conversation.

Replying to the worker does not decide its proposal. The proposal stays pending and
addressed to its holder until a decision or a replacement proposal arrives. Manager wake
delivery never moves that address or decides the proposal. The one thing that returns a ceiling to the user is a
move that leaves its holder below the Ticket, described above. Owner-held proposals remain
on Review.

The Review screen can also send an owner-addressed ticket back instead of accepting it,
whatever field is currently gated. The owner writes short guidance in the review card.
The Ticket transaction validates authority and route. It stores the exact attributed
comment as one-use feedback for the current Stage, clears the pending proposal, and
returns the Ticket to its resting control status. Ticket guidance is unchanged. The next
normal worker-step prompt carries the feedback, which is consumed only after that prompt
is accepted. The ticket's stage never changes. Settled values remain. The gated field can
therefore be revised
while the ticket remains at its current stage; it returns to Review when the worker
submits the revision. Rejecting re-addresses the revision only when the owner does it:
the owner becomes the holder, so the revision comes back to him. Every other rejection
leaves the address exactly as it was.

There is one approval gate. Every parked proposal waits on `awaiting_approval` for the
principal it is addressed to. Anyone above the Ticket approves it or rejects it with
focused revision guidance, and either way the canonical proposal resolver does the work.
Review contains owner-addressed proposals; anyone else reads theirs on the Ticket.
The ceiling cannot change while a proposal waits, so what was proposed stays fixed.

Owner-held proposals appear in Review and produce the owner's needs-approval notification.
Anyone else reads proposals from canonical Ticket state through their normal Ticket and
Sprint Item views. Filing a manager-routed proposal creates a queued wake, not an owner alert.

_Code paths:_ `web/src/routes/TicketRoute.svelte` (the Ticket leash),
`web/src/lib/ui.ts` (the shared ceiling options), `web/src/routes/ReviewRoute.svelte`
(the approval walk), `web/src/components/ReviewProposalCard.svelte` (one waiting
proposal, shared by every screen that shows one).

## Permanent deletion

A finished ticket keeps its record. Permanent deletion is different: it is for a ticket
created by mistake. Deleting follows the one rule like everything else: if you stand
above a ticket, you may delete it. The ticket UI intentionally has no delete control;
deletion remains a manual API or CLI operation, and the CLI requires `--yes`.

The user's ordinary delete is refused while the Ticket's status says a worker step is
out, and also while its conversation has a turn running. `--force` deletes it anyway. A
Sprint Item can delete its own child Ticket without that activity guard. No actor can
delete a Ticket or Sprint Item that is the ceiling holder for another Ticket. Any delete
that goes ahead over a running worker kills that worker's turn first, so nothing keeps
talking into a conversation whose ticket is gone.

One transaction removes the ticket
from days, sprint views, Ticket blocks, Review, and Workspace. Other tickets and day
ordering stay intact.

Ticket blocks are removed in the same transaction. The delete response lists the
surviving Tickets from those relationships.

The conversations live outside the Ticket record and are not erased. Deletion removes
their Ticket associations, so Panels no longer assigns those transcripts to that Ticket.

The whole deletion is one transaction, so it announces one change — not one per removed
day or Ticket block.

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
