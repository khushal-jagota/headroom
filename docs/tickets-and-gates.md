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
        └────────────  direct resolution may jump between worker stages only  ────────────────┘
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
point through a direct product operation. Direct operations can also jump between
worker stages, but cannot bypass an unresolved Kickoff or re-enter Kickoff; workers
never jump stages.

The **Kickoff field** preserves intake context: the user's original wording, source
context, boundaries, and advice. It stays readable beside the work so agents can
honor the user's direction without mixing that direction into success, approach,
plan, implementation, or closeout. After Kickoff is settled, later direct title edits
remain ordinary Ticket metadata edits, and Kickoff text edits use the ordinary field
value path.

_Code paths:_ `src/planner/tickets/` (the Ticket Stage and its fields).

### The Ticket's one conversation

A Ticket points at one conversation, and everything reaches the worker through it:
worker steps Panels starts on its own, what the human types in the pane, and revision
guidance sent back from Review. Starting a fresh one is deliberate — it stops the old
worker outright and discards anything it was still holding — and the new one starts from
the Ticket's last-chosen backend, model and reasoning.

The conversation system owns the transcript. Panels keeps no second message or
active-turn table. Pending worker context reaches the worker only when it is included
in a message that was actually sent — never because a row was written somewhere.

### Confirmed Worker failures

A Ticket becomes `errored` only when the active backend Worker reports a concrete
failure. The Ticket stores that exact text in `backend_error`, returns it through both
Ticket and Board reads, and shows it plainly on the Ticket page. The status and reason
are one fact: every transition to a non-error status clears the reason in the same
database write.

A delivery that never got through is not that. When Panels cannot get a step to the
worker at all, it simply gives back the claim it took and the Ticket goes back to rest —
nothing is recorded as an error. Workspace uses only the Ticket's canonical
`backend_error` to choose its exceptional treatment.

### Work completed outside Panels

When work was completed elsewhere, the Chief can reconcile an existing ticket or create
one already populated through the explicit `panels chief` external-work commands. This
is not a worker proposal and not a general Stage bypass. The operation requires a
complete Kickoff field value, an exact settled-field prefix for the target Stage, and a Chief
request. It refuses backward moves, pending proposals, active ticket control, and a
worker that is mid-turn. It moves the ceiling to the imported Stage but preserves the Ticket's
at-cap choice: an explicit **Stop** remains Stop; otherwise **Continue** remains. The
target Stage's effective ownership then determines whether the Ticket rests ready for
the worker, with the user, or paired.

The create or reconciliation writer commits all fields, Kickoff value, recap, Stage,
scope, and ownership-derived resting status together. A validation or concurrency
failure leaves the ticket exactly as it was. Committing is itself what tells the
readiness loop to look again.

An unparented backlog Ticket may point at a project by `project_id`. A Ticket under a
Sprint Item derives its Project and effective sprint from that item. Ticket responses
expose `sprint_item_id`, `effective_sprint_id`, and `resolved_priority_anchors`; they do
not expose or accept a direct Ticket `sprint_id`. The resolved anchors name the Sprint
Item and Project, with each anchor's priority state, so callers can explain the context
used at creation. The `project` display name remains in responses for compatibility.

If creation does not supply a Ticket priority, Panels uses the Sprint Item priority
when the Ticket has an item, otherwise the assessed Project priority, otherwise P3. An
explicit P0–P3 always wins. This is a creation default only: anchor priorities do not
cap, calculate, or later rewrite the Ticket's stored priority.

### Blockers

An ordinary Ticket create and a Chief external-work Ticket create may name any number
of existing blocker Ticket ids. Panels creates the dependent Ticket and every directed
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

### Ordinary Ticket edits

One ordinary edit may change a Ticket's title, priority, deadline, and project
together. Sprint placement belongs to the Sprint Item writer. Panels checks the whole
request before saving any of it. All requested
changes succeed together or none do, and the history records
only fields that really changed. Sending values the Ticket already has leaves it
unchanged.

### Who owns the current Stage

