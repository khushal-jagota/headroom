---
name: panels-ticket-creation
description: The shared Panels model for creating a coherent Ticket.
---

# Creating a Ticket

Use a Ticket for a concrete unit of work that one Worker can carry through its
lifecycle. A Sprint Item is the broader outcome that can own several Tickets; an idea
is a loose thought that is not committed work. Choose the Worker type whose lifecycle
fits the job, then let that type supply its normal employee runtime defaults.

The common routes are compact: `coding` changes a product or repository; `exploration`
makes an undefined question clear; `initiative_planning` settles shared decisions and
boundaries before several downstream Tickets; `product_design` works out a holistic
flow and implementation-ready design; and `new_worker` designs a new kind of Worker.
The `planning-day`, `planning-midday-check`, and `planning-sprint` types are specialized
planning conversations used by their scheduled or boundary routes, not substitutes for
ordinary work. Use the registered type's specialist when a newer type exists.

Give the Ticket a clear outcome-oriented title and a light, faithful Kickoff. Intake is
the user's request plus only the factual context the Worker needs to understand it.
Ground references to existing work, preserve agreed boundaries and dependencies, and
do not turn creation into premature planning.

## Two kinds of placement

Today and sprint placement answer different questions. Ordinary creation atomically
adds the Ticket to today, making it part of the active execution roster. This is true
whether the Ticket is in a sprint or the backlog. If the user explicitly wants it off
today, remove it from the Day as a separate follow-up; backlog does not mean not today.

Sprint placement says which outcome owns the work. Inspect the relevant Project and
Sprint Items and use an existing specific item when it genuinely owns the Ticket. With
no explicit placement and a current sprint, Panels creates or reuses that sprint's
Project-specific `Other` item; without a current sprint, the Ticket remains unparented.
An explicit Sprint Item selects that item; explicit backlog placement leaves the Ticket
outside a sprint. A Ticket under an item inherits its Project and effective sprint from
that item, while an unparented backlog Ticket may carry its Project directly. Do not
create a new Sprint Item merely to avoid the `Other` fallback; Sprint Item creation and
priority belong to sprint planning.

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

After creation, read the Ticket back as a whole. Its title, Worker type, Kickoff,
today status, Sprint Item or backlog placement, inherited Project and sprint, priority,
deadline, and blockers should tell one coherent story. Correct a mismatch through the
ordinary owning surface rather than compensating for it in prose.
