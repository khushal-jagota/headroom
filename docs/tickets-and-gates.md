# Tickets & the gates

A ticket is one piece of work small enough to hand to a single AI worker. It is the
correctness heart of the planner: everything about _how far a worker may go on its
own_ and _what waits for the human_ is decided here. A ticket moves through its stages
by filling one blank at a time, and no value ever becomes real except through one
door — the resolution engine.

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

### Work completed outside Panels

When work was completed elsewhere, the Chief can reconcile an existing ticket or create
one already populated through the explicit `panels chief` external-work commands. This
is not a worker proposal and not a general Stage bypass. The operation requires a
complete Kickoff field value, an exact settled-field prefix for the target Stage, and a Chief
request. It refuses backward moves, pending proposals, active ticket control, and
running chat turns. The resulting scope stops at the imported Stage, so the worker does
not continue automatically.

The create or reconciliation writer commits all fields, Kickoff value, recap, Stage, scope,
status normalization, and existing event signals together. A validation or concurrency
failure leaves both the ticket and its event history unchanged. The surrounding action
commits before it calls the best-effort Automatic Employee-step eligibility wake after
a new imported Ticket or a real reconciliation change. An exact replay does not wake
discovery; normalizing `errored` back to `empty` is a real change and does.

Standalone tickets may point at a project by `project_id`. API responses also include
`project`, the display name, for compatibility. A ticket under a sprint item does not
store its own project because the parent item owns that classification.

### Ordinary Ticket edits

One ordinary edit may change a Ticket's title, priority, deadline, project,
sprint, and implementer together. The implementer is nullable and limited to four fixed
assignments: Khushal, Panels worker, Hermes with Codex, and Hermes with Claude. Panels
checks the whole request before saving any of it. All requested changes succeed together
or none do, and the history records only fields that really changed. Sending values the
Ticket already has leaves it unchanged. Editing the implementer does not change the
Ticket's stage or control status and does not start implementation.

The current assignment is included in the actual Hermes worker prompt, not merely shown
in the Ticket UI or Panels chat. When an accepted Plan advances a Khushal-assigned Ticket
to implementation, control moves to user takeover for the human handoff. Agent-assigned
Tickets continue through the existing worker path, while an unassigned Ticket keeps the
existing behavior.

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
advances. At the ceiling, the at-cap rule decides. New tickets start leashed right at
**Kickoff**: the ceiling is `needs_kickoff` for every Worker type, so nothing advances past
the human-approved intake until the human grants scope onward — review before agents
start. Every later stage behaves the same way, including the last two: an accepted
implementation advances to **needs closeout**, and an accepted closeout advances
straight to **done**. (The threshold three other behaviours key off — the recap gate,
sprint-in-progress, external-work seed — is the *second* stage, held distinct from this
start ceiling; see `worker-types.md`.)

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

The Review screen can also send a ticket back instead of accepting it, whatever field
is currently gated. The human writes short guidance in the review card. Panels sends
that guidance directly to the ticket's existing Hermes session as the user message
that starts a worker turn. It is not copied into ticket chat and no later generic
worker prompt is sent. The ticket's stage never changes: a pending gated proposal is
cleared, settled values remain, and the ticket leaves Review while its control status
is **agent running step**. The gated field can therefore be revised while the ticket
remains at its current stage; it returns to Review when the worker submits the
revision.

_Code paths:_ `web/src/routes/TicketRoute.svelte` (the scope row),
`web/src/lib/ui.ts` (the shared ceiling options), `web/src/routes/ReviewRoute.svelte`
(the approval walk).

## Permanent deletion

Dropping a ticket keeps its record. Permanent deletion is different: it is a
direct-only capability for a ticket created by mistake. The ticket UI intentionally
has no delete control; deletion remains a manual API or CLI operation, and the CLI
requires `--yes`. The operation is blocked while ticket activity is still running.
One transaction removes the ticket from days, sprint views, links, Review, Workspace,
and Panels chat. Other tickets and day ordering stay intact.

Blocker links are removed in the same transaction. Surviving Ticket and Sprint-item
endpoints get `link_removed` events, and the delete response lists those affected
endpoint ids so clients can refresh them.

The deletion also replaces that ticket's old event history with one small deletion
record containing its identity, the direct actor, and the time. This is the only
exception to normal append-only event history. The separate stored Hermes session is
outside Panels' record and is not erased; once the ticket row is gone, Panels no
longer has a route that resolves or resumes it.

After the whole deletion transaction commits, the Ticket action calls the Automatic
Employee-step eligibility wake once. It does not wake once per removed day or link.

_Code paths:_ `src/planner/tickets/data.py`, `src/planner/tickets/api.py`,
`src/planner/cli/main.py`.

## The event log

Every normal change writes a permanent line into the event log. The records it
describes _do_ change — a ticket's fields update, taking a ticket off a day's list
removes that link — but the event lines remain. A permanent ticket deletion is the
one deliberate exception: that ticket's old lines are replaced by its minimal
deletion audit. The front end treats the log as a doorbell, not as data: a new line
tells screens to refetch.

_Code paths:_ `src/planner/core/events.py`.

## Handoffs

- **Worker types** (`worker-types.md`) — the registry that declares this Ticket's Stage
  set, its gates and fields, and its worker. The six Stages above are the `coding`
  Worker type's.
- **The employee runtime** (`employee-runtime.md`) — the worker that files the
  proposals and does the drafting; eligibility-affecting actions commit before calling
  its payload-free best-effort wake so discovery can check again at once.
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

_Last verified: 2026-07-14._
