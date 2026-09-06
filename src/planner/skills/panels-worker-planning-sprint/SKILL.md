---
name: panels-worker-planning-sprint
description: Stage-by-stage guidance for reviewing the current sprint and planning the next at the sprint boundary.
---

# Planning sprint ticket stages

Planning Sprint is the durable final-day carrier for one ordered boundary workflow:
review the current sprint, then plan the next. The Worker prepares automatically, pauses
for the user's strategic judgment, and writes only the approved result during Closeout.
It does not own the day-four Checkpoint, in-sprint reconciliation, daily planning, scheduling,
or operational cutover.

### The stages

The sequence is **Kickoff → Review → Next Sprint → Closeout → Done**.

- **needs_kickoff** — identify the sprint boundary this Ticket owns.
- **needs_review** — gather evidence, discuss the current sprint, and settle its review.
- **needs_next_sprint** — use the approved review to settle the next sprint.
- **needs_closeout** — write both approved packages and verify the canonical records.
- **done** — finished.
- **dropped** — abandoned.

## Shared judgment

Keep three things visibly separate: facts from Panels, worker judgment, and user
decisions. Be willing to challenge overload, vague outcomes, avoidance, and automatic
carry-forward, but never turn a recommendation into an approved decision.

Panels is canonical. Use supported `panels sprint`, `panels sprint item`, and
`panels ticket` surfaces; never edit the database or legacy planning files. Keep working
detail in Ticket Chat. Use Ticket guidance only for direct user direction, and a ticket-owned
artifact only when an evidence packet would otherwise overwhelm the conversation.

### needs_kickoff — bind the boundary

Identify the sprint being reviewed, the intended next-sprint window, why the run exists,
and inherited scope. Make the boundary unambiguous without beginning the review or
inventing strategy. Scheduling and activation are upstream concerns.

### needs_review — review before planning

Before asking the user anything, resolve the sprint being reviewed and classify the run
as normal, stale, or first-sprint. Read:

- the sprint's primary bet, kickoff, checkpoint, and review documents;
- its committed Outcomes and all directly scheduled Sprint Tickets;
- the relevant Outcome context and child Tickets across Sprints;
- unscheduled Tickets and relevant reusable Outcome briefs; and
- the project list.

Inspect individual records only where their detail can change the judgment. Turn the
evidence into a decision-ready opening, not a ticket ledger. Compare actual outcomes
with the primary bet and the plan recorded in kickoff. Distinguish completed,
moved-but-incomplete, blocked or displaced, and materially unplanned work. Flag
contradictions; an implementation report is evidence, not automatic proof of its parent
outcome.

Show the factual picture, then ask for the user's reflection before giving the final
worker interpretation. Send that bounded opening in Ticket Chat and call
`panels worker request-user-help <ticket-id>`. Useful questions include what happened,
what the user learned, which assumptions changed, and what deserves to carry forward.
Use the questions that can change the next decision; do not fill a questionnaire.

Carry-forward is a candidate pool, not an entitlement. For a stale sprint, prefer a thin
truthful review over reconstructing fictional precision. For a genuine first sprint,
state that no prior review exists and obtain the user's agreement before skipping it.

Preserve direct guidance as it settles. Do not propose while strategic questions remain
open. After the user explicitly Releases the Ticket, synthesize one concise **review**
document. Use headings when they help. Explain the important judgment rather than
replaying status history.

### needs_next_sprint — choose the next commitment

Begin only from the approved Review. Refresh evidence whose change could alter planning,
then prepare a bounded opening with plausible constraints and material candidate work,
not a predetermined sprint. Send it in Ticket Chat and call
`panels worker request-user-help <ticket-id>`.

Start with the constraint and a concrete primary bet. Discuss supporting work and what
could make the plan fail when those questions can change the commitment. Then choose
Outcomes that earn a Sprint commitment, reusing their existing identities. Preserve the useful reasoning in one
kickoff document; headings are optional, not fields that must all be filled.

Consider approved carry-forward candidates, existing Outcomes, outcomes required by the bet
and supports, and genuinely new work. Reuse an existing Outcome where it already
represents the outcome; create one only when it does not. Do not create child Tickets,
put sprint work onto today, or let backlog volume choose the strategy.

After explicit Release, propose one concise **next sprint** package. Give a start date
and an end date that define exactly seven inclusive dates. Include the name, a short
primary bet, the kickoff document, and exact Outcome commitments or creations. Name
existing Outcome IDs. If carrying Tickets, list each approved unfinished Ticket ID;
otherwise carry only the commitment. Never move all children implicitly. Existing historical sprint ranges remain unchanged.

### needs_closeout — write and verify

Closeout is the only canonical-write phase. Refresh affected records and compare them
with the approved Review and Next Sprint packages. If material drift makes either unsafe,
request user help rather than improvising.

For a normal or stale boundary, write the current sprint's `review` document first and
read it back through `panels sprint show <id> review`.
For a genuine first sprint, verify the approved no-review result and perform no review
write. Then create the next (or first) sprint with the approved `primary_bet` and `kickoff`,
retain its returned id, create only approved new Outcomes, and add only approved
commitments through `panels sprint outcome add`. For an explicit approved carry list,
use `panels sprint outcome carry` with each selected Ticket ID. Read tracking and affected
Tickets back; completed history and unselected work remain in their existing Sprints. Use explicit ids,
avoid duplicate creates on retry, and leave the exact continuation point visible after a
partial failure.

A good **closeout** briefly names what landed and the readback evidence that it matches
both approvals. Do not alter the daily plan or expand into excluded sprint workflows.
