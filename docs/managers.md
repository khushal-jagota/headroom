# Managers and Chief

A Manager is a stable place to hold a direction and the Tickets that serve it. It has a goal, a recap, an optional project, child Tickets, artifacts, and its own conversation. The Manager agent commissions and steers Workers. It has no private API. It calls the same routes everyone else calls, and authority limits it to its own record and its current children.

Chief is the special case: an assignment target and a global principal who stands above everything, with a retained conversation and settings. Chief is not stored as an ordinary Manager row. Chief never settles.

Assignment, ceiling holder, and Chief’s global reach are three different facts. A Ticket can be assigned to a Manager, park its proposal for Khushal, and still be approvable by Chief because Chief stands above it.

## Goal, recap, and settlement

The goal is why the Manager exists. A non-blank goal can start the Manager conversation as a queued message. That message does not create a reply debt. The recap is the current status line. A new recap replaces the old one. The owner and the Manager may edit both. Cards show the recap. An empty recap falls back to the child count. It does not fall back to the goal. Chief has a recap and no goal band.

Settlement is an owner-only control on an ordinary Manager. It changes presentation: the Manager leaves the active rail and can be reopened from Settled. It does not stop Workers, hide approvals, block messages, or complete children. Settled is the right destination for new work to avoid. Existing work may continue. Unread conversations can still surface. Chief cannot be settled, and Chief cannot settle anyone. The settle writer checks for the owner directly, which is tighter than the usual “Chief stands above everything” rule.

## Children

The Manager creates children through ordinary Ticket creation. The Workspace lists each child with its attention and its proposal holder, not the full stage bodies. Opening a row opens the Ticket. Deleting a child is permanent and can cut off a Worker mid-turn. A Manager may not re-parent a Ticket. Moving work to another Manager changes who stands above it, and that stays with Khushal and Chief. See [Who may act](authority.md).

Backlogged and Done children are sections on the Manager page. The rail’s attention groups omit backlogged Tickets. Chief’s page shows Chief’s children beside Chief’s conversation, in pages.

## How a Manager hears about work

Three kinds of durable wake become real queued prompts in the Chief or Manager conversation. They are recorded in the same transaction as the fact that caused them. A loop later delivers a batch, and it closes a wake only when that prompt is durably in the notebook. A stale proposal notice is dropped rather than sent. Replying to a wake does not approve anything. Approval is still the proposal resolver.

- A proposal routed to Chief or to that Manager.
- The worker-step claim becoming errored, for an ordinary Manager named on the Ticket. A Chief-assigned Ticket has no Manager id, so this particular wake is not created. The owner sees that failure through attention.
- A Ticket turn that failed, when the ceiling holder is an agent. If the holder is Khushal, he is told through attention and notifications, not through this wake.

The owner, Chief, and Managers talk to each other, and to Workers, through Send Message. Two Managers may message one another. That permission is speech. It does not put either above the other’s Tickets. Backend chatter inside a Manager’s own turn is not delivery to Khushal. Delivery is Send Message.

## What feeds a Manager, and where it shows up

Creation, goal and recap edits, child Ticket activity, and wakes feed the Manager. The Manager feeds child Tickets, messages, and its own recap. Workspace shows Chief first, then active Managers with their Workers grouped by attention, then a Settled fold. A Manager page is the header, the recap, the conversation, and the children. `#/workspace/chief-of-staff` opens Chief. `#/workspace/manager/<id>` opens a Manager. Older `#/workspace/item/…` and `#/agents/chief-of-staff` addresses still redirect.

## Pointers

`src/planner/managers/` is the record. Wakes live in `src/planner/manager_wakes/`. The screens are `web/src/components/ManagerWorkspace.svelte` and `web/src/components/ChiefConversation.svelte`.

_Last verified: 2026-10-30._
