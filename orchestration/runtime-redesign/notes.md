# Runtime redesign — fix-spec

A short-lived fix-spec for the v2 planner redesign: we built v2 poorly; this records what's
wrong, what we'll fix, and how (where we know). It feeds ticket planning, and its **OPEN**
items are the launch points for follow-up exploration agents — only agreed things live here;
it's the reference, not the discussion.

**Tags** (bold, at line start, grep-friendly): `DECIDED` settled · `REMOVE` take it out ·
`REWORK` reshape it · `OPEN` must resolve within this redesign · `PARKED` deferred beyond it.
Every **OPEN** / **PARKED** item names, in a trailing clause, what decides or unblocks it.

**Maintenance:** edit items in place; never append corrections — git is the history.

**Process from here — how we spike.** A load-bearing **OPEN** with real *empirical* risk is
handed to a Fable agent as a **spike**: give it the project context + the specific things to
prove + the read-only research boundaries; it goes and **touches the real thing** (runs it, not
just reads about it), gathers live evidence, and writes a decisive, evidence-backed plan to
`spikes/NN-name.md`. That plan is a *proposal* — findings are discussed with the owner and only
the adopted parts fold back here. One question per spike, run as we go. Not every OPEN needs a
spike: most remaining ones are **design / taste calls** (settled in discussion, then recorded
here), not empirical unknowns — reserve spikes for "will this even work."

**Spike log.**
- `spikes/01-hermes-linkage.md` (2026-07-06) — Hermes linkage / the agent-operation primitive.
  Option 3 proven end-to-end against the real gateway; role skills at kickoff feasible via the
  child's env; the run/approval boundary is code-side and clean. Adopted findings folded into
  *Agent runtime* + *Scheduling & runs* below. **This was the one big empirical risk — retired.**

---

## Principles

- **DECIDED · v2 = v1 + gates + a bit more structure + a DB.** v1 (the old markdown planner +
  its skills, esp. rollover at `~/.hermes/skills/productivity/rollover/`) is the behavioral
  reference. The only real differences in v2: approval flows (agents propose, humans approve —
  often lightweight: a change + a reason to quick-approve), a bit more structure, and a DB
  instead of markdown files. Port v1's behavior; don't reinvent it. When unsure how something
  should work, the default is "like v1, plus the gate."
- **DECIDED · Skills own the intelligence; code holds the gates and storage.** How to *do*
  things — work a ticket, roll over a day, orchestrate the board — lives in Hermes **skills**
  loaded per role at kickoff, as v1 did. The server holds only the structural spine: the gates
  (propose → human approves), storage (the DB), and the run/connect mechanism that spawns a
  mind. Very little "how to do things" should live in the server — when something is judgment,
  it belongs in a skill, not Python.
  - *Caveat — system prompts.* Skills are the primary home for intelligence, but the per-agent
    **system prompt** (set at kickoff) may be a secondary lever for role framing and lightweight
    persistent guidance. Where it lives and how it works is TBD — to be discovered after looking
    into and setting up chats. Contingent on a clean, easy way to set per-agent system prompts;
    v1 handled this poorly.
- **DECIDED · Approval machinery is invisible to the model.** The model only ever "does the next
  step." Approval, "how far it may go," gates, and stopping are all code-side and **invisible**
  to it. It may know its step awaits approval; it must never know the approval machinery
  (who/what approves, the rules, the grant). The only inputs it ever receives are next-step
  prompts and the human's free-form chat. Approval never travels to it as a message.
- **DECIDED · Code owns all state stamps; agents propose, one door writes.** Stamping state is
  not the agent's concern. The code owns and writes the ticket's status/worker stamp (System B →
  *Scheduling & runs*) and canonical field values; agents only ever submit proposals through the
  gate. There is a single door per state transition — the agent never stamps state directly.

---

## Shape at a glance

> **NON-CANONICAL** — a 30-second map; the sections below are canonical. Nothing here is a
> decision source.

- Each ticket has **one durable agent** ("mind" = one Hermes session), reached via the Hermes
  gateway as a stdio subprocess (Option 3), fed by a per-session serialized queue. → *Agent runtime*
- The same mind is who the human chats with (per-ticket chat); a separate **global chat** talks
  to the main agent about the whole board. → *Agent runtime*
