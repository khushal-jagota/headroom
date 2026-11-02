# Ideas

An Idea is a possibility Khushal wants to keep without committing a Worker. It is the opposite of a Ticket. A Ticket has stages, a conversation, and a gate. An Idea has a title, an optional longer note, and an optional project. That is the whole object, on purpose. Promoting an Idea into a Ticket, and archiving an Idea, were left out until there is a product decision to add them. Until then, creating a Ticket is a separate act.

## How capture works

The Ideas page leads with the capture box. A title and Enter saves it. A note and a project are optional. The project list is the same catalog Tickets and Managers use. Below, Ideas are newest first. A title-only Idea is one line. An Idea with a note opens its detail.

Capture feeds the list. The list feeds nothing else automatically. A person reading Ideas later is what turns one into committed work, by creating a Ticket themselves or asking Chief or a Manager to.

## Where it shows up

`#/ideas`, and the same page embedded in the Workspace at `#/workspace/utility/ideas`. The shell groups it under Work. It is not the Feedback inbox. Feedback is a note about the product for an agent to handle. An Idea is a possible piece of future work. See [Feedback](feedback.md).

## Pointers

`src/planner/ideas/` and `web/src/routes/IdeasRoute.svelte`.

_Last verified: 2026-10-30._
