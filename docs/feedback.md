# Feedback

Feedback is the loose inbox for small product notes. It is separate from Ideas because
an Idea remembers a possibility, while Feedback records something the user wants an
agent to handle.

The Feedback control is available from every page. A note requires only text. Panels can
also store the current page address and a readable label. The user can remove that page
context before save. An unfinished draft stays on that device, and a failed save keeps
the text in the composer.

The Feedback page sits under Planning in the More menu. Open notes appear newest first
with their page link and time. Dismiss moves an open note to Handled, and Undo restores
it for six seconds. Any handled note can return to Open.

Handled notes show what happened. Notes used for one Ticket form a group with that
Ticket's title and state. Notes dismissed without a Ticket appear last. If that Ticket is
deleted, its notes remain handled and move to the dismissed group.

Agents use the command line. `panels feedback list` reads open notes. The command
`panels feedback use --ticket <ticket-id> <feedback-id>...` marks the selected notes as
used in one Ticket. The update is atomic and follows the caller's Ticket or Sprint Item
scope.

_Code paths:_ `src/planner/feedback/`, `web/src/components/FeedbackCapture.svelte`,
`web/src/routes/FeedbackRoute.svelte`.

---

_Last verified: 2026-09-11._
