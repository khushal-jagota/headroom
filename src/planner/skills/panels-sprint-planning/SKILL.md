---
name: panels-sprint-planning
description: Plan, review, and reconcile two-week sprints through the current Panels workspace, with the user making the strategic decisions.
---

# Panels sprint planning

## Purpose

Use this skill for sprint-level planning and reconciliation in Panels. A sprint is a two-week focus container: it records the strategic bet for the period and the outcome-shaped work that earns space inside it.

The main sprint-boundary workflow always has two ordered phases:

1. **Review the current sprint.** Establish what happened and what it taught us.
2. **Plan the next sprint.** Use that review to decide the next limiting factor, bet, supports, risks, and sprint items.

Do not start next-sprint itemization before the current sprint review is settled. Review and planning may happen in one conversation, but they remain distinct decisions.

## When to use

Use this skill when the user wants to:

- review a sprint and plan the next one;
- reconcile the current sprint against actual Ticket Stages and item status;
- run the midpoint review;
- decide whether work belongs in the active sprint or backlog;
- reset a stale sprint and re-establish a truthful two-week commitment.

Do not use it for daily rollover or ordinary day composition. Sprint planning may read daily or ticket evidence, but it does not automatically place sprint work on today.

## Authority and source of truth

Panels is canonical. Read and change sprint state only through supported `panels` CLI/API surfaces. Do not edit the old planning Markdown files or the Panels database directly.

Use structured output when available:

```sh
panels sprint list --json
panels sprint show current --json
panels sprint item list --sprint <sprint-id> --json
panels sprint item list --sprint none --json
panels ticket list --sprint-item <item-id> --json
panels project list --json
```

Resolve the sprint being reviewed before reading its work. Use the active sprint when one exists; at a boundary with no active sprint, use `panels sprint list --json` to identify the most recently ended sprint. Then use that explicit sprint id for item and ticket reads and for every write. Inspect individual sprint items or tickets only when their detail changes a decision:

```sh
panels sprint item show <item-id> --json
panels ticket show <ticket-id> --json
```

Every scheduled Ticket belongs to a Sprint Item. For every item being reviewed,
including each item whose `kind` is `other`, use `--sprint-item <item-id>` so its child
Ticket evidence is included. Treat Other as visible catch-all work to inspect, not as a
group to skip because it was created automatically.

Separate three things in the conversation:

- **Facts:** what the current sprint, items, and tickets say.
- **Judgment:** what the evidence suggests about the sprint and next bet.
- **User decisions:** what should be recorded or committed.

Do not invent a strategic decision or treat an agent suggestion as approval.

After every accepted write, read the affected sprint or sprint item back before continuing. A successful write response is not enough verification.

# Sprint-boundary workflow

## Phase 1 — review the current sprint

Finish this phase before creating or populating the next sprint.

### 1. Preflight the review

List the sprints and resolve the sprint being reviewed. Read that sprint by id, its sprint items, relevant child tickets, backlog items, and project list. Determine whether this is:

- a normal end-of-sprint review;
- a late or stale review where the old sprint stopped being useful; or
- the first sprint, with no prior sprint to review.

For a stale sprint, do a thin truthful review rather than reconstructing a fictional clean plan from scattered history. If there is genuinely no prior sprint, state that and proceed only after the user agrees there is nothing to review.

**Complete when:** the existing sprint and any gaps or contradictions are clear enough to discuss without guessing.

### 2. Establish outcomes first

Compare actual sprint-item status and Ticket Stages with the sprint’s limiting factor, primary bet, supports, and pre-mortem. Summarize:

- what completed;
- what moved but remains incomplete;
- what was blocked, dropped, or displaced;
- what happened outside the original plan that materially changed the sprint.

Keep this factual. A ticket implementation report is evidence, not automatic proof that its parent outcome is complete. Sprint-item status is derived from non-dropped child tickets and open blocking links; do not invent or manually force a separate status.

After the user settles the outcomes, record them and read the sprint back:

```sh
panels sprint set <sprint-id> outcomes --body-file - --json
panels sprint show <sprint-id> --json
```

**Complete when:** the recorded outcomes are truthful and the readback matches the decision.

