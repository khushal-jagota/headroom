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

A Worker type is a reusable preset in the database. Each Ticket owns its copied stages. A preset amendment affects future Tickets.

Read the structured record with `panels worker-type show <type>`. Its `guidance` and stage `instructions` generate the specialist skill. The structured record is the editable source.

Preset Tickets read that specialist skill as usual. Changes to its instructions reach those Workers when they read the skill again. Their saved stages do not change.

Only the owner or Chief can save the preset. Prepare exact bytes and hand them up. Never override your principal.

## needs_amendment — agree the change (user-owned)

Read the current structured definition and specialist skill. Agree on the preset changes and the effect of shared specialist instructions on existing Workers.

Preset stage edits never move existing Tickets, remove their fields, or change their approvals. Do not move Tickets to accommodate a preset edit. Active workflow replacement is outside this process.

## needs_drafting — prepare the revised record

Write the revised structured record as a Ticket artifact. Put instructions beside their stages and common guidance in `guidance`. Include a generated skill preview. Do not edit the specialist body independently.

Show the meaningful changes. Preserve the instructions that the user did not change. Nothing is saved in this stage.

## needs_consequences — hand up the save and verify it

1. Validate the prepared record against the current workflow and preset rules.
2. Hand the owner or Chief `panels worker-type save` with the exact approved record.
3. Record the artifact digest for comparison with the approved bytes.
4. Read back the definition and generated specialist skill.
5. Confirm that existing Ticket stages and approvals remain intact.
6. Update the repository seed and relevant documentation through the ordinary branch route.

The structured record and generated skill save together. Do not use a second specialist-body write.

Write a short, verified Consequences report from the saved result.

## Boundaries

- One Worker per Ticket. If the change clearly belongs on a second Worker too, name it and stop.
- Repair only what is stuck.

