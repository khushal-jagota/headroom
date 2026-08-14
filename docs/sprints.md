# Sprints

A sprint is a stored inclusive date range. Normal sprint planning creates a seven-day
range, but the record accepts any valid, non-overlapping range. Its main page tracks
Tickets by optional Sprint Item classification. Each Item has a dedicated Item view. A
separate documents page holds the sprint's written record.

```
   Sprint tracking                 Sprint Item
   ───────────────────────────     ───────────────────────────
   Project                          Today · live state groups
    └ Sprint Item · progress        Remaining Tickets
   Other · unclassified Tickets     ▸ done Tickets
                                    Artifacts
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

The Item workspace shows its identity, editable title, and editable shared brief. Today
Tickets use live state groups. Each group and section shows its count only while folded.
Remaining Tickets keeps off-today and done work visible without competing with Today.
Ticket rows link to the canonical Ticket page for all review and resolution actions.

The workspace also lists managed Item artifacts and opens them through the shared file
preview. Delivery failures appear as attention above the work. The supervisor uses the
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

The supervisor conversation starts only after its first user or obligation message. A reset
kills current work and clears the agent link. Conversation records and message files
remain as history. The Sprint Item body is the shared brief.

A supervisor acts within its own Sprint Item. It changes Item fields, child Ticket
fields, Day membership, blocks, scope, proposal review, and Item artifacts through one
item-scoped service, and every write delegates to the same domain action that direct
product routes use. A write aimed at another Item, or at a Ticket that is not a current
child, is refused.

A supervisor creates its own child Tickets with the ordinary Ticket creation route. A
Ticket it creates under its own Item rests at agent review, so the supervisor reviews
the kickoff it wrote. A Ticket it creates anywhere else rests at user review, like any
other Ticket.

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

Supervisor obligations are durable and separate from Web Push notifications. Panels sends
bounded ordered batches through the conversation runtime. The runtime starts or queues them.
Acknowledgement records attention, while canonical Ticket state closes the obligation.

The workspace reads one coherent Item snapshot with child Ticket Day membership,
artifacts, open obligations, supervisor state, and the current conversation link. It adds
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

_Last verified: 2026-08-12._