- Two code-side systems drive work: **System A** decides what is *ready*; **System B** *sets off*
  an agent on a step and owns the ticket's run-status. Kickoff is just "step 0." → *Scheduling & runs*
- The **ticket itself** carries its status (`empty` / `agent_working` / `awaiting_approval` /
  `errored`) — no separate `runs` row. Every run ends awaiting approval; the code checks the
  proposal is present. → *Tickets*
- Intelligence lives in **role skills** (worker / rollover / main-agent) loaded at kickoff; the
  server holds only gates + storage. → *Agent runtime*, *Principles*
- **Rollover** is a staged, agent-driven flow rebuilt on v1's model. → *Days & rollover*

---

## Agent runtime

*Minds, chat, and the run primitive are one subsystem.*

- **DECIDED · One mind per ticket.** One durable Hermes session (`session_key`), resumed for
  each step. The same session is who the human chats with — chat and worker are the same agent.
- **DECIDED · Connection: Option 3.** Reach the mind via the Hermes gateway run as a **stdio
  subprocess** (JSON-RPC: `session.create` / `resume` / `prompt.submit`, streamed replies).
  Child runs on Hermes's own interpreter, so no venv coupling to our server; structured
  start/finish/error events. Not in-process (the old planner's mistake — it re-execs itself into
  Hermes's venv and mounts the gateway); not a network daemon (that's later scale-out, a pure
  transport swap on the same protocol).
  - **VALIDATED (spike 01):** proven end-to-end against the real gateway —
    `venv/bin/python -m tui_gateway.entry` speaks newline JSON-RPC; create → prompt → stream →
    close → **resume the same durable key in a fresh process** with history intact; ~0.1s cold
    start, agent build ~3.3s fresh / ~0.9s resume; 85 RPC methods enumerated; provider
    prompt-cache survives process restarts.
- **DECIDED · Per-session serialized input queue.** One in-flight run per `session_key`. Chat
  messages, next-step prompts, and wakes are all producers into it; the executor drains it by
  resuming the session when no run is live. Solves chat-mid-run and approval-lands-mid-run races
  for free.
  - **VALIDATED (spike 01) — now correctness, not just ergonomics.** The gateway's busy-guard
    (`4009 session busy`) is **per-process only**; with the child-per-run topology (below) two
    children could resume the same `session_key` concurrently. Our per-session queue is the only
    thing preventing that — it is load-bearing correctness.