### 3. Capture the user’s reflection

Ask for the user’s own read before giving the final agent interpretation. Preserve their judgment about focus, avoidance, scope, attention, and decision quality without expanding it into a generic retrospective.

After the user settles the wording:

```sh
panels sprint set <sprint-id> solo-reflection --body-file - --json
panels sprint show <sprint-id> --json
```

**Complete when:** the user’s reflection is recorded in their meaning and verified by readback.

### 4. Run the joint discussion

Discuss what the outcomes and reflection imply:

- Was the limiting factor correctly chosen?
- Was the primary bet sound?
- Were the supports the right protected outcomes?
- Was the sprint challenging but credible?
- Which failures were bad judgment, execution drift, or bad luck?

Be willing to challenge overload, vague outcomes, automatic carry-forward, and work that displaced the main bet. Keep the discussion at sprint level rather than turning it into ticket-by-ticket administration.

Record the settled discussion:

```sh
panels sprint set <sprint-id> joint-discussion --body-file - --json
panels sprint show <sprint-id> --json
```

**Complete when:** the review explains the important judgment, not merely the status list.

### 5. Settle learning and carry-forward candidates

Record durable changes to the user’s thinking separately from possible carry-forward:

```sh
panels sprint set <sprint-id> updates-to-thinking --body-file - --json
panels sprint set <sprint-id> carry-forward --body-file - --json
panels sprint show <sprint-id> --json
```

Carry-forward is a candidate pool, not the next sprint plan. Unfinished work does not earn space merely because it is unfinished.

**Phase 1 is complete when:** outcomes, solo reflection, joint discussion, updates to thinking, and carry-forward candidates are settled and verified.

## Phase 2 — decide and build the next sprint

Use the completed review as context. Do not let the backlog choose the strategy by volume or recency.

### 1. Decide the limiting factor

Identify the constraint that most needs to move during the next two-week window. Challenge vague or multi-headed formulations. Distinguish the underlying constraint from its symptoms.

**Complete when:** the user has chosen one clear limiting factor.

### 2. Decide the primary bet

Choose the central outcome and hypothesis for reducing the limiting factor. Test whether it is concrete enough to review and important enough to justify the two-week commitment.

**Complete when:** the user has settled one concise bet that directly addresses the limiting factor.

### 3. Choose supports

Choose a small set of outcome-shaped commitments that either advance the primary bet or are important enough to protect independently. Discuss the user attention each support deserves, but do not invent a field that Panels does not store.

**Complete when:** every support has a clear reason to occupy sprint capacity.

### 4. Run the pre-mortem

Assume the sprint failed. Identify the likely execution and strategic causes, then adjust the limiting factor, bet, or supports if the risks expose a weak plan.

**Complete when:** the important failure modes are explicit and the strategy still holds after the challenge.

### 5. Create the next sprint

Only after the review and kickoff decisions are settled, create the sprint through Panels:

```sh
panels sprint create \
  --name "<sprint name>" \
  --date-start YYYY-MM-DD \
  --date-end YYYY-MM-DD \
  --limiting-factor "<limiting factor>" \
  --primary-bet "<primary bet>" \
  --supports "<supports>" \
  --premortem "<pre-mortem>" \
  --json
```

Use `--body-file -` with `panels sprint set` for longer field text if later corrections are needed. Read the new sprint back by its returned id.

**Complete when:** the new sprint’s dates and kickoff fields match the settled decisions.

### 6. Select sprint items

Consider candidate items from:

- work required by the primary bet and supports;
- explicit carry-forward candidates from the review;
- backlog sprint items (`--sprint none`);
- current work that materially changed the next sprint’s constraints;
- new outcome-shaped work discovered during planning.

Each normal item must earn its place. Prefer outcome-shaped normal items over task lists.
An item whose `kind` is `other` is the machine-recognized fallback for its sprint and
Project: keep it visible and review its children, but do not pretend it is a shaped
outcome or create another Other manually. Use only current Panels fields: title, body,
priority, deadline, project, and sprint placement. Do not recreate legacy fields that
the current contract does not support.

Move an existing backlog item into the new sprint:

