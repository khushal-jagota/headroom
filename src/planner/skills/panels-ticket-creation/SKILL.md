---
name: panels-ticket-creation
description: The shared Panels model for creating a coherent Ticket.
---

# Creating a Ticket

Use a Ticket for a concrete unit of work that one Worker can carry through its
lifecycle. A Sprint Item is an optional broader-outcome classification for several
Tickets. An idea is a loose thought that is not committed work. Choose the Worker type whose lifecycle
fits the job, use its registered specialist guidance, and let that type supply its
normal employee runtime defaults.

Give the Ticket a clear outcome-oriented title and a light, faithful Kickoff. Intake is
the user's request plus only the factual context the Worker needs to understand it.
Ground references to existing work, preserve agreed boundaries and dependencies, and
do not turn creation into premature planning.

## Two kinds of placement

Today and sprint placement answer different questions. Ordinary creation atomically
adds the Ticket to today, making it part of the active execution roster. This is true
whether the Ticket is in a sprint or the backlog. If the user explicitly wants it off
today, remove it from the Day as a separate follow-up; backlog does not mean not today.

Each Ticket owns its Project and optional Sprint placement. With no explicit Sprint
choice, creation uses the current Sprint. Without a current Sprint, the Ticket stays in
backlog. Explicit backlog placement also leaves the Ticket outside a Sprint.

Sprint Item membership is optional classification. Inspect the relevant Project and
Sprint Items, and use an existing Item only when it genuinely describes the Ticket. The
Outcome's Project must match the Ticket; Sprint placement is independent. Do not create an Item only to classify
otherwise coherent work. Unclassified Sprint Tickets appear in the view-only Other
group, not in a stored fallback Item.

Route `planning-day`, `planning-midday-check`, and `planning-sprint` Tickets to the
Personal Project and the intended Sprint, without creating a Planning container. Keep an `initiative_planning` Ticket
with its initiative Project, Sprint, and optional Item.

## Importance, timing, and dependencies

Priority is the strategic importance of this Ticket itself, not the importance of its
parent and not urgency. Judge how much of the nearest anchor's outcome the Ticket
actually carries. Most Tickets represent only one contribution and should be below their
Project or Sprint Item. Match the anchor only when the Ticket carries most of that
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

Do not raise priority because a Ticket is blocking other work, on today's roster, urgent,
or requested emphatically. Use blockers for dependencies and deadlines for urgency. If
the evidence does not meet the higher bar, choose the lower priority.

Use an explicit P0–P3 only when the Ticket should differ from its context. Otherwise
creation uses the nearest assessed anchor in Sprint Item → Project order, then P3; a
Project may be explicitly unassessed and therefore contribute no default. Before omitting
priority, verify that the Ticket itself genuinely deserves the anchor's level; do not
treat the anchor as automatic inheritance. An explicit priority always wins. Anchors
explain the default; they do not calculate, cap, or later rewrite the Ticket's stored
priority.

A real calendar constraint belongs in the deadline. A genuine prerequisite belongs in
the Ticket's blockers so Panels can hold the dependent work until the prerequisite is
resolved. Do not use either as a second priority scale.

## Scope stated at creation

By default a new Ticket parks its Kickoff for the user's approval. That default is right
for ordinary intake: the user wants to sense-check what work exists before it starts.

State the scope instead when the user gave you the scope to grant. `--ceiling` says how
far the new Worker may go, and `--at-cap` says what happens there: `stop` prevents a
proposal, and `propose` parks one for the user. Stating a ceiling past kickoff settles the
Kickoff and starts the Ticket at its next Stage, so work the user has already authorized
begins instead of waiting for a second approval.

State only the scope you were actually given. Widening a Ticket beyond what the user
authorized is not a creation detail.

After creation, read the Ticket back as a whole. Its title, Worker type, Kickoff,
today status, direct Project and Sprint, optional Sprint Item, priority,
deadline, and blockers should tell one coherent story. Correct a mismatch through the
ordinary owning surface rather than compensating for it in prose.