- **DECIDED · Agent-operation primitive + role skills.** One primitive for how a mind is run: it
  loads a **role-scoped Hermes skill** at kickoff (+ system prompt — see the caveat in
  *Principles* — + tool/CLI surface), parameterized by role; the intelligence lives in the skill,
  not the server. Skills: a **worker skill** (ticket agents — work a ticket, propose the next
  step, raise a hand), a **rollover skill** (drives the rollover flow → *Days & rollover*), a
  **main-agent skill** (global chat / board orchestration). Set at kickoff the way the kanban
  flips a worker into worker-mode. Scope as v2-namespaced skills while proving v2; then they
  become the primary way the planner operates.
  - **VALIDATED (spike 01) — the mechanics.** Role skills load via `HERMES_TUI_SKILLS=<skill>`
    in the gateway child's env: the SKILL.md is force-injected into the ephemeral system prompt
    at agent build (proven live — the model named the skill and quoted its content with tools
    forbidden; a bad name fails loudly pre-model-call). Granularity is **per gateway process**,
    not per session — so a role = **one gateway child per run** carrying role env, which is
    exactly the kanban worker shape. The primitive is
    `run_step(session_key | None, role, prompt) → RunResult`: spawn child with role env →
    `session.resume` (or `create` for step 0) → `prompt.submit` → drain events →
    `message.complete` → reap child. The **system prompt** is the planner home's config
    (home-wide baseline), not a per-session param; per-ticket context rides the next-step prompt
    + per-run env (kanban's `HERMES_KANBAN_*` pattern). Slash menu for the UI =
    `commands.catalog` (135 commands + categories + alias map). Details + follow-up tickets in
    `spikes/01-hermes-linkage.md`.
- **DECIDED · Dedicated planner `HERMES_HOME`.** Planner minds run in a dedicated Hermes home,
  isolated from the owner's real `~/.hermes` (own `state.db` / `skills/` / config). Keeps planner
  sessions out of the personal session list and lets us pin model / role skills / system prompt
  for planner agents without touching the personal config. Costs a one-time provisioning step
  (model config + creds + the v2 role skills synced in) — a small follow-up (spike 01 open
  sub-Q1). Proven in spike 01: a fresh home fully isolates and fails loudly until creds are
  provisioned; the v2 role skills install here.
- **DECIDED · Per-ticket chat + global chat.**
  - Per-ticket chat is the same one-mind session (above).
  - **Global chat** is a planner-wide surface where you talk to the **main agent** about the
    whole system (reorganize a sprint, broad orchestration), NOT scoped to one ticket/day. Today
    chat is per-entity only (tickets, days); there is no global chat. It's the natural home for
    the **rollover mini-review** and the **next-day-direction** exchange (both planner-level →
    *Days & rollover*). Mechanically the same one-mind/chat model as ticket chat (Option 3 stdio
    gateway + per-session queue), just a **global session** (the main agent) instead of a
    per-ticket one.
- Every mind receives only next-step prompts + human chat; the approval machinery stays
  **invisible** to it (→ *Principles*).

---

## Scheduling & runs

- **DECIDED · Two code-side systems, designed separately.**
  - **System A — Readiness / poll.** Decides what is *ready* to be worked (dependencies met,
    not blocked, nothing already running). It does no work — it only says "this one's ready."
    Polls state, never the model.
  - **System B — Set-off / run primitive.** Actually starts an agent on a step. Shared by the
    poll (A) and the post-approval flow. Owns the ticket's run-status.
- **DECIDED · Kickoff = step 0 (approving).** Don't treat starting a ticket as special — it's just
  "step 0 approved." Both kickoff and approval feed System B: "set off step N of ticket X"; System
  B never special-cases fresh vs continuing. Whether a fresh ticket auto-runs its first step or
  waits for the human is governed by the **grant** (step 0 is the first advance), same as any later
  step — no separate kickoff concept.
- **DECIDED · System B is the writer; structured events bound each run.** System B launches the
  agent (→ `agent_working`) and observes it end (→ `awaiting_approval` / `errored`); it never
  lets the agent stamp status (→ *Principles*, *Tickets*). Option 3's structured events guarantee
  the code always knows start and end; if it ever doesn't, the transport is structured wrong.
  - **VALIDATED (spike 01) — topology.** System B spawns a **fresh gateway child per run**
    (kanban-shaped), carrying role env; it resumes (or creates step 0), `prompt.submit`s, drains
    to the single `message.complete`, then reaps the child — process-exit is a second, OS-level
    end-of-run signal. `run_step()` (→ *Agent runtime*) is this primitive; kickoff = step 0
    through it. ~1s spawn/resume overhead per step is noise against multi-minute steps.
- **DECIDED · Poll efficiency.** Query only candidate tickets (a WHERE on status / readiness),
  not a full-table re-scan-and-unpack every tick. (Implementation detail for the System A pass.)
- **DECIDED · Fast path.** Keep the timer as a backstop; add a fast path so an approval / unblock
  pokes System B immediately rather than waiting up to a full tick. (Detail for the System A pass.)
- **DECIDED · "Can't proceed yet."** It's a **readiness** call in code — the poll simply doesn't
  start the agent; there is no stop-condition on the agent (an agent never reports "blocked"; see
  the REMOVE below).
- **REMOVE · Agent can close a run `blocked`.** An agent never reports "blocked"; it only finishes
  a step by offering its result for approval. "Can't proceed yet" is a code decision to not start
  the agent (OPEN, above). Removing this deletes the respawn-forever loop (audit P1).
- **REMOVE · Budget / per-tick concurrency cap.** Never asked for; adds slop plus a starvation bug
  (counted per claim-attempt). No cost cap wanted now. (Any concurrency cap later — currently no;
  revisit only if one is later wanted.)
- **REMOVE · Timeout-as-failure.** Don't kill-at-30-min-and-count-a-failure with no liveness
  check; two long-but-healthy runs shouldn't trip a breaker.
- **REMOVE · Circuit-breaker / "failure."** "Failing" now = a run ends `errored` (crashed, or ran
  but left no proposal). Errored **stops and surfaces** to the human; it does **not** auto-retry
  (for now) — so there's no retry loop to cut off, hence no breaker. The breaker's columns
  `auto_blocked` / `consecutive_failures` (earlier marked "deferred to the tail pass; not
  decided") go with it. FUTURE: as real errors occur, decide case-by-case where auto-retry is
  worth it.
- **REMOVE · Full config re-parse every tick.** Read the one on/off setting, or read at boot —
  not the whole YAML file every tick. (The made-up config knobs themselves → *Data model*.)
- **DECIDED · Dispatcher machine-lock — KEEP.** Well-scoped, harmless; leave it.
- **PARKED · Reclaim (kill-less / liveness-blind).** The one real question left: what if the agent
  process dies in a way the code never cleanly observes (a hard machine kill)? Everything else is
  handled by the code writing `awaiting_approval` / `errored` itself. → unblocked by a dedicated
  reclaim / liveness pass.

---

## Tickets

- **DECIDED · Status belongs to the ticket (the old "lock", reframed).** Replace the claim-lock +
  separate `runs`-row split with the ticket's own status field, plus a "who/what is working it"
  field. **Ticket status**: `empty` (nothing started) / `agent_working` / `awaiting_approval` /
  `errored`. It just reflects where the ticket is; the UI reads this one field.
- **DECIDED · Every run ends on `awaiting_approval`, never "finished".** An agent always ends a
  step by leaving something for approval — it never declares itself done. "Done" is a *ticket*
  state reached when a human approves the final step, not an agent-run state.
- **DECIDED · Proposal-present invariant.** On run end, the code checks the expected proposal is
  actually there — a structural invariant. The code knows what step it asked for, so it knows
  what *should* now be awaiting approval. Present → `awaiting_approval`. Absent → the agent didn't
  do its job → `errored` (flag it: "you haven't done your job"). Catches a run that burned a turn
  and left nothing to approve, which would otherwise pass as fine.
- **DECIDED · Single atomic write; doubles as the UI field.** Status + worker are written together
  in one UPDATE — this dissolves the "runless orphan" (there is no second write to crash between).
  "What's going on and where" reads straight off this status. (Writer = code / System B, never the
  agent → *Principles*, *Scheduling & runs*.)
  - **OPEN · Runless-orphan fallback.** If an orphan ever appears anyway: don't self-heal — set
    the ticket `errored` for a human. → the fallback policy is to be decided.
- **DECIDED · Ticket proposal model — bundle.** NOTE: tickets have **no `body`** column — a
  ticket's content is `title`, `recap`, and the four fields (success / approach / plan / result,
  each value / proposal / notes). So "change the content" means title / recap / a field. A content
  change **rides the agent's normal end-of-step proposal** — one approve-act, like any advance
  (not a separate before/after diff approval; lightest gate, "v2 = v1 + a light gate"). Agents
  still can't change ticket **state** directly (goes through the gate); the out-of-band "work done
  elsewhere" feature that might relax that is a separate future thing.
- **OPEN · Ticket body / details.** Tickets have no body today; they'll likely need somewhere for
  details (a body of some sort). Form TBD. → resolved when the ticket-content shape is decided.
- **DECIDED · Grant / ceiling — KEEP.** `ceiling` / `at_cap` stay — that's the grant, which is
  wanted: the code's auto-approve-up-to-a-ceiling policy (the "code sometimes auto-approves"
  mechanism, not a contradiction of propose → approve).
