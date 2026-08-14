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
Item's Project and Sprint must match the Ticket. Do not create an Item only to classify
otherwise coherent work. Unclassified Sprint Tickets appear in the view-only Other
group, not in a stored fallback Item.

Route `planning-day`, `planning-midday-check`, and `planning-sprint` Tickets to the
Personal Project and that Sprint's Planning Item. Keep an `initiative_planning` Ticket
with its initiative Project, Sprint, and optional Item.

## Importance, timing, and dependencies

Priority is strategic importance, not urgency:

- **P0:** rare; without it, one of the user's most important outcomes fails, or the
  consequences materially escape the apparent context.
- **P1:** strongly and directly contributes to what matters to the user.
- **P2:** contributes indirectly or is meaningfully deferrable relative to its context.
- **P3:** background work.

Use an explicit P0–P3 only when the Ticket should differ from its context. Otherwise
creation uses the nearest assessed anchor in Sprint Item → Project order, then P3; a
Project may be explicitly unassessed and therefore contribute no default. An explicit
priority always wins. Anchors explain the default; they do not calculate, cap, or later
rewrite the Ticket's stored priority.

A real calendar constraint belongs in the deadline. A genuine prerequisite belongs in
the Ticket's blockers so Panels can hold the dependent work until the prerequisite is
resolved. Do not use either as a second priority scale.

## Scope stated at creation

By default a new Ticket parks its Kickoff for user review. That default is right for
ordinary intake: the user wants to sense-check what work exists before it starts.

State the scope instead when the user gave you the scope to grant. `--ceiling` says how
far the new Worker may go, and `--at-cap` says who reviews at that ceiling. Stating a
ceiling past kickoff settles the Kickoff and starts the Ticket at its next Stage, so the
work begins without a park that nobody present can resolve. `--at-cap agent_review`
requires a normal Sprint Item parent, because that Item owns the reviewer.

State only the scope you were actually given. Widening a Ticket beyond what the user
authorized is not a creation detail.

After creation, read the Ticket back as a whole. Its title, Worker type, Kickoff,
today status, direct Project and Sprint, optional Sprint Item, priority,
deadline, and blockers should tell one coherent story. Correct a mismatch through the
ordinary owning surface rather than compensating for it in prose.
