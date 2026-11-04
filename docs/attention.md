# Historical attention

Historical attention answers one question: where did Khushal's recorded involvement go between two dates? Chief uses it to compare Projects, then look at the work behind one, before discussing another commitment. It looks backward only. It does not report what is waiting now, and it does not forecast capacity. [Review](review.md) and [Notifications](notifications.md) use the word "attention" for what is waiting on him now. This page is the history.

## What it counts

For a date range, Headroom groups three things by Project, Manager, Ticket, or goal, and can scope the answer to one of them or to Chief:

- **Owner messages.** Prompts Khushal sent.
- **Owner approvals.** Explicit acceptances by him. Automatic acceptance and agent approval do not count.
- **Approval waits.** Elapsed time that a Ticket sat at an open gate.

Each group also shows the preceding period of equal length, the difference, its share of the total, and the share of active Tickets with no owner involvement. Dates are inclusive and fixed to the Europe/Berlin calendar. Today is partial.

## Where the facts come from

```
 owner accepts a proposal ───────► approval event ──┐
 a Ticket opens or leaves a gate ► gate interval ───┼─► calculator ─► summary
 prompt events, with their sender ─────────────────┘
```

Approval events and gate intervals are written when they happen, in the same transaction as the action. Messages are read from conversation events, where the authenticated sender decides whether a prompt is the owner's. Agent prompts and replays are not.

## Decisions that shape it

- **No estimate of owner time.** Headroom sees messages and decisions, not reading time or work on the Mac. A gate wait is an elapsed delay, not hours of attention, so waits are never summed into a time figure. Chief asks Khushal about the rest.
- **Unknown is not zero.** Older history can lack approvals, gates, or senders. A figure says what it covers and what it cannot know.
- **Goals are partial.** Headroom has no link between Tickets and goals, so a goal figure covers only its own conversations. Other work stays unattributed.
- **Totals are stable.** Paging and sorting never change totals or shares.

## Where it shows up

Only through `panels attention summary` and `GET /api/attention/summary`. No screen reads it. The same daily figures can also be retained as [measures](measures.md), so a goal chart can bind them. A setup command creates that collection source.

## Code

`src/planner/attention/`: history capture, the calculator in `logic/`, the CLI, and the collection setup.

_Last verified: 2026-11-04._
