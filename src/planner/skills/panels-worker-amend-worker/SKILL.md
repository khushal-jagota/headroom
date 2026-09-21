---
name: panels-worker-amend-worker
description: Stage-by-stage guidance for an amend-worker ticket — changing an existing Worker type in place.
---

# Amending an existing worker

This Ticket changes a Worker that already works. One Worker per Ticket.

Structural change is the normal case: a Stage is removed, or added, or an ownership mode, a runtime default, or the skill guidance changes. Removal is the most common, and it is usually correct — a Stage earns its place, and two Stages that always get approved together are one Stage. Do not argue the user out of a removal.

Small skill-text edits are not this worker's job. The user makes those directly.

**Stages: Brief → Amendment → Drafting → Consequences → Done.**

## Where a Worker lives

A Worker is declared in the database, in one place, and an amendment takes effect at once. There is no repository change and no deployment.

- **Structure** — stages, fields, ownership modes, runtime defaults. Read it with `panels worker-type show <type>`, which prints the record. Write it back with `panels worker-type save`, which reads the whole record on stdin.
- **Skill text** — `panels worker-type skill <type> --description "..."`, with the markdown body on stdin. Panels writes the row, writes the file every agent reads, and records the exact bytes.

Both take effect immediately. `src/planner/skills/` is only what a database with no skills in it is seeded from, so a shipped skill and a live one can differ, and the live one wins.

## needs_amendment — agree the change and its blast radius (user-owned)

Read the current definition and skill before you say anything. Then agree with the user what changes, and report what it does to live tickets of that type.

Look before you propose. `panels ticket list --json` names the live tickets and where they sit. Report three things:

- Tickets sitting in a Stage that is being removed. Panels refuses the save while any of them is unfinished, and names them. Agree with the user where each one goes first.
- Tickets holding text in a field that is being removed. Panels refuses that save too, for the same reason: the text would be dropped.
- Everything else, which is fine — including finished Tickets holding content in a removed field. That content goes dark. That is accepted loss. Do not rewrite history.

Give the count and the ids. "Some tickets may be affected" is not a blast radius.

The user decides what happens to anything in the way. That decision belongs in this field.

## needs_drafting — write the revised record

Write the revised record, and the revised skill text if the skill changes, as Ticket artifacts. Nothing is saved in this Stage.

The point is that the exact stages get read before they take effect, not after. Show what changed, not the whole record, when the change is small.

Skill text is instruction for a capable agent, not documentation. Amending a skill is more often deleting than adding.

## needs_consequences — save it

1. Move any Ticket the user agreed to move, before the save. Panels refuses a save that would strand one.
2. `panels worker-type save`, with the approved record on stdin. A refusal is the guard doing its job: read what it names, fix that, and save again.
3. `panels worker-type skill <type> --description "..."`, when the skill text changed.
4. Read it back with `panels worker-type show <type>` and confirm it is what was approved.
5. Update `src/planner/skills/panels-worker-<name>/SKILL.md` in the repository when the skill text changed, so a fresh install is seeded with the current text. This is a repository change with no effect on this Panels, and it goes through the ordinary branch and staging route.
6. Update `docs/worker-types.md` when the set of types or their shape changed.

A good Consequences is short and verified: what changed, what `panels worker-type show` reports now, and what if anything is waiting on a repository change.

## Boundaries

- One Worker per Ticket. If the change clearly belongs on a second Worker too, name it and stop.
- Repair only what is stuck.
