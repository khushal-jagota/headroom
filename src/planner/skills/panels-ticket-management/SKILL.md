---
name: panels-ticket-management
description: Creating, placing, and inspecting Panels tickets across sprint, day, and Workspace surfaces.
---

# Panels ticket management

Use this skill when creating Panels tickets, checking why a ticket is or is not visible, or deciding whether a ticket belongs in the sprint, today's day, or the Workspace execution board.

Session-specific capture guidance is in `references/ticket-capture-boundaries-2026-07-09.md`.

For turning an exploration discussion into canonical decisions, capability-level follow-up, and verified downstream Tickets, use `references/exploration-decision-status-and-follow-up.md`.

For ticket-file / preview-subsystem discussions, see `references/ticket-files-preview.md`. Before publishing an HTML planning artifact, verify it through the actual Panels embedded and full-preview paths—not only by opening the raw file—and use the reference’s script-sandbox compatibility check.

For reviewing several waiting Closeouts and recommending a safe release/integration sequence, use `references/closeout-ordering.md`.

## Core model

This skill is a mechanical reference for creating, placing, and inspecting Tickets. It does not own top-level planning judgment or the user-facing Chief-of-Staff voice; `panels-chief-of-staff` owns those. During ordinary Chief capture, consult this skill only when its placement, verification, or recovery detail is needed rather than treating it as a second general planning policy.

Panels tickets can exist in several scopes:

- A **sprint ticket** is attached to a sprint, often with `panels ticket create --sprint current`.
- A **loose sprint ticket** is attached to the sprint but not to a sprint item.
- A **day ticket** is attached to a day through `day_tickets`.
- The **Workspace** route is the execution board. It is day-scoped, not an all-ticket inventory.

## Status overviews and operational adoption

Keep user-facing overviews conversational and short: lead with the current position in 2–5 plain-language bullets, then let the user choose what to inspect. If they ask only what has been done, report only completed work rather than attaching the remaining roadmap.

When reading completed infrastructure or workflow Tickets, distinguish three different claims:

1. machinery exists in code and tests;
2. the real local workflow has adopted it; and
3. it is installed and running in the eventual hosting environment.

A completed implementation Ticket proves only the claims supported by its evidence. Commands, templates, and isolation tests do not by themselves prove that current live processes use the machinery, that persistent staging exists, or that Workers and role skills follow it. Check those operational facts before summarizing status.

If the missing work is cross-cutting adoption—runtime launch, persistent environments, Worker/Chief skills, repository guidance, Closeout behavior, and lifecycle tooling—use an `initiative_planning` Ticket to resolve the shared operating model and create the smallest implementation set. Do not defer local adoption merely because stronger VPS-only isolation will come later.

## Create a ticket

1. Create the ticket with the narrowest known scope. The current CLI requires an explicit Worker type; use the appropriate registered type rather than assuming a default.
   - Current sprint coding ticket: `panels ticket create --title "..." --worker-type coding --priority P3 --sprint current --json`
   - Specific project when known: add `--project-id <project_id>` or `--project "<name>"`.
   - Specific sprint item when known: add `--sprint-item <sprint_item_id>`.
   - Discover available Worker type ids from the live Panels type surface when the work is not a coding ticket; never omit `--worker-type` or invent an id.
2. Read the created ticket back with `panels ticket show <ticket_id> --json`.
   - If a shell wrapper fails after printing or saving a created id, do not retry creation blindly. Read the saved JSON or find the matching title, then continue from the existing ticket.
   - For multi-paragraph Kickoffs, prefer `--kickoff-note-file`; shell-escaped `\n` can become visible backslash text rather than real paragraphs.
   - `panels ticket set ... kickoff-note` edits a settled Kickoff, not a pending Kickoff proposal. Do not approve a malformed proposal merely to make it editable. If the agent has just created an unreviewed, otherwise untouched malformed Ticket, delete that accidental record and recreate it once from the corrected note file, then verify the old id is gone and only the corrected Ticket was placed. If the Ticket has acquired user decisions, work, links, or meaningful history, preserve it and use the supported proposal-correction flow instead of recreating it.
