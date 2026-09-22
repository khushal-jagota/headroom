# Sprints and Sprint Items

A Sprint is an inclusive, non-overlapping date range. Normal planning chooses seven
calendar dates. A Sprint Item holds shared intent, artifacts, and its supervisor
conversation across Sprints. A Ticket owns its Project and optional Sprint directly.
Its optional Sprint Item identifies the shared context it belongs to.

A commitment means “we chose this Sprint Item for this Sprint.” It can exist before any
Tickets are created. The same Sprint Item can be committed to several Sprints. Committing
or removing it never schedules, unschedules, or relocates its Tickets.

## Sprint documents — the thinking

The documents page has a short **Primary bet** summary and three editable Markdown
documents: **Kickoff**, **Checkpoint**, and **Sprint Review**. The same Primary bet appears
above Sprint tracking so the sprint's direction stays visible beside the work.

Each document is one piece of writing. Use headings when they help; there is no list of
blanks to fill. Kickoff explains the plan, Checkpoint revisits it on day four, and Sprint
Review records what happened and what to learn. Click the text to edit and click away to
save. All four values remain editable. Existing writing keeps its original headings.

Kickoff opens for a new sprint. Checkpoint opens when it or the review contains text;
Sprint Review opens when it contains text. Both later documents can be open near the end.

The sprint day changes at 05:00 local time. Panels uses that canonical day to decide
which sprint is current and which numbered day the sprint page shows. Existing sprint
ranges remain as stored, so historical sprints keep their original dates. Planning Sprint
must propose exactly seven inclusive dates for every new sprint.

At 17:00 local time on day four, the internal schedule creates a personal Checkpoint
Ticket in the current sprint. At 17:00 on the final day, it creates a `planning-sprint`
Ticket in the Personal Project and current Sprint. Its specialist Worker reviews
the current sprint first, plans the next sprint with the user, and writes only the
approved result at Consequences. A matching pre-laid Ticket suppresses each scheduled
duplicate. If a run is missed, recovery uses ordinary Ticket creation. Neither schedule
backfills a missed occurrence, and Planning Sprint stays on the final day.

## Tracking and carry-forward

Sprint tracking shows committed Sprint Items under Projects, including Sprint Items with no
Tickets. Each Sprint Item row links to its workspace. One collapsed **No Sprint Item** row follows
all Project groups when the Sprint contains unclassified Tickets. It combines
those Tickets across Projects and reveals their canonical links when opened.

The progress count says how many Tickets are done. It does
not claim that the Sprint Item has been achieved. It always uses `done/total`, including
`0/0` for a Sprint Item with no Tickets.
The Sprint review records the user's judgment about actual outcomes.

Add Sprint Item lets the user search and reuse an existing Sprint Item or create one with a
Project and optional brief. New creation retains the returned identity if commitment
fails, so retrying the commitment does not create another Sprint Item. The control follows
the Sprint Item list.

Carry forward adds the existing Sprint Item to a target Sprint and moves only the exact
unfinished Tickets the user checks. No Ticket is preselected. An empty selection carries
only the commitment. The whole change validates and commits together. A reclassified,
newly completed, or differently scheduled Ticket rejects the request without
moving the remaining selection. Repeating an unchanged successful request is safe.
The source commitment and unselected Tickets remain. Completed history never moves as
a side effect of carrying a Sprint Item.

Removing a commitment does not remove a Ticket's Sprint Item classification. Those Tickets
remain under that Sprint Item on Sprint tracking. Only Tickets without a Sprint Item appear
under **No Sprint Item**. The Sprint Item workspace always holds its full brief, artifacts, and
Tickets across Sprints. Its header shows open work and work that needs the user. Its
artifact strip lists what there is to open, newest first. A folder is one thing on it,
not one thing per file: a folder that holds an `index.html` opens that page, and a folder
without one opens where it stands and shows what is directly inside. The files an index
loads, such as its stylesheet and its images, never appear on their own. Today and Other Tickets use the same status
groups. Other Tickets starts collapsed. Ticket rows omit Sprint names and mark only open,
unsprinted work as Backlog. The Ticket page does not show Sprint or Backlog placement.
Commitment links and a Ticket's stored schedule can differ: they state different facts.

The open Item and its Workspace rail card use one Item snapshot. One presentation rule
derives each Ticket group, label, activity order, count, and mark. A white dot means that
the owner owes approval or an answer. A blue dot means that an unread reply waits. A
spinner means that an agent works now. The mark is empty otherwise. Ownership groups and
activity marks are independent. The rail stays compact, while Today and Other Tickets
show the full set.

Sprint Item and Ticket Projects remain coherent. Classifying a Ticket aligns its Project
and preserves its Sprint. Removing its Sprint Item preserves both Project and Sprint.
Changing a Sprint Item's Project moves child and template Projects in one transaction,
without changing their Sprint destinations. A Ticket Project patch must explicitly
clear an incompatible Sprint Item; the server rejects inconsistent final placement.

Ordinary creation defaults to Today and the current Sprint; explicit backlog leaves
Sprint empty. Sprint Item context does not choose a Sprint. Existing Planning Sprint Items keep
all their text, children and supervisor history. New Sprints and planning Tickets do
not manufacture Planning containers.