- **REMOVE · Old-model claim columns.** `claim_lock`, `claim_expires` — replaced by the
  code-owned run-status field.
  - **OPEN · `alias`** (migration leftover) → review separately.
- **REMOVE · `runs` table.** The ticket carries its run-status; drop the separate `runs` table
  (no keep-as-history). Its CLI counterpart, the `run` group, → *CLI*.

---

## CLI

- **DECIDED · One CLI, scope via naming.** One `plan` CLI for both ticket-agents and the
  main-agent/human; ticket-agent vs main-agent responsibilities are conveyed by skill + verb
  **naming**, not two binaries. The goal is *clarity*, not *enforcement* — agents are trusted
  (single-user local planner), so no hard scoping barrier is needed.
- **DECIDED · `serve` split.** `plan serve` → a separate server-ops entry (an agent shouldn't
  start the server).
- **REWORK · `run` group + run/claim headers.** `plan run heartbeat`, `plan run close --outcome
  done|blocked`; `X-Plan-Run-Id` / `X-Plan-Claim` — bound to the claim/heartbeat/close-blocked
  model being replaced. New model: code observes run end (no agent `close`), liveness is
  code/transport-owned (no agent `heartbeat`), the claim becomes a code-owned ticket status (no
  agent-carried claim). (The `runs` table itself → *Tickets*.)
