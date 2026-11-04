# Feedback

Feedback is a loose note about the product, captured from whatever page Khushal is on, for an agent to handle later: a rough edge, a wrong sentence, a behavior to change. It is not an [Idea](ideas.md). An Idea remembers possible work for a person. Feedback records something for an agent to act on.

## How it works

A control in the shell opens capture on any page. Headroom can attach the current page address and label, which the owner can remove before saving. Open notes are listed newest first. Dismissing moves a note to Handled, and any handled note can be reopened.

An agent reads the open notes and marks the ones it used, naming the Ticket that took them. Handled notes group under that Ticket's title. Notes dismissed without a Ticket, or whose Ticket is later deleted, sit in a dismissed group. Marking is one write that follows the caller's scope.

## Decisions that shape it

A note never becomes a Ticket and never enters a conversation by itself. It reaches an agent only when the agent reads the list or someone sends its text as a prompt. It is data to act on, not an instruction.

## Where it shows up

The Feedback control on every page, with the open count, and the list at `#/feedback`. Agents use `panels feedback`, a hidden command group, or `/api/feedback`.

## Code

`src/planner/feedback/`, `web/src/components/FeedbackCapture.svelte`, and `web/src/routes/FeedbackRoute.svelte`.

_Last verified: 2026-11-04._
