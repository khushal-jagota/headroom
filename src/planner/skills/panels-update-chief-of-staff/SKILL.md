---
name: panels-update-chief-of-staff
description: Send an update from the current conversation to the Panels Chief of Staff through Hermes. Use when the user invokes $panels-update-chief-of-staff or asks to update, brief, or tell the Chief of Staff about completed work, progress, decisions, blockers, deferrals, or new context.
---

# Panels Update Chief of Staff

Prepare and send an update to the Panels Chief of Staff.

## Prepare the update

Use the user's explicit instruction together with the current conversation.

When no specific message is supplied, identify the meaningful new information the Chief of Staff should know:

- Completed or started work
- Status changes
- Decisions and constraints
- Blockers
- Deferrals
- New work or context

Write the message in the user's voice and make it understandable without access to the conversation.

For substantial work, explain what the work was trying to achieve, what now exists or behaves differently, the important decisions or safeguards, its current status, and anything left for later.

Treat one worker assignment as one ticket unless the user says otherwise. Prefer a concise account of the resulting state over a chronological work log.

## Confirm the message

Show the user the exact message and ask for approval.

Revise it if requested.

## Send the update

After approval, run:

```sh
hermes chat -q "<approved message>" -s panels-chief-of-staff
```

Pass the approved message as one literal argument.

## Return the response

Show the user the Chief of Staff's response. If the Chief asks for clarification or a decision, return that question to the user.