- **DECIDED · Verb details (agreed).** Verbs read action + entity (`create ticket` / `show ticket`
  / `list tickets`).
  - `create ticket`: `title` required, `deadline` optional, **`project` referenced by a project
    ID** (→ *Data model*, projects table; project likely optional, not required), `--item` →
    `--sprint-item`.
  - `list tickets`: add a `--day` / `--date` filter (this is what makes `pickup` unnecessary).
  - `show ticket`: returns essentially the whole ticket.
  - `propose`: params = what-it-proposes + ticket id + body + recap; exact proposal shape
    (intra-ticket field proposal vs other kinds) TBD → *Tickets* proposal model (OPEN).
  - Keep `create` / `show` / `list`.
  - No standalone `recap` verb — recap stays a **top-level** ticket field, written/overridden via
    `propose`.
- **DECIDED · Drop `queue pickup`.** It's only a read-out of the dispatcher's ready set (not
  load-bearing — the dispatcher computes readiness internally), and the board's ticket status
  already shows what's ready. Caveat: keep only if a distinct "what's queued next" view is wanted
  that the board doesn't give.
- **OPEN · `note` verb.** Unclear it should exist. → resolved by the CLI-surface decision.
- **OPEN · `day` verbs.** → *Days & rollover* (canonical).
- **OPEN · Bodies inline vs file/stdin.** Current CLI forbids inline bodies (`--body-file` / stdin
  only). That only matters if the CLI is invoked through a shell; with an argv-array invocation an
  inline body is safe. → decide against how agents actually call the CLI.

---

## Days & rollover

Rebuild on v1's staged, agent-driven model. v1's rollover is the reference
(`~/.hermes/skills/productivity/rollover/`): an AGENT owns it, staged, with a brief human
checkpoint. v2's current boundary is a thin deterministic slice (close yesterday with counts +
one agent call that writes only the overview blurb; places no tickets, reconciles nothing).
Rebuild it as the staged flow on the DB.

- **DECIDED · Orchestration: one mind + the rollover skill drives it.** The staging and
  reconciliation logic lives in the **skill**, not the server. Cron / autonomous path: the agent
  drives it to completion. User-in-the-loop path: it drives and pauses at the human checkpoint (a
  gate), then continues. The server provides only the gate + storage.
- **DECIDED · Stage 1 — make the old day true: minimal now.** Work flows through the app, so
  ticket statuses are already current — little to clean. **Rollover doesn't change tickets**:
  tickets have their own gate (propose → approve on fields / state) and stay current as work flows
  through the app, so rollover relies on that and does not touch them (it may read them for
  context). FUTURE: out-of-band work the owner does elsewhere must feed back in — the only thing
  that would make rollover reconcile tickets — deferred.
- **DECIDED · Stage 2 — boundary mini-review (brief).** A short next-day-direction exchange, then
  write the new day. Its home is **global chat** (→ *Agent runtime*). (With old-day-truth and
  sprint reconciliation both handled elsewhere, rollover for now is basically this review +
  writing the new day.) Sprint-item reconciliation is handled separately (→ *Sprints & sprint
  items*, OPEN).
- **DECIDED · Stage 3 — write the new day: not conservative.** The new day itself rides an
  approval flow (agent proposes the day, owner approves), so shape it fully rather than scaffold
  cautiously. Carry the right tickets forward (In Progress / still-relevant Todo / carryover;
  never Done or unchosen sprint work) and write the overview.
- **OPEN · Approval for agent day-composition.** An agent putting tickets on a day — placing,
  creating, or carrying them over — needs some level of approval too (kickoff = approving applies
  here). Exactly how is unsettled: bundled into the day-approval (approve the proposed day =
  approve its ticket set) vs per-ticket, and how creates vs carryovers differ. → resolved when the
  day-composition + approval flow is worked out.
- **DECIDED · Port the core flow first.** v1's skill has a `references/` library (repo-evidence
  splitting, failsafe scaffolds, multi-day gaps, …) — the "built to expand" tail; port the core
  flow first.
