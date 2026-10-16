# Orchestrators and historical Sprints

Orchestrators hold shared intent, Workers, artifacts, and a conversation. Ordinary
orchestrators retain their former Sprint Item IDs and supervisor agent keys. The stored
`sprint_items` name and existing Item links remain stable. They do not require a Sprint.

Chief is a special orchestrator and an explicit assignment destination. A Worker names
`orchestrator: {"kind":"chief","id":"chief"}` or
`orchestrator: {"kind":"sprint_item","id":"<existing-item-id>"}`.
The database stores `orchestrator_kind` and the conditional `sprint_item_id` reference.
A null Item reference alone no longer represents assignment. Chief keeps the existing
Chief agent, conversation, and launch settings. It is not a synthetic Item.

Assignment identifies responsibility. A ceiling holder identifies an approval address.
Chief's global authority remains independent of either fact. Worker Project rules also
remain independent of the assignment discriminator.

The owner controls an ordinary orchestrator's settled state. Chief can never settle.
Settlement changes presentation, not Worker eligibility, approval discovery, message
delivery, or history. The reviewed cutover starts exactly two existing ordinary
orchestrators active: Rethink how Panels works for us and Make Panels run work across
the Mac and the VPS. All other existing ordinary orchestrators start settled.
The migration checks both canonical identities and titles before any settlement write.
It changes no child execution or assignment. A fresh installation has no initial cohort,
and later orchestrator creation keeps the ordinary active default. Child completion
does not settle a parent.

Settled supervisor and Worker conversations remain in Messages when unread. Each dot
belongs to its own conversation row. Message folds expose distinct conversation counts
in text. The Settled footer shows only its ordinary orchestrator count. Normal read
acknowledgments clear unread dots and message counts. Later messages restore discovery
without reopening the orchestrator.

Use `panels orchestrator create/list/show/set`, `workspace`, and `artifact` for ordinary
orchestrators. Existing `panels sprint item` paths remain aliases. Use
`panels ticket assign <ticket> --chief` or `--orchestrator <item>` for explicit assignment.
`panels orchestrator settle <item>` settles an ordinary orchestrator. `--active` returns
it to active. The server accepts only the owner for this action.

Sprint records, documents, commitments, dates, and historical placement remain readable.
Sprint creation, edits, commitment changes, and carry-forward are retired from the live
API and CLI. `panels sprint list/show/list-items` and historical GET routes preserve
access. Reading history neither creates a Sprint nor activates old planning work.

The final visual layout waits for the approved design handoff. Its confirmed direction
keeps Workers nested beneath orchestrators and puts Project and Settle in the eyebrow
above the title in the sidebar and open content.

## Orchestrator supervisors

Each orchestrator owns one supervisor identity derived from its Item ID. Panels creates it and
its launch configuration with the item. Existing items received the same fixed
configuration during migration. Former loose Workers now explicitly name Chief.

The supervisor conversation starts when the user sends it a message or when Panels
delivers a manager wake. A reset kills current work and clears the agent link.
Conversation records and message files remain as history. The orchestrator body is the shared
brief.

The supervisor commissions, steers, and reviews its Workers with the user. It reads
canonical state before it acts on a routed proposal or Worker error. The owner decides
when the orchestrator is settled.

Manager wakes use one durable queue. Panels groups open wakes for an orchestrator and sends a
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
not hold. The one thing it may not do is move a child Ticket to another orchestrator: changing
who stands above a Ticket is handing authority around rather than using it.

A supervisor creates its own child Tickets with the ordinary Ticket creation route. A
Ticket it creates carries a ceiling like any other: this orchestrator becomes the holder,
so the Brief parks for the Item unless the supervisor states a higher ceiling it was
given.

It deletes a current child Ticket through that same route. The deletion is permanent and
nothing else guards it: a Ticket whose Worker is mid-turn is deleted too, and that Worker
is killed with it.

Config edits the canonical orchestrator supervisor role skill. Supported backends read
that managed source for future conversations. A save does not rewrite an existing
conversation, its role record, or its history.

The Item workspace is the overview: the Item itself, its artifacts, and one line for each
Ticket on it — what the Ticket is called and where it has got to.
Finished Tickets stay in that list. It is what a supervisor reads to decide where to
look, so it never carries a Ticket's written work. It names who holds each parked
proposal, so any reader can see which ones are addressed to them.

The written work lives on the Ticket, read the ordinary way. A Ticket's current Worker
conversation is read where every conversation is read, in bounded pages, forwards from a
position or backwards from the end.

A message to a Worker goes through Send Message, the one door for messaging any
principal. It resolves the Ticket's current conversation as it lands, and starts one when
there is none. Panels records the sender. This path does not change the Ticket Stage,
ceiling, or status.

Nothing a supervisor does reaches the user on its own. Backend prose is runtime output;
only an explicit Send Message reaches another principal. The Item row in the Workspace
shows a dot only for an unread addressed message in its supervisor conversation.
Child Ticket messages stay on their own rows. Reading the supervisor conversation
clears its unread message through the normal conversation read position. A
system marker in the transcript says when a turn ended without an explicit reply.

The workspace reads one coherent orchestrator snapshot with child Ticket
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

_Code paths:_ `src/planner/sprints/`, `src/planner/core/authority/`,
`src/planner/tickets/`, and `src/planner/cli/main.py`.
