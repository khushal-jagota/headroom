---
name: panels-rollover
description: Carry Panels work across the 5am planning-day boundary and prepare today's kickoff for review.
---

# Panels rollover

Use this when the user starts rollover or a scheduled check finds that today's kickoff has not been prepared.

Panels is the source of truth. Use the `panels` CLI; do not edit the old planner files or the database directly.

## Rules

- The planning day changes at **5am local time**.
- Never carry `done` or `dropped` tickets forward.
- Trust the stored Panels Ticket Stage; commits and implementation reports are supporting evidence only.
- Carry only obvious unfinished work, not the whole sprint backlog.
- Keep broad reprioritization in sprint planning.
- Do not bypass ticket gates.

## 1. Read the boundary

Read today, the previous day, their tickets, and the current sprint:

```sh
panels day show --date today --json
panels day list-tickets --date today --json
panels day show --date YYYY-MM-DD --json
panels day list-tickets --date YYYY-MM-DD --json
panels sprint show current --json
panels sprint item list --sprint current --json
panels ticket list --sprint-item <item-id> --json
```

Work out what finished, what remains unfinished, what is waiting for approval or blocked, and which unfinished tickets are obvious carryover candidates.
Read the children of every current-sprint item. Surface children under `kind: other`
items explicitly in the kickoff draft and review so fallback placement never hides work
that needs shaping or a carryover decision.

Do not rewrite a Ticket Stage during rollover. Surface uncertain choices instead of inventing them.

## 2. Prepare today's kickoff

Write a likely direction into today's overview:

- **Focus** — the likely main direction.
- **Brief take** — why that direction makes sense from the boundary.
- **Watchout** — the main distraction or risk.
- **If today lands** — what the day would unlock.

Use `panels day set` for those fields.

For an automatic run, treat this as a draft for the user to inspect. Record the small set of obvious carryover candidates in the day notes as pending review, but **do not add tickets to today yet**.

If today's kickoff already exists, preserve it and stop unless the user asks to reshape it.

## 3. Review with the user

The user checks today's kickoff and either agrees or corrects it.

Keep the exchange short. Call out only:

1. any non-obvious reconciliation decision;
2. your direct read on the old day;
3. the likely carryover tickets.

A response such as “yes, that looks right” is enough approval to continue.

## 4. Add the carryover

After the user agrees:

- add the approved carryover tickets with `panels day add-ticket`;
- create a ticket only when concrete missing work was agreed;
- remove the pending-review note or replace it with any useful final day note.

Do not add `done`, `dropped`, speculative, or ordinary backlog tickets.

## 5. Check the result

Read today back. Confirm that its overview and ticket set match the agreed direction.

## Scheduled checks

Cron prompts stay thin and load this skill.

- **Morning:** prepare the kickoff draft if it is missing, then stop for review.
- **Afternoon:** act only as a failsafe—prepare the same draft if it is still missing; otherwise no-op.
- Scheduled runs never add tickets to today without the user's agreement.
- Hermes cron sessions inherit worker identity, while Day overview and note writes are direct-only. For scheduled checks, prefix only the approved kickoff-draft `panels day set` commands with `PLAN_ACTOR=chief`. This authority is limited to the approved kickoff draft; never use it to edit a Ticket Stage, approve gates, or add tickets before the user agrees.

If Panels is unavailable, report the blocker. Never fall back to the old files.
