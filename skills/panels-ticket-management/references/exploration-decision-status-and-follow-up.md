# Exploration decision status and follow-up capture

Use this reference when a design/exploration conversation is becoming a Panels proposal or downstream Ticket package.

## 1. Preserve decision status

A useful ladder:

- **Possible:** “might,” “maybe,” “it depends,” “one option is.” Discuss only; do not canonicalize.
- **Leaning:** “I think this makes the most sense.” Reflect the current preference and unresolved tradeoff; confirm before treating it as durable direction when the distinction matters.
- **Agreed:** “yes,” “agreed,” “that is what I want,” or an explicit approval action. Safe to preserve in notes/proposals.

A user may firmly reject one mechanism while remaining undecided about ownership. Preserve each part separately. For example, “do not create another conflict branch” does not by itself choose whether the original Worker, a dedicated Worker, or GitHub owns integration.

If an option was prematurely recorded as decided:

1. acknowledge the exact overreach;
2. state what was actually said;
3. correct the canonical note/proposal/artifact;
4. resume discussion without defending the old record.

## 2. Find the capability-level 80/20

A long preflight list is often implementation detail, not a Ticket list. Group lines by user-visible or operational capability first.

Example pattern:

- six queue mechanics may be one **serialized Closeout lane** capability;
- staging isolation is one capability;
- production promotion/deployment is one capability;
- state safety is one capability;
- VPS resource hygiene is one capability.

Then choose the smallest immediate follow-up package:

- send a settled, standalone capability directly to its implementation Worker;
- send the remaining confirmed multi-Ticket direction to `initiative_planning` for cross-Ticket decisions and downstream Ticket creation.

Be explicit: “two immediate Tickets cover five capabilities” does not mean “two Tickets implement all five.”

## 3. Preserve Stage plus control semantics

Never infer runnability from Stage alone. Capture the exact control tuple the user approved.

Example:

- `Closeout + Continue` is eligible for a runnable queue;
- `Closeout + Stop` stays parked and does not claim a lane.

Likewise, distinguish lane ownership from review behavior. If approval intentionally holds a lane, record when it releases, how approval-through-Done auto-accepts, and what a rejection does.

## 4. Apply approved exploration follow-up safely

Before creation:

1. read the accepted Follow-up field;
2. search for existing aligned Tickets;
3. confirm the destination container and approved day placement;
4. use the exact registered Worker type.

Create one record, capture its ID, then continue. Read every record back and verify title, Worker type, priority, sprint item/effective sprint, day membership, Kickoff proposal, and observed status. The exploration Closeout reports only what was created and verified; it does not claim the downstream implementation happened.