- **REMOVE · Plan-tree / day-plan / replan subsystem.** The whole subsystem, not just the
  `days.plan` column. Dead: the boundary stopped seeding the plan, so every plan endpoint errors
  "no plan." Its job — an agent placing the day's tickets — is already covered by the rollover
  rebuild. Delete tree / effects / replan-queue / plan-endpoints / plan-events + `days.plan`.
- **REMOVE · `boundary_runs` table.** Its only job is a dedup guard (did the daily rollover run
  for this date). Replace by simply checking whether the next day is already planned / materialized.
- **OPEN · `day` verbs.** Which (if any) `day` CLI verbs exist is unsettled (paired with the
  `note`-verb question → *CLI*). → resolved with the rollover flow + CLI-surface decision.

---

## Sprints & sprint items

- **OPEN · Sprint-item change model.** Today it's a status-only `status_proposal`. How a sprint
  item should be changed and approved is unsettled. (Earlier "whole item in one go + reason"
  wording was a misspeak that actually applied to tickets — see the ticket proposal item.) →
  **direction set:** the ticket proposal model is now DECIDED as *bundle* (one approve-act →
  *Tickets*); apply the same one-act shape to sprint-item changes when this layer is built. Exact
  shape (which fields, the "reason") settled then.
- **OPEN · Sprint-item reconciliation at rollover.** Depends on the sprint-item change model
  (above), which is unsettled — don't assume a settled gate here yet. (Referenced from *Days &
  rollover*.) → unblocked by the change model.
- **DECIDED · Confirmed FK / join facts (no change).**
  - Sprint items get their tickets via the ticket → `sprint_item_id` FK (tickets point up to their
    item; no separate list).
  - Days get their tickets via the `day_tickets` join table.
  - `day_tickets` is many-to-many by design: a day has many tickets, a ticket can appear on many
    days (how a ticket carries over day to day).
- **REMOVE · `sprint_items.current_state_note`.** Unnecessary for now; drop from the schema.
- **OPEN · `blocked_by`.** `sprint_items.blocked_by` (JSON list of *ticket* ids) is folded into
  the deferred "what do links mean" thread → *Data model* (blocking + links semantics). →
  unblocked by the links pass.

---

## Data model (cross-cutting only)

*A schema change lives with the subsystem that reasons about it (`runs` → Tickets, `boundary_runs`
→ Days & rollover, claim columns → Tickets, `current_state_note` → Sprints). This section keeps
only what no subsystem owns.*

- **OPEN · Blocking + links semantics.** Blocking lives in two places: `links` (`kind='blocks'`)
  and `sprint_items.blocked_by` (JSON list of *ticket* ids). Relationships overall are spread
  across FK columns (`sprint_item_id`, `sprint_id`), the generic `links` table, and `blocked_by`
  JSON. Open: item↔item vs ticket↔ticket blocking granularity, and what `links` owns vs the FKs.
  Its own pass. → resolved by the links pass.
- **DECIDED · Projects table.** Projects become a first-class table with IDs; tickets reference a
  project by ID (project likely optional, not required). (`project` is a repeated enum string on
  `tickets`, `sprint_items`, `ideas` today.) Used by `create ticket` → *CLI*.
- **REMOVE · Orphan dormant columns.** `sprints`: `weekly_addenda`, `kickoff_frozen_at`,
  `review_frozen_at` — dormant; drop from the schema. (`days.plan` → *Days & rollover* plan-tree
  removal; `sprint_items.current_state_note` → *Sprints & sprint items*.)
- **REMOVE · Seed-importer permanent surface — keep as a script.** Remove the `/api/seed` route,
  CLI verb, dedicated error code, idempotence + skip-audit machinery, `--demo`. KEEP the importer
  as a one-shot standalone **script** for the cutover migration.
- **DECIDED · `events` table stays (debugging).** The `events` table stays, but the owner does not
  want the browser watching the feed and refetching everything; for the owner the log exists
  primarily as a **debugging** tool. How the UI actually updates (the data flow) is a separate
  later pass → *UI & data flow* (PARKED).
- **REMOVE · Config tunables (made-up knobs).** Keep the real, localised config; remove the
  made-up knobs — settings for removed machinery (→ *Scheduling & runs*), and the `title_max_chars`
  config-vs-DDL-vs-startup-assert triple.

---

## UI & data flow (PARKED)

*Deferred beyond this redesign — the UI / data-flow pass owns all of it.*

