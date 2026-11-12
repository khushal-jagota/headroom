# Managers and Chief

A Manager is an agent that holds one direction and the Tickets that serve it. It exists so a line of work has a stable home and a supervisor: someone who commissions Workers, reads their results, decides the proposals addressed to it, and keeps a short status a person can read. Chief is the top-level agent above all of them.

## What a Manager is made of

- **A goal**: the sentence that says why this Manager exists. This is a field on the Manager, not a [goal](goals.md) with a deadline and a page.
- **A recap**: the current status line. Each new recap replaces the old one.
- **A page document**: the overview that the Manager keeps for Khushal, with text, checklists, charts, layouts, and Tickets.
- **An optional project.** A Manager's project is also the effective project of its Tickets.
- **Child Tickets**: the Tickets assigned to it.
- **Artifacts** and **its own conversation**, on the ordinary [conversation system](conversations.md).
- **A launch setup**: the backend, model, and reasoning its conversation uses.

## Manager identity

Current agent identities use `PLAN_ACTOR=manager` and `PLAN_MANAGER_ID`. The forward migration preserves each Manager and its conversation. Retained conversations receive the current role instructions on their next turn. Old actor names, command aliases, request headers, and environment names do not select a Manager.

Manager files retain `/files/sprint-items/` addresses so that existing Ticket links still open. Historical migration backfills remain isolated from current navigation writes.

## Chief

Chief is the special case. It is a global principal that stands above everything, with its own retained conversation and settings. Tickets can be assigned to Chief directly. Chief is not stored as an ordinary Manager, has no goal field, and can never be settled.

## What a Manager does

A Manager acts through the same routes as everyone else. It has no private API. [Authority](authority.md) limits it to its own record and its current children. It can create child Tickets, approve or send back their proposals, message them, delete them, and restart a Worker that errored. It cannot move a Ticket to another Manager. Only Khushal and Chief can.

The Manager can edit its own page and held goal pages directly. A captured document revision protects an edit from replacing newer content. Its plain goal field states its purpose. Artifacts hold supporting files. Neither replaces a page document.

The owner, Chief, Managers, and Workers talk through Send Message, which delivers a real prompt into the recipient's conversation. Two Managers can message each other. That is speech, not authority over each other's Tickets.

## How a Manager hears about work

Some events become durable **wakes**, real prompts queued into the responsible Chief or Manager conversation:

- a Ticket proposal parked for that Manager or for Chief;
- a worker step erroring on an ordinary Manager's child Ticket;
- a Ticket turn failing when an agent holds that Ticket's review;
- a [measure source](measures.md) failing when that Manager or Chief is responsible for it.

Each wake is recorded in the same write as the event that caused it, then delivered by a loop, so a crash cannot lose it. A notice that has gone stale, such as a proposal already decided, is dropped rather than sent. Replying to a wake approves nothing. Proposals are decided only through the [Ticket lifecycle](tickets.md).

When the event concerns Khushal rather than an agent, it reaches him through [Review](review.md) and [notifications](notifications.md) instead.

## Settlement

Settling a Manager is a presentation control, and only Khushal may do it. A settled Manager leaves the active part of the Workspace rail and can be reopened from Settled. Its children keep running, their approvals stay in Review, and messages still work. Settled means "don't put new work here", not "stop". Each rail card carries its background and goal tint with its words through movement, size changes, and fades.

## Launch setup

Khushal and Chief set the default backend, model, and reasoning for new Managers, and can change any one Manager's setup. A new Manager copies the defaults. A change applies to the next message and does not interrupt a running turn. An existing conversation keeps its backend. Switching backend means starting a new conversation.

## Where it shows up

Manager and Chief pages display their text without owner edits. Agents still update the text through their tools. Charts, tables, checklists, and conversations retain their controls. Separate Goal pages retain text edits.

The Workspace shows Chief first, then active Managers with their Tickets grouped by the attention they need, then the Settled fold. A Manager page has its header, recap, conversation, and current work. If it holds exactly one goal, that goal's document appears under the Manager name. Otherwise, its own document appears with active held goals as cards.

A Tickets block places the Workers within the document. Without that block, they appear below it. See [Goals](goals.md).

`panels manager workspace` explains which document the browser displays and gives its edit command. `panels page` reads and edits Manager and Chief documents. `panels goal` edits a held goal's document. Their schema commands explain the accepted content. The plain purpose, recap, and artifacts remain separate.

## Decisions that shape it

- **No private API.** A Manager's power is exactly what the authority rule gives it over its current children.
- **Assignment is not re-assignable by the assignee.** Handing a Ticket away would let a Manager give away authority it holds, so only Khushal and Chief move work.
- **Wakes are written with their cause.** A supervisor is never told about something that did not commit, and never misses something that did.
- **Settlement hides. It does not stop.** Work in flight always finishes or fails on its own terms.

## Code

- `src/planner/managers/`: the record, settlement, and launch settings.
- `src/planner/manager_wakes/`: wake creation and delivery.
- `src/planner/worker_settings/`: Chief's own settings.
- `web/src/components/ManagerWorkspace.svelte` and `web/src/components/ChiefConversation.svelte`: the screens.

_Last verified: 2026-11-11._
