# Ticket capture boundaries — 2026-07-09

Session learning from live Panels planning.

## User corrections

- A title-only ticket is often too thin. A title names the object but does not preserve the user's intent, boundaries, source context, or exact wording.
- The missing primitive should be a **user note** / intake note, not a generic ticket body. It should preserve why the ticket exists and the user's direction without competing with `success`, `approach`, `plan`, or `result`.
- Field notes are primarily preserved user guidance for a specific step. Agents may fill them from user input, including guidance given directly to a worker, but should treat them as user direction rather than agent scratchpad.
- Recap is a short cold-user reorientation field: it works alongside the title and should say what the ticket is, where it stands now, and the one or two key facts relevant to the current step. It is not a detailed log.
- If the user says they review tickets elsewhere, do not monitor tickets directly by default. Monitoring blocks other help. Set up or organize the ticket, report ids/state, and stop unless asked to drive it.

## Practical pattern until first-class user notes exist

When creating a ticket from conversational planning:

1. Create the ticket with the right project/sprint/day placement.
2. If the user supplied important boundaries or step-specific direction, preserve that as field notes on the most relevant gated field.
3. Phrase field notes as `User direction: ...` when helpful for clarity.
4. Do not fill canonical gated field values unless the user explicitly asked for that or an approved workflow requires it.
5. Report the ticket id and current state/status; do not wait for workers unless asked.
