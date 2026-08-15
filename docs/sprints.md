# Sprints

A sprint is a stored inclusive date range. Normal sprint planning creates a seven-day
range, but the record accepts any valid, non-overlapping range. Its main page tracks
Tickets by optional Sprint Item classification. Each Item has a dedicated Item view. A
separate documents page holds the sprint's written record.

```
   Sprint tracking                 Sprint Item
   ───────────────────────────     ───────────────────────────
   Project                          identity · title · brief
    └ Sprint Item · progress        today's Tickets, by status
   Other · unclassified Tickets     ▸ Artifacts
                                    ▸ Remaining Tickets
                                    Supervisor conversation

   Sprint documents
   ───────────────────────────
   Kickoff · Checkpoint · Sprint Review
```

## Sprint documents — the thinking

The documents page has three headed sections you read top to bottom: **Kickoff** (why this
sprint, the bet, what it rests on, what could go wrong), **Checkpoint** (where we stand,
what's changed, and what to adjust on day four), and **Sprint Review** (how it went, at
the end). Every section is headed writing you edit in place — click a line, type, click
away, and it saves on its own. Kickoff opens for a new sprint. Checkpoint opens when it
contains text. Sprint Review also opens when it contains text, so both later sections
can be open near the end. The stored field names still use their historical `mid_*`
identifiers.

Nothing on this page locks a section. Each edit writes its document field directly.

The sprint day changes at 05:00 local time. Panels uses that canonical day to decide
which sprint is current and which numbered day the sprint page shows. Existing sprint
ranges remain as stored, so historical sprints keep their original dates. Planning Sprint
must propose exactly seven inclusive dates for every new sprint.

At 17:00 local time on day four, the internal schedule creates a personal Checkpoint
Ticket in the current sprint. At 17:00 on the final day, it creates a `planning-sprint`
Ticket in that Sprint's Personal / Planning Item. Its specialist Worker reviews
the current sprint first, plans the next sprint with the user, and writes only the
approved result at Closeout. A matching pre-laid Ticket suppresses each scheduled
duplicate. If a run is missed, recovery uses ordinary Ticket creation. Neither schedule
backfills a missed occurrence, and Planning Sprint stays on the final day.

## Tracking — the items

Tracking groups Sprint Items under foldable Projects. Project priority orders the
groups, and done Items come last inside a Project. Tickets without a Sprint Item appear
in one view-only **Other** group. Other is not a stored Item, has no Item address, and
shows its Ticket rows directly.

Each Item row shows its priority, title, and Ticket completion. It says `to do` before
any Ticket is done, a fraction during progress, and `done` when all non-dropped Tickets
are done. Selecting the row opens the dedicated Item address.

The Item workspace shows its identity, editable title, and editable shared brief. The
brief runs the full width of the column and clamps to three lines, with Show more.

Under the brief the page is two more blocks, each behind a hairline. First today's
Tickets, named only by their status. Then the index: Artifacts, then Remaining Tickets,
which keeps off-today and done work visible without competing with today. Both index
sections arrive shut.

Every status is its own dropdown. Needs you, User, Waiting for kickoff, Awaiting
approval, Paired and Agent arrive open — work running right now is what the reader came
for. Blocked, Waiting for closeout, Empty and Done arrive shut, still named and counted, and
they are broadly what the Workspace rail leaves out: a group you have to open here is
usually a group the rail does not carry. The one crossing is a Ticket whose own run
errored. It reads as Blocked here, alongside a Ticket waiting on another Ticket, but the
rail carries it as Errored, because a broken run wants you now and waiting on someone
else does not. Open shows a chevron, shut shows the
count, on statuses and sections alike, and the two measure the same, so opening one
moves nothing else on the page. A Ticket waiting on you and a Ticket that is yours to do
are two groups, here and in the Workspace rail both.

This page splits a Ticket by its condition and the rail splits it by its raw status, so
the two screens do not always draw the same set of groups. They do use the same words: a
group here and a group there that hold the same Tickets carry one label. Rename one and
rename both.

A Ticket row is its condition mark and its title. Priority is stated once, in the
identity above, not on every row. Ticket rows link to the canonical Ticket page for all
review and resolution actions.

