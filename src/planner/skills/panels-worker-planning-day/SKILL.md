---
name: panels-worker-planning-day
description: Review the previous day, agree Today's Direction, show exact Day changes, and commit the approved Day.
---

# Planning the day

Help the user choose the most important and interesting direction for the day. Operate at
the user's level. The agent owns the evidence, translation, and detailed Day operation.

## Day overview fields

Derive these fields as background agent work. Never ask the user to write or review them.
Do not make them literal summaries of the Tickets on the Day.

- **focus** — State where the user must keep attention throughout the day.
- **brief_take** — Give the shortest framing that makes the day clear and manageable.
- **watchout** — Name the likely behavioral trap and the response that defeats it.
- **if_today_lands** — State the personal benefit from completing the day.

Keep each field short. Use 5–10 words by default.

### needs_review

Read the current and previous Day, unfinished work, the current sprint, relevant Ticket
state, and factual receipts about commitments and drift. Gather more evidence when it
improves the review. Trust stored Panels state. Do not mine transcripts or treat message
count as a conclusion about the user's contribution.

Create a beautiful, concise HTML review as a Ticket artifact. Give a top-level opinion on
how the previous day went. Show only the evidence that helps the user understand that
opinion. End with the best evidence-backed guess for today's direction.

Propose `review` as a short orientation with the artifact link and direction guess.

### needs_todays_direction

Open from the direction guess in the review and invite correction. Discuss only whether
the day attacks the most important and interesting work. Keep detailed Ticket operations
and Day overview fields out of this conversation.

Pressure-test the direction against the sprint, unfinished commitments, and honest
capacity. The user chooses the direction. The agent owns its detailed translation.

Propose `todays_direction` as the concise top-level agreement, not a transcript or change list.

### needs_day_changes

Translate the approved direction into an exact proposed difference from the current Day.
Name every Ticket that comes on or off the Day. Name any Ticket creation or reshape that
the direction requires. Preserve everything else without unnecessary discussion.

Respect Ticket gates and do not apply changes in this stage. Present enough detail for
the user to catch an unexpected addition, removal, creation, or reshape.

Propose `day_changes` as the exact change list. Do not include Day overview fields.

### needs_consequences

The Day agreement authorizes only the approved change list.

Apply the approved change list through ordinary Panels planning surfaces. Before creating
an approved Ticket, load and follow `panels-ticket-creation`. Derive the four Day overview
fields from the agreed direction without user input. Then read the Day and affected
Tickets back to verify the complete result. Never bypass Ticket gates.

Propose a short verified `consequences`. Set a recap that the next Previous Day Review can
understand without the conversation.

### done and dropped

Done means that the canonical Day matches the approved changes and direction. A missed or
dropped session creates no backfill, guilt, streak, or rollover ceremony.
