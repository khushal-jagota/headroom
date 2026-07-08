# Spike 11 - Rollover adaptation

Settled direction: v2 rollover is an agent flow, not deterministic server code.
The replacement skill should be `panels-rollover`: a role skill loaded by the
runtime when a planning day needs to cross the boundary. It should adapt the v1
rollover substance to v2's database, API, and CLI surfaces, not port v1's
markdown filesystem mechanics.

## 1. What Carries Over

The core v1 shape carries over almost directly:

1. **Preflight the boundary.** Resolve the active planning date using the
   planning-day rule, inspect whether the target day already exists or has been
   shaped, and avoid rerunning rollover just because the user comes back later.
2. **Make the old day true.** Read the previous day, current tickets, current
   sprint items, and the current sprint. Turn stale claims into honest state.
   In v2 this usually means observing already-current app state, not rewriting
   tickets opportunistically.
3. **Reconcile daily work into sprint reality.** The v1 discipline is important:
   daily work that changes durable sprint truth must be reflected at sprint-item
   or sprint level, while daily noise should not be promoted. Carry real
   unfinished work forward; never carry `done`.
4. **Pause for the boundary mini-review.** The agent should surface 1-3
   non-obvious reconciliation calls, then give its read on how the day went in
   sprint context. The user responds, they discuss briefly, and the exchange ends
   with next-day direction.
5. **Shape the new day.** After the direction exists, place the right tickets on
   the new day, create missing tickets only when they are real work, and write the
   overview in the new day's DB-backed fields.

The behavioral rules worth preserving:

- Carry forward real unfinished `in_progress`, still-relevant `todo`, and
  explicit carryover.
- Never carry `done` work forward.
- Keep ordinary current-sprint backlog out of today's day unless the boundary
  exchange chooses it.
- Promote conservatively. Recent detail is not importance.
- Reconcile into sprint reality without turning rollover into a full sprint
  review.
- Treat agent implementation reports as evidence, not automatic completion,
  unless the user or app state has actually closed the work.

## 2. What Drops

The v1 skill is large because v1's planner is a markdown filesystem with years of
cron/failsafe repair history. v2 should not inherit that surface.

Drop these parts:

- Daily folder writes to `tracker.md`, `overview.md`, and `workspace.md`.
- Direct edits to `sprint-tracking.md`.
- Markdown section parsing, bullet surgery, sibling-file repair, and day-folder
  validity checks.
- The cron/failsafe reference library as canonical behavior. It is a useful
  history of edge cases, but v2 should port the core flow first.
- Repo-scanning rules that only existed to correct stale markdown claims. v2
  tickets and sprint items are the source of truth; out-of-band work feedback is
  a future problem, not a reason to rebuild deterministic filesystem scans now.
- Deterministic carryover/overdue/approval digests. The agent reads the live
  DB/API/CLI state and reasons; server code should not synthesize a rollover
  judgment.

## 3. V2 Mechanism

`panels-rollover` runs as one employee-style agent with the rollover skill
loaded. It operates through Panels surfaces:

- `panels day show [today|YYYY-MM-DD]`
- `panels day add-ticket <ticket_id> [--date ...]`
- `panels day remove-ticket <ticket_id> [--date ...]`
- `panels ticket list --day ...`, `panels ticket show ...`, `panels ticket set --day ...`
- `panels ticket create ...` when the boundary reveals real missing work
- `panels item show/list/set/propose-status ...`
- `panels sprint show`

The DB remains the canonical store. The CLI/API are how the agent reads and
changes days, tickets, sprint items, and sprint state. It creates and shapes a
day by materializing it through normal day reads/writes and by adding/removing
day-ticket joins. The day's overview fields stay empty until the rollover agent
has a next-day direction and writes/proposes them.

The server should not run a deterministic boundary tick. Its job is:

- store days, tickets, sprint items, sprint fields, events, chat session keys,
  and approval/gate state;
- expose stable CLI/API verbs for the rollover agent;
- provide the human checkpoint/gate in global chat;
- wake or host the rollover agent when the product decides rollover should run.

## 4. Staged Skill Shape

`panels-rollover` should be written as an operating skill, not as a code spec.
The likely top-level procedure:

1. **Preflight.** Determine current planning date, previous planning date, current
   sprint, existing current-day tickets, and previous-day tickets.
2. **Stage 1 - old-day truth.** Read previous-day tickets, ticket states, sprint
   item linkage, review/approval queues, and sprint state. Do not mutate tickets
   merely because rollover sees them. Identify actual reconciliation calls.
3. **Stage 1.5 - sprint reconciliation.** For now, propose or perform only the
   v2-supported smallest durable sprint/item updates. If the required change
   needs an unsettled sprint-item change model, surface it as a reconciliation
   call instead of inventing a write path.
4. **Stage 2 - boundary mini-review.** In global chat, present 1-3 non-obvious
   calls, then the agent's day-in-sprint read. Pause for the user. End the
   exchange with next-day direction.
5. **Stage 3 - day composition.** After direction, add the selected carryover and
   new-day tickets to the target day, create only real missing tickets, remove
   unchosen work from the day if needed, and write/propose the overview fields.

## 5. Triggering

The agent can run in two product modes:

- **User-in-the-loop.** A user starts rollover or opens planning when the current
  planning day is not shaped. The rollover agent runs Stage 1, pauses at the
  mini-review, then continues after the next-day direction.
- **Autonomous kickoff.** A scheduled or runtime trigger starts the same
  `panels-rollover` agent. It must still use the same skill and the same
  checkpoint. If no user is present, it can prepare the reconciliation read and
  ask for direction; it should not invent a deterministic plan in server code.

The trigger mechanism is intentionally outside deterministic day code. It belongs
to the agent runtime/orchestration layer.

## 6. Unclear / Needs Design

- **Day overview write path.** Current day overview PATCH is human-only. The
  rollover agent needs an approved way to write or propose `focus`,
  `brief_take`, `watchout`, and `if_today_lands` after the boundary checkpoint.
- **Day composition approval.** The notes leave this open: approving the proposed
  day may approve its ticket set, or creates/carryovers may need finer gates.
- **Sprint-item reconciliation writes.** The sprint-item change model is still
  settling. Until that exists, rollover should identify required sprint
  reconciliation rather than silently mutating unsupported fields.
- **Exact runtime trigger.** The skill can define behavior, but the owner of
  "when to launch `panels-rollover`" is the runtime scheduler/global-chat
  orchestration layer, not `src/planner/days/`.
- **Out-of-band work.** v2 currently assumes tickets stay current because work
  flows through the app. Future external work ingestion may change Stage 1, but
  it should be added as agent-readable evidence, not deterministic markdown
  parsing.

## 7. Concrete Adaptation

The v1 rollover skill becomes smaller and sharper in v2:

- Replace "read and patch markdown files" with "read and mutate DB entities
  through Panels CLI/API."
- Replace deterministic carryover digests with agent reasoning over day-ticket,
  ticket, sprint-item, and sprint state.
- Replace `sprint-tracking.md` reconciliation with sprint-item/sprint changes or
  proposals.
- Replace "write three day files" with "compose the day-ticket set and overview
  fields."
- Keep the staged human checkpoint exactly: reconciliation calls first, agent
  day read second, user view, then next-day direction.
- Keep the core constraint: rollover is accountable for truth and continuity,
  not automatic completion or automatic priority inflation.
