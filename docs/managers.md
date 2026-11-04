# Managers and Chief

A Manager is a stable place to hold a direction and the Tickets that serve it. It has a goal, a recap, an optional project, child Tickets, artifacts, and its own conversation. The Manager agent commissions and steers Workers. It has no private API. It calls the same routes everyone else calls, and authority limits it to its own record and its current children.

Chief is the special case: an assignment target and a global principal who stands above everything, with a retained conversation and settings. Chief is not stored as an ordinary Manager row. Chief never settles.

Assignment, ceiling holder, and Chief’s global reach are three different facts. A Ticket can be assigned to a Manager, park its proposal for Khushal, and still be approvable by Chief because Chief stands above it.

## Goal, recap, and settlement

The goal is why the Manager exists. A non-blank goal can start the Manager conversation as a queued message. That message does not create a reply debt. The recap is the current status line. A new recap replaces the old one. The owner and the Manager may edit both. Cards show the recap. An empty recap falls back to the child count. It does not fall back to the goal. Chief has a recap and no goal band.

Settlement is an owner-only control on an ordinary Manager. It changes presentation: the Manager leaves the active rail and can be reopened from Settled. Workers keep running, Ticket approvals stay in Review, and messages can still be sent. The settled Manager’s own unread conversation leaves Review. Ticket messages under that Manager stay eligible until the Ticket is Done or backlogged. Children are not completed. Settled is the right destination for new work to avoid. Existing work may continue. Chief cannot be settled, and Chief cannot settle anyone. The settle writer checks for the owner directly, which is tighter than the usual “Chief stands above everything” rule.

## Children

The Manager creates children through ordinary Ticket creation. The Workspace lists each child with its attention and its proposal holder, not the full stage bodies. Opening a row opens the Ticket. Deleting a child is permanent and can cut off a Worker mid-turn. A Manager may not re-parent a Ticket. Moving work to another Manager changes who stands above it, and that stays with Khushal and Chief. See [Who may act](authority.md).

Backlogged and Done children are sections on the Manager page. The rail’s attention groups omit backlogged Tickets. Chief’s page shows Chief’s children beside Chief’s conversation, in pages.

## Manager models

Khushal chooses defaults for new Managers in Settings → Config, under the Manager role. Chief can read and save the same defaults through `GET|PUT /api/managers/launch-defaults`. Each new Manager copies those choices. Existing Managers retain their own choices. A fresh installation uses Codex `gpt-6.1-sol` with `medium` effort. The migration preserves existing Managers.

Chief can change one existing Manager through `GET|PUT /api/managers/{id}/launch-defaults`. The owner also retains access to these routes. The complete object contains `employee_backend`, `employee_launch_model`, and nullable `employee_launch_reasoning_effort`. A save sends no message and preserves the conversation and history. An active turn keeps its model. An explicit save applies to the next message, unless its sender explicitly selects another choice. Historical launch metadata does not override an existing conversation.

Queued messages retain the choice that accompanied their admission. An accepted owner picker change also saves that intent, even while its message waits. Later wakes and unchanged messages use that saved choice. A null effort requests the provider default on that next message.

An existing conversation keeps its backend. To change backend, the owner or Chief starts a new conversation first. Ordinary Managers cannot change global defaults or another Manager's settings.

Model choices are exact backend tokens. Catalogs help the picker but do not restrict valid unlisted tokens. New settings reject disabled models. Reads do not reject historical settings. Existing conversations retain their current model until an explicit change. The migration marks historical settings as implicit, because their columns do not prove a later choice. A setting save records explicit intent without a timestamp guess. Normal messages, wakes, and review notices share that distinction.

The former Codex default `sol` is a known unsupported token. If a conversation starts or resumes on that token, the Codex adapter refuses before process startup. The conversation records that refusal with explicit-model guidance. A healthy conversation remains reachable when only its historical launch setting names `sol`. No choice changes silently. If the current conversation model itself is disabled, the existing execution policy still records a durable refusal. Provider availability still requires a real start.

## How a Manager hears about work

Three kinds of durable wake become real queued prompts in the Chief or Manager conversation. They are recorded in the same transaction as the fact that caused them. A loop later delivers a batch, and it closes a wake only when that prompt is durably in the notebook. A stale proposal notice is dropped rather than sent. Replying to a wake does not approve anything. Approval is still the proposal resolver.

- A proposal routed to Chief or to that Manager.
- The worker-step claim becoming errored, for an ordinary Manager named on the Ticket. A Chief-assigned Ticket has no Manager id, so this particular wake is not created. The owner sees that failure through attention.
- A Ticket turn that failed, when the ceiling holder is an agent. If the holder is Khushal, he is told through attention and notifications, not through this wake.

The owner, Chief, and Managers talk to each other, and to Workers, through Send Message. Two Managers may message one another. That permission is speech. It does not put either above the other’s Tickets. Backend chatter inside a Manager’s own turn is not delivery to Khushal. Delivery is Send Message.

## What feeds a Manager, and where it shows up

Creation, goal and recap edits, child Ticket activity, and wakes feed the Manager. The Manager feeds child Tickets, messages, and its own recap. Workspace shows Chief first, then active Managers with their Workers grouped by attention, then a Settled fold. A Manager page is the header, the recap, the conversation, and the children. `#/workspace/chief-of-staff` opens Chief. `#/workspace/manager/<id>` opens a Manager. Older `#/workspace/item/…` and `#/agents/chief-of-staff` addresses still redirect.

Current agent identities use `PLAN_ACTOR=manager` and `PLAN_MANAGER_ID`. The forward migration preserves each Manager and its conversation. Retained conversations receive the current role instructions on their next turn. Old actor names, command aliases, request headers, and environment names do not select a Manager.

Manager files retain `/files/sprint-items/` addresses so that existing Ticket links still open. Historical migration backfills remain isolated from current navigation writes.

## Pointers

`src/planner/managers/` is the record. Wakes live in `src/planner/manager_wakes/`. The screens are `web/src/components/ManagerWorkspace.svelte` and `web/src/components/ChiefConversation.svelte`.

_Last verified: 2026-11-02._
