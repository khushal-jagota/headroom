# Feedback

Feedback is a loose note about the product, captured from whatever page Khushal is on, for an agent to handle later. It is not an Idea. An Idea remembers possible work. Feedback records something to act on: a rough edge, a sentence that is wrong, a behavior to change.

## How a note moves

The shell control opens a capture overlay on any page. The note needs text. Headroom can store the current page address and a readable label, and that context can be removed before save. An unfinished draft stays on the device. A failed save keeps the text.

Open notes are newest first, with the page link and the time. Dismiss moves a note to Handled. Undo restores it for six seconds. Any handled note can return to Open. Handled notes that were used for one Ticket group under that Ticket’s title. Notes dismissed without a Ticket appear with the dismissed group. If the Ticket is later deleted, those notes stay handled and move to the dismissed group.

An agent reads open notes with `panels feedback list` and marks the ones it used with `panels feedback use --ticket …`. That update is one write, and it follows the caller’s Ticket or Manager scope.

## What it feeds

Capture feeds the open list. Using a note feeds the handled history and the link to the Ticket that took it. The note does not become a Ticket and does not enter a conversation unless someone sends its content as a prompt.

## Where it shows up

The Feedback control is in the shell on every page. The list is `#/feedback`. The open count sits on the trigger. A toast after capture can jump to the list.

## Pointers

`src/planner/feedback/`, `web/src/components/FeedbackCapture.svelte`, `web/src/routes/FeedbackRoute.svelte`.

_Last verified: 2026-10-30._