Every non-terminal Stage has a default owner: **worker**, **user**, or **paired**. The
Worker type supplies the starting value, and the Workers screen can change that value for
future Stage entries. When a Ticket enters a Stage, Panels stores the default in that Ticket.
Later changes therefore do not move work already resting there. A Ticket may also override
the stored default for a particular Stage. The current Stage's override wins; without one,
the stored default applies. Terminal Tickets have no current owner.

- **Worker-owned** Stages rest at `empty`, ready for Panels to start the next step —
  or at `blocked` while a live blocker remains. The other readiness, proposal, and
  scope conditions must still allow it.
- **User-owned** Stages rest at `user` and are never dispatched automatically. The user
  does the work, then the Chief records it through external-work reconciliation; there
  is no direct self-settle path.
- **Paired** Stages get one automatic opening turn when the Stage becomes ready, then
  rest at `paired`. Because only `empty` Tickets are started automatically, a Ticket
  resting at `paired` is never started again — the human conversation carries it from
  there, in the same Ticket conversation. A turn without a proposal leaves it at
  `paired`; a real proposal always parks for approval, regardless of scope.

**Take over** sets a `user` override for the current Stage, even while a worker step is
out. Nothing that step does afterwards can undo the takeover. **Release** clears the current
Stage override and reapplies the default captured when the Ticket entered that Stage; there
is no stack of older overrides. Moving to another Stage captures that Stage's current global
default, then applies any explicit Ticket override.

Ownership and scope answer different questions. Ownership says who drives the current
Stage. Scope says how far a worker may advance autonomously and what it may do at the
ceiling. The Worker type chooses the specialist skill used for that work.

_Code paths:_ `src/planner/tickets/logic/machine.py`, `src/planner/tickets/data.py`,
`src/planner/worker_settings/`, and `src/planner/tickets/api.py`.

## The one rule: proposals and the single door

Workers never change the record directly. A worker that wants to move work forward
files a **proposal** on the blank the current stage gates. The proposal resolver is
the only thing that can turn a proposal into a real value or advance the stage. Only
one proposal can be pending on a blank at a time — a newer one replaces the older,
and the replacement is recorded.

Each field also has a **field user note**. It is step-specific user guidance, not
agent scratchpad and not a canonical value. A worker may write one when the user gives
guidance that should survive for the relevant step.

The **recap** is different from both kinds of user note. It is a short cold-reader
orientation line that works beside the title: what the ticket is, where it stands now,
and the key fact for the current step. It is not a detailed log. It can be written or
updated at any stage — recap is never gated.

_Code paths:_ `src/planner/tickets/logic/resolution.py` (the proposal resolver), `src/planner/tickets/data.py`.

## How far a worker may go: the scope

Every ticket carries a permission with two parts — together, its **scope**:

- **The ceiling** — how far along the stages a worker may push this ticket on its own.
- **At the cap** — what a worker may do once the ticket reaches that ceiling: either
  **Stop** (don't even suggest anything) or **Continue** (draft the next step and park
  it for approval). Continue keeps the stored `propose` value and its existing behavior.

Below the ceiling, a worker-owned Stage's proposal is accepted automatically and the
ticket advances. A paired Stage's proposal always parks instead. At the ceiling, the
at-cap rule decides whether a worker-owned Stage may propose. User-owned Stages are not
automatically dispatched; paired Stages only get their opening turn. New tickets start leashed right at
**Kickoff**: the ceiling is `needs_kickoff` for every Worker type, so nothing advances past
the human-approved intake until the human grants scope onward — review before agents
start. Every later stage behaves the same way, including the last two: an accepted
implementation advances to **needs closeout**, and an accepted closeout advances
straight to **done**. (The threshold used by sprint-in-progress behavior is the
*second* stage, held distinct from this start ceiling; see `worker-types.md`.)

## The approval gate and Ticket leash