- **PARKED · Event-feed usage / UI data-flow.** Owner does not want the browser watching the feed
  + refetching everything (the log is primarily a debugging tool → *Data model*). How the UI
  actually updates is a separate later pass. → unblocked by the UI / data-flow pass.
- **PARKED · Client-side state-machine copy → one shared source.** The server owns the state
  machine and sends the derived flags (next state, field passed, is-gating); the browser renders
  them — no second hand-written copy. → deferred to the UI pass.
- **PARKED · Duplicate frontend helpers → write each once, reuse.** `segToggle`, `make` / `el` /
  `quiet`. → deferred to the UI pass.
- **PARKED · Chat-input swap registry ("audio seam") → remove; text-only input.** → deferred to
  the UI pass.

---

## Removal & rework ledger

*Pointer-only — one line each; rationale lives in the linked section.*

- **REMOVE** · agent-closes-run-`blocked` → *Scheduling & runs*
- **REMOVE** · budget / per-tick concurrency cap → *Scheduling & runs*
- **REMOVE** · timeout-as-failure → *Scheduling & runs*
- **REMOVE** · circuit-breaker / "failure" (+ `auto_blocked` / `consecutive_failures`) → *Scheduling & runs*
- **REMOVE** · full config re-parse every tick → *Scheduling & runs*
- **REMOVE** · `runs` table → *Tickets*
- **REMOVE** · claim columns (`claim_lock` / `claim_expires`) → *Tickets*
- **REMOVE** · orphan dormant columns (`weekly_addenda`, `kickoff_frozen_at`, `review_frozen_at`) → *Data model*
- **REMOVE** · `sprint_items.current_state_note` → *Sprints & sprint items*
- **REMOVE** · plan-tree / day-plan / replan subsystem (+ `days.plan`) → *Days & rollover*
- **REMOVE** · `boundary_runs` table → *Days & rollover*
- **REMOVE** · seed-importer permanent surface (keep one-shot script) → *Data model*
- **REMOVE** · config tunables — made-up knobs (+ `title_max_chars` triple) → *Data model*
- **REMOVE** · chat-input swap registry (text-only) → *UI & data flow*
- **REWORK** · `run` CLI group + run/claim headers → *CLI*
- **REWORK** · client-side state-machine copy → *UI & data flow*
- **REWORK** · duplicate frontend helpers → *UI & data flow*
- **DECIDED (KEEP)** · dispatcher machine-lock → *Scheduling & runs*
- **DECIDED (KEEP)** · grant / ceiling (`ceiling` / `at_cap`) → *Tickets*
- **DECIDED** · `serve` split (server-ops entry) → *CLI*
- **DECIDED** · drop `queue pickup` → *CLI*
- **DECIDED** · projects table → *Data model*
- **DECIDED** · global chat → *Agent runtime*
- **DECIDED** · agent-operation primitive + role skills → *Agent runtime*

---

## Open & parked index

*The menu of follow-up exploration tasks. Pointer-only — one line each.*

- *(resolved → **DECIDED** in Scheduling & runs: poll efficiency · fast path · "can't proceed yet")*
- **OPEN** · runless-orphan fallback → *Tickets*
- *(resolved → **DECIDED** ticket proposal model = bundle, in Tickets)*
- **OPEN** · ticket body / details → *Tickets*
- **OPEN** · `alias` column (review separately) → *Tickets*
- *(resolved → **DECIDED** one CLI, scope via naming, in CLI)*
- **OPEN** · bodies inline vs file/stdin → *CLI*
- **OPEN** · `note` verb → *CLI*
- **OPEN** · `day` verbs → *Days & rollover*
- **OPEN** · approval for agent day-composition (creates / carryovers) → *Days & rollover*
- **OPEN** · sprint-item change model → *Sprints & sprint items*
- **OPEN** · sprint-item reconciliation at rollover → *Sprints & sprint items*
- **OPEN** · `blocked_by` (folded into links thread) → *Sprints & sprint items* / *Data model*
- **OPEN** · blocking + links semantics → *Data model*
- **PARKED** · reclaim (kill-less / liveness-blind) → *Scheduling & runs*
- **PARKED** · event-feed usage / UI data-flow → *UI & data flow*
- **PARKED** · client-side state-machine rework → *UI & data flow*
- **PARKED** · duplicate frontend helpers → *UI & data flow*
- **PARKED** · chat-input removal (text-only) → *UI & data flow*