3. Keep Kickoff light enough to work as a human gate.
   - Preserve everything the user supplied, summarized or close to verbatim. Avoid title-only tickets except for tiny obvious changes.
   - Add only the small amount of context needed to prevent an obvious misunderstanding. Do not turn Kickoff into assistant-authored Success, Approach, Plan, architecture, test coverage, or defensive process instructions.
   - Longer Kickoffs are appropriate after a long discussion or when carrying substantial upstream Ticket context. Even then, prefer a concise summary plus links to durable sources over repeating them.
   - Trust the Worker type and specialist to do their declared jobs. If a clear lightweight Kickoff exposes weak Worker behavior, improve the Worker instead of hiding the failure behind instructions copied into every Ticket.
   - If the user gives step-specific advice (for example, “when shaping success/review, remember X”), put it in the corresponding field notes as user direction.
   - If the ticket emerges from a design conversation, use the ticket-level user note and field notes to carry the agreed context. Do not rewrite the conversation as a fully assistant-authored spec unless the user asks for that.
   - When the user asks what you would write as the ticket, answer in note-shaped pieces (ticket user note, success note, approach note, plan note), not a polished canonical ticket unless requested.
   - Keep intake/user-note context separate from `success`, `approach`, `plan`, `implementation`, and `closeout`; do not prematurely fill canonical gated fields unless asked.
   - For tickets about Panels itself — Workspace, Review, tickets, workers, Chief, chat, CLI/API, days/sprints, or planning-v2 — assign the Panels project when it exists (usually `project_panels`).
4. Keep **creation approval** separate from **placement approval**.
   - Brainstorming, correcting a rough plan, naming a possible track, or saying work matters is not permission to create a ticket.
   - Create only when the user explicitly requests the ticket or confirms the proposed ticket shape.
   - A request to create a ticket does not automatically approve today, tomorrow, sprint, sprint-item, blocker, priority, or gate changes. Apply only placement the user explicitly requested or approved; otherwise leave the ticket unplaced and say so.
   - During day/sprint planning, show the rough plan first. Corrections revise that draft; they are not approval. Wait for “yes,” “approved,” “apply it,” or an equally explicit instruction before mutating overview fields or membership.
   - Never create helper tickets merely to make an unapproved plan look executable.
5. If the user explicitly wants the ticket visible today **without starting the worker**, create it before adding it to today and inspect the live ticket-control commands before assuming a scope setter exists.
   - Prefer a supported operation that leaves the ticket at its current gate with `at_cap=stop` before day placement.
   - If the live CLI has no direct scope/control operation, do not invent one. An ordinary newly created ticket whose Kickoff is genuinely awaiting approval is not runnable; add it only when that pending gate provides the required safety, then immediately read it back and verify `stage`, `at_cap`, and `ticket_status`.
   - If neither a supported Stop operation nor a non-runnable pending gate is available, do not add the ticket to today without explicit permission to start it.
6. After any approved placement, read the ticket back and verify the requested `day_ids`, sprint, or sprint item. Do not report setup complete from the create response alone. Also report the resulting `ticket_status` — `empty` (at rest and startable), `blocked` (at rest behind a live blocker), `awaiting_approval`, or `agent` (already running).
7. Unless asked to drive or monitor the worker flow, stop after setup and report the ticket id, project/sprint/day placement, scope, and current state/status. The user may review elsewhere.

## Onboard the first tickets for a future Worker type

When a `new_worker` ticket is meant to prove the new type with its first real tickets:

**Preflight:** Reuse the existing work container before inventing another one. A sprint item already represents an outcome and groups its child Tickets, so an “initiative-level” planning worker does not by itself justify a new Initiative entity, duplicated plan, or mandatory shared artifact. When the planning Ticket is under the sprint item, keep it as discoverable decision history, encode the context each resulting Ticket needs, and add extra durable machinery only when a concrete retrieval or coordination problem requires it.

1. Treat the first-ticket set as part of the `new_worker` ticket's explicit Success/Closeout contract. Do not create those future tickets early under `coding` or another available type merely to reserve their names.
2. Preserve each first ticket's agreed premise and intended parent sprint item in the new-worker Kickoff or the relevant Closeout field note. A later user correction can add or replace required onboarding tickets, but it does not approve unrelated day/sprint changes.
3. After the Worker type is registered and live, create the required tickets with that exact type. Verify the live manifest/CLI accepts it before creation.
4. Apply day, sprint, and sprint-item placement only as explicitly approved. If one new-worker Closeout creates several required tickets, verify every created id and type rather than trusting prose that says they were onboarded.
5. If an incorrectly typed placeholder already exists, permanently delete it only on explicit user instruction, then remove its stale id from day notes or planning summaries. Keep the intended outcome in the new-worker Closeout note so deletion does not lose the requirement.

This keeps the type system truthful: the worker-creation ticket proves the shipped type through real instances instead of leaving permanent coding-shaped stand-ins.