Whenever the human approves a step, they must say in the same breath how far the
worker may go next — the system refuses an approval that doesn't answer that
question. The Ticket details disclosure shows the same scope as a readable leash:
"approved until [a stage], then continue" or "then stop." Opening it reveals selects
for the ceiling and at-cap action, plus Take over or Release. A fresh approval starts
on Continue so the worker
keeps drafting the next gated step unless the human changes it. At Kickoff, an unchosen
ceiling starts from that Worker type's managed suggestion. Other approvals start from
their normal next Stage. `No further` remains a one-off choice. The stages it offers
are always the current one and the
ones after it, never an earlier one, so you can't hand back ground the ticket has
already covered. One shared source of the allowed stages feeds both the Ticket leash
and the approval screen, so the two cannot disagree.

Review's single, oldest-first walk shows today's tickets whose status is
`awaiting_approval` or `needs_user` — nothing else decides membership. A parked
proposal keeps its approval and revision controls. A Worker help request uses the same
Ticket title, Skip, and Open Ticket structure without proposal controls; the answer
belongs in the Ticket conversation. Either kind leaves Review the moment its status
changes, whichever way that happens.

Replying to the worker is one of those ways. A ticket parked on a proposal is waiting
for you, and typing an answer into its conversation is an answer of a kind — the
proposal is being discussed rather than approved — so the ticket moves to `paired` and
leaves Review. It moves when the message has actually reached the conversation: a reply
that got nowhere is not a reply. Only a person can do this. The automatic loop sends into
the same conversation, and its prompts are not replies.

The Review screen can also send a ticket back instead of accepting it, whatever field
is currently gated. The human writes short guidance in the review card. Panels
sends that guidance as the real next message into the Ticket's conversation, and only
then hands the Ticket back to the worker — that order matters, because the hand-back
deletes the pending proposal and could not be honestly undone if the send had failed.
A refused send changes nothing and can simply be retried. The ticket's stage never changes: a pending
gated proposal is cleared, settled values remain, and the ticket leaves Review while
its control status is `agent`. The gated field can therefore be revised
while the ticket remains at its current stage; it returns to Review when the worker
submits the revision.

_Code paths:_ `web/src/routes/TicketRoute.svelte` (the Ticket leash),
`web/src/lib/ui.ts` (the shared ceiling options), `web/src/routes/ReviewRoute.svelte`
(the approval walk).

## Permanent deletion

Dropping a ticket keeps its record. Permanent deletion is different: it is a
direct-only capability for a ticket created by mistake. The ticket UI intentionally
has no delete control; deletion remains a manual API or CLI operation, and the CLI
requires `--yes`. It is refused while the Ticket's status says a worker step is out,
and also while its conversation has a turn running. One transaction removes the ticket
from days, sprint views, links, Review, Workspace, and pending worker context. Other
tickets and day ordering stay intact.

Blocker links are removed in the same transaction, and the delete response lists the
surviving Ticket and Sprint-item endpoints those links pointed at.

The conversation itself lives outside Panels' record and is not erased, but nothing in
Panels points at it any more.

The whole deletion is one transaction, so it announces one change — not one per removed
day or link.

_Code paths:_ `src/planner/tickets/data.py`, `src/planner/tickets/api.py`,
`src/planner/cli/main.py`.

## Handoffs

- **Worker types** (`worker-types.md`) — the registry that declares this Ticket's Stage
  set, its gates, fields, default ownership, and worker. The six Stages above are the
  `coding` Worker type's.
- **Worker orchestration** (`worker-orchestration.md`) — how the worker that files
  these proposals gets asked to take the next step; committing a write is what tells the
  readiness loop to look again at once.
- **The command-line tool** (`cli.md`) — how a worker files proposals, recaps, and
  notes. The direct `ticket approve` command exists, but the `worker` subgroup and a
  Worker identity hold no approval verb or authority.
- **The front end** (`frontend.md`) — the Ticket, Review, and Board screens that
  render a ticket's story and carry the human's decisions.
- **Projects** (`projects.md`) — the catalog used directly by backlog Tickets and
  inherited through Sprint Items by scheduled Tickets.

## Deferred

- **Ideas → tickets** has no in-place promotion path. Trigger: a product decision
  that an existing idea should convert directly into a Ticket.

---

_Last verified: 2026-08-09._
