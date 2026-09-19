# Sprints and Outcomes

A Sprint is an inclusive, non-overlapping date range. Normal planning chooses seven
calendar dates. An Outcome holds shared intent, artifacts, and its supervisor
conversation across Sprints. A Ticket owns its Project and optional Sprint directly.
Its optional Outcome identifies the shared context it belongs to.

A commitment means “we chose this Outcome for this Sprint.” It can exist before any
Tickets are created. The same Outcome can be committed to several Sprints. Committing
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
approved result at Closeout. A matching pre-laid Ticket suppresses each scheduled
duplicate. If a run is missed, recovery uses ordinary Ticket creation. Neither schedule
backfills a missed occurrence, and Planning Sprint stays on the final day.

## Tracking and carry-forward

Sprint tracking shows committed Outcomes under Projects, including Outcomes with no
Tickets. Each Outcome row links to its workspace. One collapsed **No Outcome** row follows
all Project groups when the Sprint contains non-dropped unclassified Tickets. It combines
those Tickets across Projects and reveals their canonical links when opened.

The progress count says how many Tickets are done, excluding dropped Tickets. It does
not claim that the Outcome has been achieved. It always uses `done/total`, including
`0/0` for an Outcome with no non-dropped Tickets.
The Sprint review records the user's judgment about actual outcomes.

Add outcome lets the user search and reuse an existing Outcome or create one with a
Project and optional brief. New creation retains the returned identity if commitment
fails, so retrying the commitment does not create another Outcome. The control follows
the Outcome list.

Carry forward adds the existing Outcome to a target Sprint and moves only the exact
unfinished Tickets the user checks. No Ticket is preselected. An empty selection carries
only the commitment. The whole change validates and commits together. A reclassified,
newly completed, dropped, or differently scheduled Ticket rejects the request without
moving the remaining selection. Repeating an unchanged successful request is safe.
The source commitment and unselected Tickets remain. Completed history never moves as
a side effect of carrying an Outcome.

Removing a commitment does not remove a Ticket's Outcome classification. Those Tickets
remain under that Outcome on Sprint tracking. Only Tickets without an Outcome appear
under **No Outcome**. The Outcome workspace always holds its full brief, artifacts, and
Tickets across Sprints. Its header shows open work and work that needs the user. Its
artifact strip lists files newest first. Today and Other Tickets use the same status
groups. Other Tickets starts collapsed. Ticket rows omit Sprint names and mark only open,
unsprinted work as Backlog. The Ticket page does not show Sprint or Backlog placement.
Commitment links and a Ticket's stored schedule can differ: they state different facts.

Outcome and Ticket Projects remain coherent. Classifying a Ticket aligns its Project
and preserves its Sprint. Removing its Outcome preserves both Project and Sprint.
Changing an Outcome's Project moves child and template Projects in one transaction,
without changing their Sprint destinations. A Ticket Project patch must explicitly
clear an incompatible Outcome; the server rejects inconsistent final placement.

Ordinary creation defaults to Today and the current Sprint; explicit backlog leaves
Sprint empty. Outcome context does not choose a Sprint. Existing Planning Outcomes keep
all their text, children and supervisor history. New Sprints and planning Tickets do
not manufacture Planning containers.

The stable CLI and API still call the stored identity a Sprint Item. Use
`panels sprint item create/list/show/set` for its record, `add-ticket/remove-ticket`
for classification, and ordinary Ticket placement for scheduling.
`panels sprint outcome add/remove/list` manages commitments and tracking;
`carry <source-sprint> <outcome> --to <target-sprint> --ticket <ticket>` states the exact
selection. `GET /api/sprints/{id}/tracking` also reads past or future Sprints directly.

Outcome deletion still refuses children. Childless deletion removes its commitments
and managed files through the existing guarded lifecycle.

## Outcome supervisors

Each normal Outcome owns one supervisor identity. Panels creates the identity and
its launch configuration with the item. Existing items received the same fixed
configuration during migration. The Other section is a view of loose Tickets and owns
no supervisor.

The supervisor conversation starts only when the user sends it a message. Nothing else
starts one. A reset kills current work and clears the agent link. Conversation records and
message files remain as history. The Outcome body is the shared brief.

Its job is small. It creates Tickets under its Item, and it answers what is going on
there. It does more when the user asks it to, and the actions below are how. It is not a
manager: it does not push Tickets along, and it does not resolve parked proposals as
routine work.

A supervisor acts within its own Outcome. It changes Item fields, child Ticket fields,
scope, proposal review, and Item artifacts through one item-scoped service, and every
write delegates to the same domain action that direct product routes use. Day membership
and Ticket blocks between its children use the ordinary membership call, where its own
identity carries the same authority. A write aimed at another Item, or at a Ticket that
is not a current child, is refused.

A supervisor creates its own child Tickets with the ordinary Ticket creation route. A
Ticket it creates is scoped like any other: the kickoff parks for the user's approval
unless the supervisor states a wider scope it was given.

A supervisor also deletes a current child Ticket, through the ordinary deletion route.
The Item is taken from the supervisor's own identity, so it cannot reach a Ticket
elsewhere. That boundary is the only check. The deletion is permanent and nothing else
guards it: a Ticket whose Worker is mid-turn is deleted too, and that Worker is killed
with it.

Config edits the canonical Outcome supervisor role skill. Supported backends read
that managed source for future conversations. A save does not rewrite an existing
conversation, its role record, or its history.

A supervisor asking about its own Item gets an overview: the Item itself, and one line
for each Ticket on it — what the Ticket is called, where it has got to, and which days
it sits on. Finished Tickets stay in that list. The overview is what a supervisor reads
to decide where to look, so it never carries a Ticket's written work.

Ticket context is where that written work lives. It includes current Ticket facts, Day
membership, the current Worker conversation, and the exact triggering Worker message when
its sequence is supplied. The supervisor can read bounded pages from that current
conversation.

A targeted Worker message requires the exact current child conversation. Panels records
the Outcome supervisor agent key as the sender. A missing, reset, stale, or unrelated
conversation is refused. This message path cannot create a conversation and does not
change the Ticket Stage, scope, status, or Day membership.

Nothing a supervisor does reaches the user on its own. Backend prose is runtime output;
only an explicit Send Message reaches another principal. The Item row in the Workspace
carries the same mark a Ticket row carries: an unseen completed turn from the
conversation, put out when the user opens the Item. A system marker in the transcript
says when that turn ended without an explicit reply to its prompt sender.

The workspace reads one coherent Item snapshot with child Ticket Day membership,
artifacts, supervisor state, and the current conversation link. It adds
no second Ticket review or Worker-control route. Worker readiness remains the only
automatic creator of a Worker step.

Managed item artifacts live under `files/sprint-items/<item-id>/`. The server exposes
them through `/files/sprint-items/<item-id>/<relative-path>`. Item deletion moves this
directory to quarantine before its database transaction. A failed transaction restores
the directory. A successful deletion removes the item, its agent row, and its files.

_Code paths:_ `src/planner/sprints/` (the sprint, its items, and workspace read),
`web/src/components/SprintItemWorkspace.svelte` (the Item workspace),
`web/src/routes/SprintRoute.svelte` (tracking and documents), and
`web/src/lib/sprintItemWorkspace.ts` (workspace presentation rules).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the tickets a sprint item is
  made of.
- **The front end** (`frontend.md`) — the Sprint routes and their shared visual system.
- **Projects** (`projects.md`) — the catalog used by sprint items.

---

_Last verified: 2026-08-17._
