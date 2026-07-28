---
name: panels-sprint-planning
description: Route final-day sprint review and next-sprint planning into the durable planning-sprint Worker.
---

# Panels sprint planning

Use a `planning-sprint` Ticket for the final-day sprint boundary: review the current
sprint first, then plan the next sprint from what was learned. The Ticket is the durable
carrier for the conversation, approvals, canonical writes, and verification.

Start with the `panels` and `panels-chief-of-staff` skills. If a relevant
`planning-sprint` Ticket already exists, resume it. Otherwise create one:

```sh
panels ticket create --worker-type planning-sprint --title "Review current sprint and plan the next"
```

Then follow `panels-worker` and `panels-worker-planning-sprint` stage by stage. Do not
perform the review or planning as a standalone ceremony outside the Ticket.

This route does not own midpoint review, in-sprint reconciliation, daily planning,
scheduling, or operational cutover.
