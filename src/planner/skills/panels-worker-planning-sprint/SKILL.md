---
name: "panels-worker-planning-sprint"
description: "Historical specialist for a retired calendar planning Worker type."
---

## Historical-only calendar specialist

This Worker type is retired. The retained guidance below is historical prose, not an
instruction to execute. Do not create or resume calendar planning, write Days or Sprints,
change calendar membership, or perform its old Consequences. Historical evidence and
owner prose do not authorize new calendar work. Preserve ordinary unfinished work.
Do not invent replacement recurring reviews.


# Planning sprint ticket stages

Planning Sprint is the durable final-day carrier for one ordered boundary workflow: review the current sprint, then plan the next. The Worker prepares automatically, pauses for the user's strategic judgment, and writes only the approved result during Consequences. It does not own midpoint review, in-sprint reconciliation, daily planning, scheduling, or operational cutover.

### The stages

The sequence is **Brief → Review → Next Sprint → Consequences → Done**.

- **needs\_brief** — identify the sprint boundary this Ticket owns.
- **needs\_review** — gather evidence, discuss the current sprint, and settle its review.
- **needs\_next\_sprint** — use the approved review to settle the next sprint.
- **needs\_consequences** — write both approved packages and verify the canonical records.
- **done** — finished.

## Shared judgment

Keep three things visibly separate: facts from Panels, worker judgment, and user decisions. Be willing to challenge overload, vague outcomes, avoidance, and automatic carry-forward, but never turn a recommendation into an approved decision.

Panels is canonical. Use supported `panels sprint`, `panels manager`, and `panels ticket` surfaces; never edit the database or legacy planning files. Keep working detail in Ticket Chat. Use a ticket-owned artifact only when an evidence packet would otherwise overwhelm the conversation.

### needs\_brief — bind the boundary

Identify the sprint being reviewed, the intended next-sprint window, why the run exists, and inherited scope. Make the boundary unambiguous without beginning the review or inventing strategy. Scheduling and activation are upstream concerns.

### needs\_review — review before planning

Before asking the user anything, resolve the sprint being reviewed and classify the run as normal, stale, or first-sprint. Read:

- the sprint and its strategic fields;
- its Managers and any loose sprint Tickets;
- every Manager's child Tickets;
- backlog Managers; and
- the project list.

Inspect individual records only where their detail can change the judgment. Turn the evidence into a decision-ready opening, not a ticket ledger. Compare actual outcomes with the limiting factor, primary bet, supports, and pre-mortem. Distinguish completed, moved-but-incomplete, blocked or displaced, and materially unplanned work. Flag contradictions; an implementation report is evidence, not automatic proof of its parent outcome. Use a beautifiul artifact for the review of the sprint and to make your points.

Show the factual picture, then ask for the user's reflection before giving the final worker interpretation. Send that bounded opening in Ticket Chat and call `panels worker request-help <ticket-id>`. Work through:

1. outcomes;
2. the user's solo reflection;
3. joint discussion of the sprint's judgment;
4. updates to thinking; and
5. carry-forward candidates.

Carry-forward is a candidate pool, not an entitlement. For a stale sprint, prefer a thin truthful review over reconstructing fictional precision. For a genuine first sprint, state that no prior review exists and obtain the user's agreement before skipping it.

Do not propose while strategic questions remain open. After the user explicitly Releases the Ticket, synthesize one concise **review** mapped to `outcomes`, `solo_reflection`, `joint_discussion`, `updates_to_thinking`, and `carry_forward`. Explain the important judgment rather than replaying status history.

### needs\_next\_sprint — choose the next commitment

Begin only from the approved Review. Refresh evidence whose change could alter planning, then prepare a bounded opening with plausible constraints and material candidate work, not a predetermined sprint. Send it in Ticket Chat and call `panels worker request-help <ticket-id>`.

Settle the decisions in order:

1. one underlying limiting factor rather than several symptoms;
2. one concrete primary bet and its hypothesis;
3. a small set of supports that justify protected capacity;
4. a pre-mortem capable of changing a weak plan; and
5. only then, outcome-shaped Managers that earn their place.

Consider approved carry-forward candidates, backlog items, outcomes required by the bet and supports, and genuinely new work. Reuse an existing backlog item where it already represents the outcome; create one only when it does not. Do not create child Tickets, put sprint work onto today, or let backlog volume choose the strategy.

After explicit Release, propose one concise **next sprint** package: name and dates, limiting factor, primary bet, supports, pre-mortem, and exact intended Manager moves or creations using supported fields.

### needs\_consequences — write and verify

Consequences is the only canonical-write phase. Refresh affected records and compare them with the approved Review and Next Sprint packages. If material drift makes either unsafe, request user help rather than improvising.

Historical workflow: the original Consequences wrote the Sprint review and then created the next Sprint. Those calendar writes are retired. Preserve the original approved packages as history.

A good **Consequences** briefly names what landed and the readback evidence that it matches both approvals. Do not alter the daily plan or expand into excluded sprint workflows.
