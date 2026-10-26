---
name: "panels-worker-amend-worker"
description: "Stage-by-stage guidance for an amend-worker ticket \u2014 changing an existing Worker type in place."
---

# Amending an existing worker

This Ticket changes a Worker that already works. One Worker per Ticket.

Structural change is the normal case: a Stage is removed, or added, or an ownership mode, a runtime default, or the skill guidance changes. Removal is the most common, and it is usually correct — a Stage earns its place, and two Stages that always get approved together are one Stage. Do not argue the user out of a removal.

Small skill-text edits are not this worker's job. The user makes those directly.

**Stages: Brief → Amendment → Drafting → Consequences → Done.**

## Where a Worker lives

A Worker is declared in the database, in one place, and an amendment takes effect at once. There is no repository change and no deployment.

- **Structure** — stages, fields, ownership modes, runtime defaults. Read it with `panels worker-type show <type>`, which prints the record. It is written back with `panels worker-type save`, which reads the whole record on stdin.
- **Skill text** — `panels worker-type skill <type> --description "..."`, with the markdown body on stdin. Panels writes the row, writes the file every agent reads, and records the exact bytes.

You can read both. You cannot write either: both endpoints are owner-only, and no Ticket principal of any type can reach them. That is deliberate, not a fault to work around. Overriding your principal would be forging one. So you prepare the exact bytes and hand them up — see Consequences.

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

## needs_consequences — get it saved, then verify it

The two writes are not yours to make. Hand them up and verify the result.

1. Move any Ticket the user agreed to move, before the save. Panels refuses a save that would strand one.
2. Prove the record before you hand it over. The refusal arrives before the record is ever looked at, so you cannot learn from it that the record would have been accepted. Parse the prepared record with the deployed validator, and re-run the strand conditions read-only, so whoever runs the save is not running it blind. Report both results.
3. Hand your supervisor, or the Chief if you have none, the two commands to run back to back, in this order: `panels worker-type save` with the approved record on stdin, then `panels worker-type skill <type> --description "..."` with the skill body on stdin, when the skill text changed. Record first, then skill — a skill that describes fewer Stages than the record still gates would deny a Stage that is live.
4. Give the sha256 of each artifact you prepared, so whoever runs it can confirm they ran what was approved, and so you can confirm it afterwards.
5. Read it back yourself with `panels worker-type show <type>`, and read the stored skill bytes. Write Consequences from what the live store returns, never from what was sent.
6. Update `src/planner/skills/panels-worker-<name>/SKILL.md` in the repository when the skill text changed, so a fresh install is seeded with the current text. This is a repository change with no effect on this Panels, and it goes through the ordinary branch and staging route.
7. Update `docs/worker-types.md` when the set of types or their shape changed.

A seeding pass on a fresh deploy will not apply your amendment: seeding declines a table that already holds rows. Only the explicit save does it.

A good Consequences is short and verified: what changed, what `panels worker-type show` reports now, who ran the save, and what if anything is waiting on a repository change.

## Boundaries

- One Worker per Ticket. If the change clearly belongs on a second Worker too, name it and stop.
- Repair only what is stuck.