The stable CLI and API still call the stored identity a Sprint Item. Use
`panels sprint item create/list/show/set` for its record, `add-ticket/remove-ticket`
for classification, and ordinary Ticket placement for scheduling.
`panels sprint add-item/remove-item/list-items` manages commitments and tracking;
`carry-item <source-sprint> <item> --to <target-sprint> --ticket <ticket>` states the exact
selection. `GET /api/sprints/{id}/tracking` also reads past or future Sprints directly.

Sprint Item deletion still refuses children. Childless deletion removes its commitments
and managed files through the existing guarded lifecycle.

## Sprint Item supervisors

Each normal Sprint Item owns one supervisor identity. Panels creates the identity and
its launch configuration with the item. Existing items received the same fixed
configuration during migration. The Other section is a view of loose Tickets and owns
no supervisor.

The supervisor conversation starts when the user sends it a message or when Panels
delivers a manager wake. A reset kills current work and clears the agent link.
Conversation records and message files remain as history. The Sprint Item body is the shared
brief.

Its job is small. It creates Tickets under its Item, and it answers what is going on
there. It does more when the user asks it to, and the actions below are how. It is not a
manager: it does not push Tickets along. A wake asks it to inspect one or more routed
proposals or explicit worker errors. It reads canonical state before it decides what to do.

Manager wakes use one durable queue. Panels groups open wakes for a Sprint Item and sends a
normal queued message. A busy supervisor finishes its current turn first. Panels closes a
wake only when the exact prompt reaches the durable conversation record. Definite refusals
and discarded queued prompts get a later attempt. An uncertain send is retained without an
automatic replay. Open wakes survive server restarts.

A supervisor has no routes of its own. It calls the routes Khushal calls, and the one
rule admits it on its own current child Tickets and on its own Item, because that is what
it stands above and what it is. A write aimed at another Item, or at a Ticket that is not
a current child, is refused by the same sentence. See `authority.md`.

That is wider than the eight wrapped operations it used to have. A supervisor now reaches
every ordinary operation on its own child Tickets, including deciding a proposal it does
not hold. The one thing it may not do is move a child Ticket to another Sprint Item: changing
who stands above a Ticket is handing authority around rather than using it.

A supervisor creates its own child Tickets with the ordinary Ticket creation route. A
Ticket it creates carries a ceiling like any other: this Sprint Item becomes the holder,
so the Brief parks for the Item unless the supervisor states a higher ceiling it was
given.

It deletes a current child Ticket through that same route. The deletion is permanent and
nothing else guards it: a Ticket whose Worker is mid-turn is deleted too, and that Worker
is killed with it.

Config edits the canonical Sprint Item supervisor role skill. Supported backends read
that managed source for future conversations. A save does not rewrite an existing
conversation, its role record, or its history.

The Item workspace is the overview: the Item itself, its artifacts, and one line for each
Ticket on it — what the Ticket is called, where it has got to, and which days it sits on.
Finished Tickets stay in that list. It is what a supervisor reads to decide where to
look, so it never carries a Ticket's written work. It names who holds each parked
proposal, so any reader can see which ones are addressed to them.

The written work lives on the Ticket, read the ordinary way. A Ticket's current Worker
conversation is read where every conversation is read, in bounded pages, forwards from a
position or backwards from the end.

A message to a Worker goes through Send Message, the one door for messaging any
principal. It resolves the Ticket's current conversation as it lands, and starts one when
there is none. Panels records the sender. This path does not change the Ticket Stage,
ceiling, status, or Day membership.

Nothing a supervisor does reaches the user on its own. Backend prose is runtime output;
only an explicit Send Message reaches another principal. The Item row in the Workspace
uses the shared activity mark across its supervisor and child Tickets. Reading an Item
clears its unread supervisor reply through the normal conversation read position. A
system marker in the transcript says when a turn ended without an explicit reply.

The workspace reads one coherent Item snapshot with child Ticket Day membership,
activity timestamps, attention facts, artifacts, supervisor state, and the current
conversation link. It adds
no second Ticket review or Worker-control route. Worker readiness remains the only
automatic creator of a Worker step.

Managed item artifacts live under `files/sprint-items/<item-id>/`. The server exposes
them through `/files/sprint-items/<item-id>/<relative-path>`. Two readings of the same
directory exist, and they are for different readers. The flat listing returns every file
at every depth, which is what an agent needs to find the file it wrote. The workspace
returns folded entries, which is what a person reads. Item deletion moves this
directory to quarantine before its database transaction. A failed transaction restores
the directory. A successful deletion removes the item, its agent row, and its files.

_Code paths:_ `src/planner/sprints/` (the sprint, its items, and workspace read),
`web/src/components/SprintItemWorkspace.svelte` (the Item workspace),
`web/src/routes/SprintRoute.svelte` (tracking and documents), and
`web/src/lib/workItemPresentation.ts` (shared Item and Ticket presentation rules).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a sprint item is
  made of.
- **The front end** (`frontend.md`) — the Sprint routes and their shared visual system.
- **Projects** (`projects.md`) — the catalog used by sprint items.

---

_Last verified: 2026-09-22._