```sh
panels sprint item set <item-id> sprint --value <new-sprint-id> --json
```

Create a new item only when no existing item represents the outcome:

```sh
panels sprint item create \
  --title "<outcome>" \
  --project-id <project-id> \
  --priority P0|P1|P2|P3 \
  --sprint <new-sprint-id> \
  --body-file - \
  --json
```

Do not create child tickets merely to make the sprint look executable. Ticket breakdown is separate work and should happen only when the user asks or an item is ready to be decomposed.

Read the completed sprint and item list back:

```sh
panels sprint show <new-sprint-id> --json
panels sprint item list --sprint <new-sprint-id> --json
```

**Phase 2 is complete when:** the new sprint has a coherent kickoff, every normal item
has earned its place, fallback children are explicitly surfaced, and the readback
matches the approved plan.

# In-sprint reconciliation

Use this path when the user wants the current sprint made truthful without starting a boundary review and next-sprint plan.

1. Read the current sprint, all of its items, and each item's child Tickets through `--sprint-item`. Explicitly call out children of `kind: other` items so fallback work is visible.
2. Identify only material sprint-level changes: outcome movement, real blockers, changed scope, priority/deadline/project corrections, or work that no longer belongs.
3. Present factual mismatches and recommended corrections before writing.
4. Apply only settled changes with `panels sprint set` or `panels sprint item set`.
5. Read the affected sprint and items back.

Do not use ordinary daily movement as an excuse to rewrite the sprint continuously.

## Midpoint review

At the sprint midpoint, settle and record:

- where the sprint stands;
- what changed;
- what to adjust.

Use the supported sprint fields:

```sh
panels sprint set <sprint-id> mid-where-we-stand --body-file - --json
panels sprint set <sprint-id> mid-whats-changed --body-file - --json
panels sprint set <sprint-id> mid-what-to-adjust --body-file - --json
panels sprint show <sprint-id> --json
```

A midpoint review may narrow or correct the sprint deliberately. It does not silently begin next-sprint planning.

# Daily rollover boundary

Sprint planning and daily rollover are separate workflows.

- Sprint planning decides the two-week strategic commitment.
- Rollover makes the old day truthful and shapes the next day.
- Sprint planning may read day or ticket evidence when it changes sprint truth.
- Sprint planning never adds sprint work to today automatically.
- Rollover does not replace the sprint review or decide the next sprint.

# Scheduling

Do not run sprint planning autonomously from a cron. The review and next-sprint decisions require the user. A future boundary reminder may invite the user to start this skill, but it must not create a second planning workflow or commit a sprint by itself.

# Common failures

- **Planning before reviewing:** backlog grooming starts while the current sprint is still uninterpreted. Finish Phase 1 first.
- **Automatic carry-forward:** unfinished work moves because it is unfinished. Make it earn space against the new limiting factor and bet.
- **Status administration instead of judgment:** the review becomes a ticket ledger. Keep the discussion focused on outcomes and decision quality.
- **Writing before decisions settle:** agent suggestions become canonical state. Present choices first and verify every accepted write.
- **Recreating the old filesystem:** legacy Markdown files or direct DB edits become a competing source of truth. Use Panels CLI/API only.
- **Pulling sprint work onto today:** sprint commitment is mistaken for day commitment. Leave day composition to rollover or daily planning.
- **Inventing unsupported fields:** legacy concepts are copied into a contract that no longer stores them. Use only current Panels fields.

# Verification checklist

Before declaring a sprint workflow complete, confirm:

- [ ] The current sprint review was completed before next-sprint planning.
- [ ] Outcomes, user reflection, joint discussion, learning, and carry-forward were read back correctly.
- [ ] The next limiting factor, primary bet, supports, and pre-mortem were user decisions.
- [ ] Every normal next-sprint item earned its place and uses supported Panels fields.
- [ ] Children of Other fallback items were explicitly surfaced rather than hidden in totals.
- [ ] The final sprint and item list match the approved decisions.
- [ ] No daily tickets were added merely because they belong to the sprint.
- [ ] No legacy planning file or direct database write was used.
- [ ] No autonomous sprint-planning cron was created.
