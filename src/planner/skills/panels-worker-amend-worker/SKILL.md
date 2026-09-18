---
name: panels-worker-amend-worker
description: Stage-by-stage guidance for an amend-worker ticket — changing an existing Worker type in place.
---

# Amending an existing worker

This Ticket changes a Worker that already works. One Worker per Ticket.

Structural change is the normal case: a Stage is removed, or added, or an ownership mode, a runtime default, or the skill guidance changes. Removal is the most common, and it is usually correct — a Stage earns its place, and two Stages that always get approved together are one Stage. Do not argue the user out of a removal.

Small skill-text edits are not this worker's job. The user makes those directly.

**Stages: Kickoff → Amendment → Drafting → Closeout → Done.**

## The two authorities

The two halves of a Worker live in different places and land differently. This catches people.

- **Structure** — stages, fields, ownership modes, runtime defaults — lives in `src/planner/worker_types/<type>.py`. It ships through the repo and takes effect on deployment.
- **Skill text** lives in the managed home at `data/skills/panels-worker-<name>/SKILL.md`. That copy is the live authority. `src/planner/skills/...` only seeds a home that has none. A deployment never overwrites a managed skill that already exists.

Editing the repo copy of a skill changes nothing for a running worker. Editing only the managed copy means the change is missing from any fresh install. Skill-text amendments land in both.

## needs_amendment — agree the change and its blast radius (paired)

Read the current definition and skill before you say anything. Then agree with the user what changes, and report what it does to live tickets of that type.

Look before you propose. `panels ticket list --json` names the live tickets and where they sit. Report three things:

- Tickets sitting in a Stage that is being removed. These are stranded. The repository change must include an explicit data migration to a surviving Stage.
- Tickets whose ceiling names a removed Stage.
- Everything else, which is fine — including done tickets holding content in a removed field. That content goes dark. That is accepted loss. Do not rewrite history.

Give the count and the ids. "Some tickets may be affected" is not a blast radius.

The user decides what happens to anything stranded. That decision belongs in this field.

## needs_drafting — write the revised files

Write the revised definition module, and the revised `SKILL.md` if the skill changes, as Ticket artifacts. Nothing goes live in this Stage.

The point is that the exact stage tuple gets read before a restart, not after one. Show what changed, not the whole file, when the change is small.

Skill text is instruction for a capable agent, not documentation. Amending a skill is more often deleting than adding.

## needs_closeout — land it

1. `src/planner/worker_types/<type>.py` — the definition.
2. `tests/unit/test_<type>_worker_type.py` — the pinned manifest. It asserts stage ids, fields, the advance map, the ceiling range, backend, model, and effort. It is the real spec. An amendment that does not update it is a failing build, not a finished change.
3. `docs/worker-types.md`.
4. Skill text, when it changed: the repo copy under `src/planner/skills/`, and the live managed copy. `./skills` is a symlink to the repo copy, so it needs nothing.
5. `src/planner/worker_types/configuration.py` and `src/planner/environments/hermes_home.py` only when a skill is added or renamed. A pure stage change touches neither.
6. Run `./verify`.
7. Complete the repository-defined integration route without deploying: bring current `staging` into the Ticket branch, repair and verify the prospective result, advance `staging` only when it is green, push that exact revision to `origin/staging`, verify the remote ref, and ensure the single rolling `staging` → `main` pull request exists.
8. Deployment from `main` is a later user action. Do not dispatch deployment, merge or push `main`, restart Panels, or modify the deployed application.
9. Include the migration for stranded Tickets in the repository change. Do not rely on a later product operation.

A good closeout is short and verified: what changed, which managed skill copy took effect now, which repo copy ships, the exact pushed `staging` revision, and which migration ships with it.

## Boundaries

- One Worker per Ticket. If the change clearly belongs on a second Worker too, name it and stop.
- Repair only what is stuck.
