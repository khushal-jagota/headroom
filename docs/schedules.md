# Schedules

A schedule creates an ordinary Ticket every day at one local minute. It exists so a known kind of recurring work appears without anyone remembering to create it. The schedule's job ends at creation. It does not start the Worker, choose a gate, or send a prompt.

## What a schedule is made of

- **A time of day.** New schedules are daily.
- **A Ticket template**: the title, Worker type, a kickoff note that becomes the Brief, the assignment to Chief or a Manager, the project, launch choice, locale, and blockers.
- **An enabled switch.**
- **Occurrence receipts**, one per schedule per minute it fired, recording whether the Ticket was created or the attempt failed.

## How a minute becomes a Ticket

```text
schedule loop ── due this minute? ──► ordinary Ticket creation, as the owner
                                          │
                                          └─ receipt for this schedule and minute

commit ──► change signal ──► worker-step loop starts the Ticket like any other
```

A loop inside the server checks the current local minute. Each due schedule creates its Ticket through the same creation path everyone uses, acting as the owner, so the usual validation and ceiling rules apply. The receipt is written in the same transaction. A repeated check finds the receipt and does nothing. One schedule failing does not stop the others.

From there the Ticket is ordinary. The [worker-step loop](worker-steps.md) notices it and sends the first prompt.

Minutes the host was down are not backfilled. Receipts only stop a schedule from repeating its own minute. They do not stop two schedules from creating similar Tickets.

## Who controls it

Schedules are owner-level configuration. Only Khushal and Chief create, edit, enable, or disable them. A Manager assigned a schedule's Tickets does not control the schedule. See [Who may act](authority.md).

Schedules from retired planning workflows remain as history and can only be disabled.

## Where it shows up

Settings → Scheduled tasks in the browser, `panels schedule` on the command line.

## Decisions that shape it

- **Supply, not execution.** A schedule only creates a Ticket. Starting work stays with the one loop that starts every Ticket.
- **No catch-up.** A missed minute is missed. Recurring work should not arrive in a burst after downtime.
- **One receipt per minute.** The receipt, written with the Ticket, is what makes a fire happen at most once.

## Code

- `src/planner/scheduled_tickets/`: records, the due check, creation, and the loop.
- `src/planner/core/loops.py`: where the loop starts with the server.

_Last verified: 2026-11-04._
