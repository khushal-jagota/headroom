---
name: "panels-ticket-creation"
description: "The shared Panels model for creating a coherent Ticket."
---

## Retired Worker catalog

The supported catalog excludes `personal`, `planning-day`, `planning-midday-check`, and
`planning-sprint`. Do not create, retype, restore, or schedule work with those types.
Use the supported Worker catalog when new work needs a Ticket. Do not recreate planning
routines or replacement recurring reviews.

Existing personal Tickets keep their identity, records, proposals, and ordinary Stage
gates. Their removal from the catalog does not cancel them, complete them, grant agent
permission, or widen their ceiling. Preserve all 19 personal Tickets. Cancellation
applies only to the predecessor's reviewed planning cohort. Do not expand that cohort.

# Creating a Ticket

Use a Ticket for a concrete unit of work that one Worker can carry through its
lifecycle. A Manager is the broader outcome that can own several Tickets; an idea
is a loose thought that is not committed work. Choose a preset whose workflow fits the job, or define a custom workflow. A preset supplies stages, specialist guidance, and launch defaults. Each new Ticket owns its stages.

For custom work, specify each stage’s name, owner, result field, and instructions. Put the stages in order and review the whole workflow. Include any guidance that applies across stages. An adapted preset keeps its initial launch choices unless you change them. Pure custom work must name its backend and model. Custom guidance reaches the Worker as one complete guide at kickoff. No new Worker type is necessary.

Give the Ticket a clear outcome-oriented title and a Brief that stays top level. The
Worker reading it was not in the room: it needs to understand the work, know why it
matters, and know what is still undecided. Carry the user's reason for the work, not
only the facts. Ground references to existing work, preserve agreed boundaries and
dependencies, and do not turn creation into premature planning.

## A Brief's text and approval

Supplying a Brief does not approve it. With the default ceiling at Brief, supplied text becomes
a proposal for review. If an authorized creator sets the ceiling past Brief, the supplied text
settles and the Ticket advances to its next stage.

If creation supplies no Brief text, the Ticket stays at Brief without a proposal. Its Worker
writes the missing text, and the same ceiling rule determines whether it needs review. Empty or
whitespace-only text counts as no text.

There are two possible authors, but one approval rule. Automatically approving creator-supplied
text also approves agent-authored work without the default review.

## Explicit manager assignment

A Worker names Chief or one ordinary manager. Inspect existing Managers and
choose the one that describes responsibility for the job. Use Chief when no ordinary
manager applies. Do not create a thin fallback Item for Chief.

The canonical field is `manager`, with `kind` and `id`. Chief is
`{"kind":"chief","id":"chief"}`. An ordinary manager is
`{"kind":"manager","id":"<existing-item-id>"}`. Former Manager IDs and
supervisor conversations remain stable. Assignment is independent of the ceiling holder
and Chief's global authority. The ceiling is what the Worker does unsupervised, and the
holder is whoever supervises it: you, the owner, or a Manager. Existing Project validation still applies.

Today and live Sprint placement are retired. Creation does not create a Day or Day
membership. Workers continue through normal rules without Today. Do not use settlement
as a replacement activation gate, and do not create the retired planning Worker types
or replacement recurring reviews.

## Timing and dependencies

A real calendar constraint belongs in the deadline. A genuine prerequisite belongs in
the Ticket's blockers so Panels can hold the dependent work until the prerequisite is
resolved.

After creation, read the Ticket back as a whole. Its title, Worker type, Brief,
Project, explicit manager assignment, deadline, and blockers must tell
one coherent story. Correct a mismatch through its canonical surface.
