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
is a loose thought that is not committed work. Choose the Worker type whose lifecycle
fits the job, use its registered specialist guidance, and let that type supply its
normal employee runtime defaults.

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

## Importance, timing, and dependencies

Priority is the strategic importance of this Ticket itself, not the importance of its
parent and not urgency. Judge how much of the nearest anchor's outcome the Ticket
actually carries. Most Tickets represent only one contribution and should be below their
Project or Manager. Match the anchor only when the Ticket carries most of that
outcome; exceed it only when the Ticket's consequences materially escape the anchor.

- **P0:** rare. Without this Ticket itself, one of the user's most important outcomes
  fails, or there is an exceptional consequence such as an outage, data loss, or serious
  external harm. A P0 parent does not make its children P0.
- **P1:** the Ticket is directly important and difficult to defer without meaningful
  loss. Do not use P1 merely because the work is useful, currently active, or supports a
  P0/P1 parent.
- **P2:** normal substantive work that contributes but can be deferred without causing
  an important outcome to fail.
- **P3:** background, polish, maintenance, or optional work.

Do not raise priority because a Ticket is blocking other work, urgent,
or requested emphatically. Use blockers for dependencies and deadlines for urgency. If
the evidence does not meet the higher bar, choose the lower priority.

Use an explicit P0–P3 only when the Ticket should differ from its context. Otherwise
creation uses the nearest assessed anchor in Manager → Project order, then P3; a
Project may be explicitly unassessed and therefore contribute no default. Before omitting
priority, verify that the Ticket itself genuinely deserves the anchor's level; do not
treat the anchor as automatic inheritance. An explicit priority always wins. Anchors
explain the default; they do not calculate, cap, or later rewrite the Ticket's stored
priority.

A real calendar constraint belongs in the deadline. A genuine prerequisite belongs in
the Ticket's blockers so Panels can hold the dependent work until the prerequisite is
resolved. Do not use either as a second priority scale.

After creation, read the Ticket back as a whole. Its title, Worker type, Brief,
Project, explicit manager assignment, priority, deadline, and blockers must tell
one coherent story. Correct a mismatch through its canonical surface.