## Visibility pitfall: Workspace is day-scoped

Creating a current-sprint ticket does **not** make it appear in Workspace. Workspace is backed by `/api/board`, which only returns tickets on today's day. A loose current-sprint ticket appears in the current sprint view under `loose_tickets`, not in Workspace.

To make a ticket visible/workable in Workspace today, add it deliberately:

```sh
panels day add-ticket <ticket_id> --date today --json
```

Do not do this casually. Adding a runnable ticket to today can wake System A and cause the worker loop to pick it up.

## Carry recent work into today

When the user asks to move recent or yesterday's work into today:

1. Resolve dates from Panels' planning calendar, not the host clock. Read today's Panels day first; its `day_YYYY-MM-DD` id is authoritative across timezone and 5am-boundary differences. The CLI may reject `--date yesterday`, so subtract one calendar day from the resolved Panels date and pass the explicit `YYYY-MM-DD`.
2. Inspect both days before acting:
   - `panels day list-tickets --date 2026-MM-DD --json`
   - `panels day list-tickets --date today --json`
   - `panels ticket show <ticket_id> --json` for likely candidates.
   If a day payload is too large for reliable parsing, reduce it before capture, for example with `jq '[.tickets[] | {id,title,stage,ticket_status}]'`; do not parse a tool-truncated JSON blob.
