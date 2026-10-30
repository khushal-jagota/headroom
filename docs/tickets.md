# Tickets

A Ticket is one piece of work small enough to hand to a single AI Worker. It is the unit Headroom can stage, pause, approve, and come back to. The title and recap orient a person. The Worker type supplies the ladder of stages. The conversation is where the Worker is actually told things. Artifacts are the plans, documents, images, and prototypes the work produced.

## Why a Ticket is a ladder

Each non-terminal stage has exactly one blank. Filling that blank, and having the fill accepted, moves the Ticket one stage forward. The ladder always starts at Brief and always ends at Done. The stages between belong to the Worker type: a coding Ticket walks Success Condition, What Changes, Plan, Implementation, and Consequences; a research Ticket walks a shorter path. The shapes are listed in [Worker types](worker-types.md).

Brief holds intake: the original wording, the boundaries, and the advice that should stay readable beside the later stages. The title is ordinary metadata, not part of the Brief proposal. After Brief is settled, editing the title stays a metadata edit. Editing Brief text uses the ordinary field path.

The Ticket also stores the backend, model, and reasoning its conversation should start with. Creation copies the Worker type’s three starting values. The Brief section shows those controls only while Brief is still pristine and no conversation exists yet. Hermes has a model and no reasoning control. Codex and Claude Code show the reasoning values their selected model supports.

There is no retirement state. Done is the end. Historical text for a field the Worker type no longer declares stays in storage. Reads and search show declared fields. New proposals must use declared fields.

## How status is read

The stored control fact is the worker-step claim: none, out, or errored. Everything a person calls “status” is derived on read, first match wins:

| Derived status | What is true right now |
| --- | --- |
| errored | The claim is errored. Delivery was uncertain, or a restart is required. |
| awaiting_approval | A proposal is parked. |
| agent | The claim is out. Headroom has handed the Worker a step. |
| blocked | The Ticket would otherwise be at rest, and a live blocker remains. |
| empty | At rest, so readiness may start the next step. |

A live blocker is another Ticket that is not Done. While the Worker is mid-step or a proposal is parked, the person sees agent or awaiting approval. The blocker shows up again when the Ticket returns to rest. Finishing or removing the blocker clears the blocked reading by itself.

A second, quieter fact is whether the agent is working, idle, or errored. That blends the derived status with the live conversation: a running turn reads as working even while the claim still says the step is out. The claim is not a heartbeat. See [Worker steps](worker-steps.md).

## Backlog, resume, delete

Backlog is an explicit pause of the next automatic Worker step. The stage, the parked proposal, the holder, the assignment, the history, the conversation, and the artifacts stay. Messages and approvals stay available. A turn that was already delivered keeps running. Resume clears only the backlog flag. Ordinary readiness and approval rules apply again.

The Ticket page offers Backlog ticket and Resume ticket. Each has one writer. The browser sends a hash of the record it displayed, and a stale request is refused inside the write.

Delete removes the Ticket’s record. Files under the Ticket’s file tree survive. The browser does not offer delete. `panels ticket delete` does, and it refuses while a Worker step or a live turn still needs the Ticket, unless the caller forces it or a Manager is deleting its own child. A Ticket that is another Ticket’s ceiling holder cannot be deleted out from under that review.

## Where the Worker may run

Each Ticket stores a locale, `vps` or `macos`. Creation uses an explicit locale when one is given. Otherwise a Mac requirement on the effective Project or the Worker type selects `macos`, and everything else selects `vps`. The chosen backend is checked against that locale before the Ticket exists. The Mac compatibility set starts empty, so Mac creation refuses until that configuration allows a backend. The VPS refuses to start a Ticket whose locale is somewhere else.

`panels ticket move` changes the locale of an idle Ticket: no Worker step, active turn, or accepted message may still need the current machine. The conversation and history stay. Repository files do not travel. Later edits to a Project or Worker requirement do not move Tickets or stored schedule locales that already exist.

## Assignment

A Ticket names Chief or one ordinary Manager. That assignment is who the work sits under in the Workspace. It is a different fact from the ceiling holder, who is addressed when a proposal parks. Chief’s global authority does not imply that every Ticket is assigned to Chief.

## What feeds a Ticket, and what it feeds

People, Chief, Managers, and due schedules create Tickets. Blockers are named at creation or added later. The readiness loop feeds the conversation its next opener when the Ticket reads empty. The Worker feeds proposals. The resolver feeds settled stage text and the next stage. Attention flags feed Review and notifications. Artifacts land in the Ticket file tree and open in the viewer.

Settled fields remember who approved the current text. Explicit approval says Approved by Khushal, Chief, or the Manager’s title. A field that settled below the ceiling says Auto-accepted, including a Brief that settled at creation. Older rows and some direct completions say Approval unknown. Unknown is not auto-accepted. An edit that changes the text clears the approval. An identical edit keeps it.

## Where it shows up

The Workspace rail and each Manager page list Tickets by the kind of attention they need. The Ticket itself is the inspector: masthead, lifecycle, blockers, the stage spine, the current gate, artifacts, and the conversation dock. Backlogged Tickets leave the rail’s attention groups and appear in the Manager’s Backlogged section. `#/backlog` opens the Workspace. There is no separate backlog screen and no global New Ticket form in the browser. Creation is the command line, the API, or a Manager or Chief doing it in conversation.

Artifact links in stage text and in the conversation open in the Ticket viewer when the format can be previewed. JSON receipts and unsupported formats keep their download. HTML links inside an artifact stay in the viewer only when they resolve to HTML inside that same artifact directory. A link that leaves the directory, or a symlink that escapes it, leaves the current document up and the parent shows a rejection. The frame is sandboxed with scripts allowed, because the artifact is trusted Ticket output, not an arbitrary page from the web.

A shared file link can also open at `#/preview`. Inline preview on the Ticket keeps the Ticket’s address.

## Pointers

`src/planner/tickets/` holds the record, the derived status, and the writers. Stage ladders live in `src/planner/worker_types/shipped_worker_types.json`. Locale checks live in `src/planner/tickets/locale_configuration.py`. The Ticket screen is `web/src/routes/TicketRoute.svelte`.

_Last verified: 2026-10-30._
