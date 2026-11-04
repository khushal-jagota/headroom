# Ideas

An Idea is a possibility Khushal wants to keep without committing a Worker. It is the opposite of a [Ticket](tickets.md), which has stages, a conversation, and a gate. An Idea has a title, an optional note, and an optional Project. That is the whole object, on purpose.

## How it works

The Ideas page leads with a capture box, with the Project catalog shared with Tickets and Managers. Below it, Ideas are listed newest first. Capture feeds the list and the list feeds nothing else. A person turns an Idea into work by creating a Ticket, or by asking Chief or a Manager to. Nothing promotes an Idea automatically, and Ideas cannot be edited, archived, or deleted yet. Those were left out until there is a product decision to add them.

An Idea is not [Feedback](feedback.md). Feedback is a note about the product for an agent to act on. An Idea is future work for a person to weigh.

## Where it shows up

The Ideas page, under Work in the shell and inside the Workspace. Capture and list are also open through the HTTP API. There is no `panels` command for Ideas.

## Code

`src/planner/ideas/` and `web/src/routes/IdeasRoute.svelte`.

_Last verified: 2026-11-04._