The workspace also lists managed Item artifacts and opens them through the shared file
preview. The supervisor uses the
same live conversation, composer, model controls, transcript, reset, and change-stream
behavior as Ticket conversations. Reset starts a new current conversation without hiding
prior transcripts. The layout preserves the same document and conversation split on
desktop and phone. An Item reads at the same width and against the same edges as a
Ticket, whichever address it is opened from, so opening one after the other in the same
place shows no step between them.

Each Sprint Item stores plain fields and placement only: title, body, priority,
deadline, Project, and optional Sprint. Its status is derived when read:
an item is done when all non-dropped child tickets are done, in progress when any
child ticket is active or an agent is working, blocked when it has an open blocking
ticket or blocked/errored child, and todo otherwise.

Each Ticket stores its Project and optional Sprint directly. Sprint Item membership is
optional classification. When present, the Item's Project and Sprint must match the
Ticket. An Item placement change moves all classified Tickets with it. A Ticket placement
change clears an Item that no longer matches.

Ordinary Ticket creation adds the Ticket to Today and the current Sprint when the caller
omits those choices. Explicit backlog keeps the Sprint empty. An explicit Sprint Item
sets one coherent Project, Sprint, and Item combination. Panels stores no fallback Other
Items.

The Personal Project has one Planning Item in each Sprint. Existing and future
`planning-day`, `planning-midday-check`, and `planning-sprint` Tickets use that Item.
`initiative_planning` Tickets stay with their initiative instead of moving to Planning.

Planning writes validate the full requested change before altering the Sprint, Item, or
Ticket, then commit the compound change once. `panels sprint item move-ticket` atomically
classifies a Ticket under the Item and aligns its direct placement.
`move-ticket-to-backlog` names both the current Item and Ticket, so a stale request
cannot move a Ticket that was subsequently reclassified. Repeating either request is
safe. Sprint creation remains an explicit
non-idempotent operation: after an ambiguous response, read the sprint list before
trying another create.

Any Ticket worker can use both Ticket placement commands when it sends its own existing
Ticket id with its worker identity. Direct callers keep the same access. Other sprint
planning writes still require the `planning-sprint` Worker type.

An unwanted Sprint Item can be permanently deleted through
`panels sprint item delete <item-id> --yes`. Panels refuses deletion while the item
has child Tickets, so existing work cannot disappear as a side effect. Deleting a
childless item also removes its blocking links and refreshes sprint, backlog, board,
and linked-Ticket views.

## Sprint Item supervisors

Each normal Sprint Item owns one supervisor identity. Panels creates the identity and
its launch configuration with the item. Existing items received the same fixed
configuration during migration. The Other section is a view of loose Tickets and owns
no supervisor.

The supervisor conversation starts only when it is first needed: a user message, or the
first wake because the Item needs its supervisor. A reset kills current work and clears
the agent link. Conversation records and message files remain as history. The Sprint Item
body is the shared brief.

A supervisor acts within its own Sprint Item. It changes Item fields, child Ticket
fields, Day membership, blocks, scope, proposal review, and Item artifacts through one
item-scoped service, and every write delegates to the same domain action that direct
product routes use. A write aimed at another Item, or at a Ticket that is not a current
child, is refused.

A supervisor creates its own child Tickets with the ordinary Ticket creation route. A
Ticket it creates is scoped like any other: the kickoff parks for the user's approval
unless the supervisor states a wider scope it was given.

Config edits the canonical Sprint Item supervisor role skill. Supported backends read
that managed source for future conversations. A save does not rewrite an existing
conversation, its role record, or its history.

Ticket context includes current Ticket facts, Day membership, the current Worker
conversation, and the exact triggering Worker message when its sequence is supplied.
The supervisor can read bounded pages from that current conversation.

A targeted Worker message requires the exact current child conversation. Panels records
the Sprint Item supervisor agent key as the sender. A missing, reset, stale, or unrelated
conversation is refused. This message path cannot create a conversation and does not
change the Ticket Stage, scope, status, or Day membership.

Supervisor wakes are separate from Web Push notifications, and they store nothing. Panels
asks one read-only question about each Sprint Item — does this Item need its supervisor
right now — and it asks only when the change signal says something was written. When the
answer is yes, and the supervisor's conversation is free with nothing already waiting for
it, Panels sends one message that carries no facts: your Sprint Item needs you, read the
current context and act. The supervisor then reads canonical state itself, and what it
reads is current at the moment it reads it. There is nothing to identify, retry, or
acknowledge; if the Item still needs its supervisor after the turn, the next answer is
still yes.

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

_Last verified: 2026-08-14._