3. Distinguish selection requests from exact-set requests:
   - If the user asks which work should carry, prefer tickets that are recently shaped, active, or not waiting on approval; do not blindly move every previous-day ticket.
   - If the user names the day-owned set directly (for example, **move yesterday's tickets**), treat the prior day's full membership as the requested set rather than editorially selecting a subset.
   - If the user explicitly says **all tickets that are not complete**, treat that as an exact filter rather than reprioritizing: carry every ticket whose canonical state is neither `done` nor `dropped`.
4. Add selected tickets with `panels day add-ticket <ticket_id> --date today --json`.
5. Preserve prior day links as history unless the user explicitly asks to remove them.
6. If today's notes still describe carryover as “pending review,” remove or replace that stale note after the user approves and the move succeeds. Preserve unrelated day notes.
7. Verify today/Workspace visibility and tell the user if adding the ticket woke execution (`ticket_status: agent`). For an exact-set request, compare expected and actual ticket-id sets and report missing or unexpected ids rather than relying on counts alone.

## Bring current-sprint work into today

When the user asks what else from the sprint belongs in today, do not rank tickets from title, priority, or sprint status alone.

1. Read today’s open tickets and recursively collect every non-terminal current-sprint ticket not already on today.
2. Read the full records for the strongest candidates. Check accepted fields, current gate, scope, status, prior day history, parent sprint item, and overlap with work already represented today.
3. Prefer:
   - the ticket attached to the sprint’s primary bet when it has a concrete next decision;
   - a bounded closeout/publication ticket for work already built;
   - a recently shaped ticket with no overlapping active ticket.
4. Avoid carrying both an old implementation ticket and its newer publication/closeout ticket, or both a broad gate ticket and a more concrete integration ticket already on today. Stale P3 cleanup should not displace the sprint bet merely because it is easy to start.
5. Present the smallest recommended set before adding it. Add only the tickets the user chooses, then verify today’s `day_ids` and resulting control status.

If a candidate has a settled value in its current gating field but its `state` still points at that field, repair the stale position before waking a worker: inspect events/history, move only to the next genuinely blank gate through the supported direct state operation, set scope for that gate, and preserve the user’s current-state investigation request in the ticket-level note. Do not wake a worker into a gate whose canonical field is already settled.

See `references/sprint-to-day-triage.md` for the compact selection and stale-position recovery pattern.

## Turn a prior diagnostic into implementation tickets

When the user names a prior exploration/diagnostic and asks to ticket specific findings:

1. Inspect the original Panels ticket and its managed artifact before searching conversation history. Treat the source's current wording and ranking as canonical.
2. Search live tickets for existing aligned follow-ups before creating anything.
3. Create only the findings the user selected. Preserve exact distinctions from the source—for example, recovery after crashes is not the same as preventing crashes—and link the source ticket in each Kickoff.
4. Put concrete work in its requested project, sprint, and day, then read each ticket back. Ordinary creation may leave Kickoff awaiting approval; report that rather than bypassing the gate.
5. If the user supplies a broader outcome after the tickets exist, create or reuse one outcome-shaped sprint item and attach the related tickets with `panels sprint item add-ticket <item-id> <ticket-id> --json`. Verify attachment through `panels ticket list --sprint-item <item-id> --json`; the sprint-item detail response may not embed child tickets.

Do not silently expand selected findings into every recommendation from the diagnostic, VPS provisioning, or other downstream work.

When an approved exploration contains a long “required before” checklist, find the **capability-level 80/20** before proposing follow-up. Several low-level checklist lines may be one implementation capability rather than separate Tickets. Map every agreed capability to an immediate consequence, but distinguish **coverage** from **direct implementation**: a focused, settled capability may go straight to one implementation Ticket, while a confirmed direction spanning several interdependent Tickets belongs in an `initiative_planning` Ticket that resolves cross-Ticket questions and creates the smallest downstream set. Say explicitly when two immediate follow-up Tickets cover five outcomes but do not themselves perform all five implementations.

When the user explicitly asks for a fresh system-level exploration of behavior that Panels already implements, search for the prior exploration and implementation first and link them in the new Kickoff. Preserve the requested sequence: orient the user to what exists, establish what they actually want through paired Understanding, then judge the best mechanism independently of the current implementation. Describe prior work as evidence rather than an inherited decision unless the user explicitly keeps that decision. Do not collapse this into a regression-fix coding Ticket merely because code already exists.

Preserve exact lifecycle admission conditions in Ticket context. A Stage name alone may not make work runnable: record the relevant scope/control state too—for example, `Closeout + Continue` may enter a queue while `Closeout + Stop` remains parked. Do not shorten such a rule to “Tickets at Closeout are queued,” because that changes the user's control semantics.

During exploration Closeout, create exactly the approved records and no speculative helpers. Preflight for duplicate titles or aligned existing Tickets; create records one at a time when IDs must be captured safely; apply only approved sprint-item/day placement; then read every Ticket back and verify id, Worker type, parent, effective sprint, day membership, Kickoff proposal, and observed status. The Closeout report should distinguish the tickets just created from the eventual implementation work they will plan or perform.

## Investigate briefly before ticketing when asked

If the user asks to “look into” a Panels behavior, says something feels off, or asks for your opinion/diagnosis, treat that as a request for orientation—not authorization to complete the repair.

1. Inspect only the smallest evidence set needed for a useful rough diagnosis.
2. Report promptly: what is proven, the likely mechanism, and what remains uncertain.
3. Stop before source edits, database mutation, restarts, broad reproduction matrices, independent review, or canonical verification.
4. Ask whether the user wants a Ticket for the complete investigation and fix.
5. Create that Ticket only after explicit approval. Put the evidence and unanswered questions in Kickoff; do not treat an exploratory patch as an accepted design.

For cross-Ticket worker identity symptoms, use `references/session-identity-diagnostics.md` to distinguish database ownership, CLI lookup, gateway identity, and terminal-environment propagation before scoping the Ticket.

For a regression in work that was already merged or closed, inspect the actual live/integration branch and the existing regression coverage, not merely the session’s current worktree. A stale worktree can make a shipped feature appear absent. Ground the likely seam, label hypotheses as hypotheses, and put an exact red-capable reproduction requirement into the follow-up ticket before suggesting a fix.

When the user says a completed feature was done poorly and explicitly asks for a ticket, create a focused follow-up rather than reopening or silently expanding the completed ticket. Read and reference the original ticket so the follow-up preserves its working infrastructure and narrows the deficit. Capture concrete interaction requirements in the intake note (for example drag/drop, clipboard paste, pending previews, per-item removal), plus explicit preservation boundaries. If the user requests HTML mockups during planning, record that as a required ticket-owned planning artifact—not as an implementation deliverable—and keep the worker responsible for shaping the canonical Success, Approach, and Plan.

If the user is discussing a structural/product idea and says to “act as if we’re doing this now,” treat that as continuing the design conversation unless they explicitly ask to create a ticket or start implementation. Do not prematurely convert exploratory discussion into a ticket. Keep the conversation concise: broad options first, no long essays, then let the user choose what to examine next.

Treat **decision status** as part of the design content. Language such as “might,” “maybe,” “I think it depends,” and “one option is” identifies a possibility, not an approved direction. Answer every material part of the user's message, reflect the option and its tradeoffs, and ask at most one decision question at a time. Do not turn each tentative thought into a recommendation, policy, user note, recap, artifact rewrite, or proposal. Before canonicalizing a choice, rely on explicit acceptance or briefly restate the exact choice for confirmation. If tentative language was recorded as settled, repair the canonical record rather than expecting chat context to correct it.

When creating a ticket from a design conversation, treat notes as the place to carry **agreed** context before the worker shapes canonical fields. Prefer ticket-level user note plus light field notes over writing the whole ticket as if it were already settled.

When a pending Plan links a visual comparison artifact, treat the artifact and proposal as one decision package. If the user narrows the direction, remove the discarded variants, verify the revised managed artifact through the actual Panels preview, preserve the correction in the Plan field note, and supersede the pending Plan proposal so its option names and implementation steps match the artifact. Do not leave stale proposal wording for chat to explain away.

For Panels runtime/model questions, use `references/runtime-model-home.md` to check whether workers/Chief inherit the Hermes-home default or have an explicit Panels-side model setting.

For Panels role-skill or slash-menu discoverability questions, use `references/skill-provisioning-slash-menu.md` to distinguish native Hermes skill discovery, the symlinked Panels Hermes home, gateway `commands.catalog`, and role preloading.

For errored worker recovery and `/compress`/retry pitfalls, use `references/worker-recovery.md`; for the detailed stale-`errored`/live-`slash_worker` pattern, see `references/worker-recovery-stale-errored-status.md`.

When a conversation shows generic **unsupported content**, use `references/conversation-unsupported-content.md` to distinguish a server protocol rejection from browser-generated fallback content and to diagnose orphaned ACP replay updates without interrupting a live Worker.

For importing work completed outside Panels, especially when the user asks for one completed reconciliation rather than several tickets, use `references/external-work-reconciliation.md`.

## External-work reconciliation

When the user asks to reconcile completed external work, prefer the explicit Chief external-work commands. Keep the shape the user asked for: if they say “one completed external-work ticket, not three,” create/reconcile exactly one ticket and put the connected parts into that ticket's five canonical fields rather than splitting them. A direct user report that work is already done is valid reconciliation evidence, but attribute it and do not invent stronger verification.

If `panels chief ...` exists in the CLI but the running server returns HTTP 404 for the Chief route, treat it as a server/code version mismatch. Do not abandon the reconciliation or create separate tickets. Use the ordinary ticket flow as a fallback: create one ticket, then propose/approve Success → Approach → Plan → Implementation → Closeout with explicit `--ceiling` and `--at-cap` transitions. When a ticket is parented to a sprint item, omit redundant `--sprint` and `--project`; they are derived and can be rejected.

For an existing ticket, Chief reconciliation rejects active control and pending proposals. Inspect chat state before clearing control. When no turn is running and the user has explicitly reported the work complete, park dispatch with `at_cap=stop`, settle any compatible pending proposal, then reconcile to `done`; never edit the database directly. See `references/external-work-reconciliation.md` for the exact sequence and current five-field command shape.

## Active worker and shared-worktree pitfall

When a Panels ticket is already being implemented by the Panels worker, do not casually dispatch a separate coding subagent into the same shared worktree. External delegations have independent time/call limits and can leave partial edits that confuse the worker loop. Prefer inspecting the ticket, chat state, events, and focused tests read-only; if recovery implementation is needed, either let the Panels worker continue, create a follow-up ticket, or explicitly coordinate a single owner for the worktree.

When a ticket shows `ticket_status: errored`, do not immediately release/retry it. First inspect the ticket's `chat_session_key`, raw Hermes logs, and `slash_worker` process. If the raw worker session is still making API/tool calls or has just received `/compress`/`continue`, wait and monitor; a release can interrupt the live turn and create another false errored result. After manual recovery, treat canonical ticket `state` and gated field values/proposals as authoritative over a possibly stale `ticket_status`. If release is appropriate, remember that it rings readiness: a runnable ticket on today may immediately become `agent`, so verify and report the observed post-release status rather than promising `empty`.

## Debugging visibility

When a user says they cannot see a ticket:

1. Check the ticket itself: `panels ticket show <ticket_id> --json`.
   - `sprint_id` shows sprint scope.
   - `sprint_item_id` shows parent-item scope.
   - `day_ids` shows whether it is on any day board.
2. Check all tickets if needed: `panels ticket list --json`.
3. Check the day: `panels day show --json`.
4. Check current sprint: `panels sprint show current --json`.
5. Interpret the result:
   - In `/api/board` / Workspace only if it is in today's `day_tickets`.
   - In `/api/sprint/current` loose tickets if it has `sprint_id` and no sprint item.

## Safety

If adding the ticket to today would change execution state, tell the user what will happen and ask or wait for explicit instruction unless they already asked to make it visible/workable today.
