# Panels simplification — working notes (paired conversation)

Working file for the simplification program. Decisions phase COMPLETE as of
2026-07-25 — every pre-build question has a recorded verdict (mirrored in
permanent memory: panels-simplification-program.md). Talk-only until the
final VPS operating-model ticket lands.

## Ground rules (owner)

- Better, not perfect; rounds are fine. Never everything in one go.
- Loop per system: problems → wants → implications. Decisions later.
- Maintenance test: a system sucks to maintain if we're having to talk
  about it now. "Much easier to use a package and not use half of it."
- Within-system bugs are not program concerns.
- One-person app: every failure visible with its cause; manual recovery
  always possible; no automatic recovery machinery.
- Prime directive: the offense in anything, ever, is a user that doesn't
  know what's going on. Degradation tolerable; silent breakage is not.
- Each system owns its own facts; composed queries fine with segmentation
  and clear language — never an unnamed cross-system blend.
- The problem is foundations and naming, not feature count.

## Boundary map (final)

- The record — storage + history: tickets (subsystems: status, current
  stage position), sprints, days, projects, ideas.
- Worker system: per-worker-type stage definitions (ordered path, ownership
  worker/user/paired, specialist identity) + the proposal resolver (the one
  door worker output passes through).
- Agent system: standalone role agents (today: chief of staff).
- Worker orchestration — ONE system: readiness loop + readiness check +
  readiness wake + step runner as subsystems. Composes what text is sent
  and when. VERDICT: containment, not replacement; no stack pull.
- Agent conversation — its own ticket-ignorant product (see boundary
  contract below).
- Errors: policy top-level, absorbed per system. Chain insight: browser ↔
  server ↔ agent process ↔ backend are separate links; honest per-link
  health, "unknown" displayable.
- Surfaces: web + CLI.

### Conversation ↔ orchestration boundary (owner "Agreed", 2026-07-25)

Conversation host owns: session start from a kickoff request (who the agent
is / what kind / where it starts from — sized to need); the single durable
transcript copy + fetch-then-tail contract; backend catalog (hermes, codex,
claude) and CLI process lifecycle (spawn, session-load recovery,
replaceability — new session or backend behind the same owner); session
facts stated as facts (busy / turn ended + how / send failed + stderr); its
own error-log lines. Knows nothing of tickets/stages/statuses/approvals.
Orchestration owns: readiness loop/check/wake; statuses via the write door;
stages + proposal resolver; composes step messages (incl. pending worker
context); all interpretation into ticket facts. Never touches transport.
Exactly four things cross, via the host's ONE public contract (same for
runner/browser/chief): start session, send text, is-session-free, readable
transcript/facts feed.
Acceptance test: conversation side liftable to another product unchanged;
orchestration blind to a backend swap.

## VERDICTS (all owner-ratified 2026-07-25 unless noted)

1. CONVERSATION HOST — build our own, copying the converged industry design;
   NOT connecting into OpenHands. Durable sequence-numbered log per
   conversation; connect = fetch-after-N with snapshot fallback; pending
   permission prompts carried in the snapshot; subscribe-on-screen (ruled
   2026-07-24). Wire layer = official Python ACP SDK (PyPI
   agent-client-protocol, Apache-2.0, official org) — "we use that for
   sure." References: OpenHands deep-dive (primary how-to; runtime-verified),
   Mitto docs, Microsoft AHP spec. Key recipes to lift: persist ACP session
   id + cwd, lazy respawn + session/load on next message (cwd guard,
   fallback to new session); boot-time crash repair (stale running → error +
   explicit interrupted event); immutable per-event storage + state
   snapshot; full-state push on subscribe; filter non-JSON CLI stdout;
   idle-timeout resetting on updates; tool-call events start+final only;
   streaming deltas ephemeral.
   Grounds against connecting: no stability contract on OpenHands' REST/WS
   wire or event vocabulary (own TS client says alpha); 185-pkg/494MB
   minimum; 101 always-mounted routes; event-model mismatch (FinishAction
   turns, frozen 3-provider table); zero production embedders.
   Fact (owner): Panels is NOT approval-centric for CLI permissions — agents
   run bypass/full-access; ACP permission prompts are not a Panels need
   (Panels approvals are ticket/proposal-level, a different system).

2. LIVE UPDATES (replaces the event log) — final, amended: commit-time
   CONTENTLESS signal from the single write door ("something changed", no
   payload — naming what changed recreates a mapping), over auto-reconnecting
   SSE; browser query cache (TanStack Svelte Query, current for Svelte 5)
   invalidates on signal; only mounted queries refetch; structural sharing =
   zero repaint on unchanged data; reconnect/focus refetch = self-healing by
   construction. No timers; idle system does zero network. Kills: events
   table, envelope push, event-kind→resource mapping, completeness test.
   ~50-100 lines. Escape hatch if refetch volume ever bites: per-resource
   ETags derived from data (still no vocabulary).
   ACCEPTANCE CRITERION (owner-lived pain): a refetch must NEVER steal
   focus, wipe in-progress composition, or move scroll — composer state is
   component-local; server data never writes into an editor. Playwright-
   asserted, not intended.
   Conversations sit OUTSIDE this mechanism (transcript = fetch-once-on-open
   + sequence-numbered appends; nothing re-downloads mid-read).
   Build-time notes: HTTP/1.1 6-connections-per-domain cap across tabs
   (SharedWorker / Web Locks leader if it ever bites); nginx
   proxy_buffering off for the SSE route; periodic ping vs LB idle timeouts;
   native EventSource sends cookies not Authorization headers.
   Event-log history readers accounted for: paired marker scan (deleted by
   statuses rulings), review waiting_since (→ stored timestamp), CLI events
   printout (→ stored facts).

3. COMBINED SIGNAL (owner): the browser SSE ping and the readiness-loop wake
   are ONE contentless change signal per commit, fanned to both subscribers.
   No "eligibility-affecting action" vocabulary at call sites; the polling
   timer stays canonical backstop. Exact wiring at design time.

4. STACK — no changes. Python/FastAPI stays; Svelte stays; SQLite stays.
   One addition: Alembic for migrations (dissolves the hand-ladder's
   enum-change table-rebuild tax — the db pass's one carried cost).
   Agents-as-users is not DB multi-user: all writers converge on the one
   server process (CLI is an HTTP client; loops in-process) → single write
   door, short transactions; 1-2 orders of magnitude write headroom at the
   projected autonomous scale (~100-150 tickets/day, tens of thousands/yr,
   single-digit GB/yr). sqlite-vec covers the "tickets like x" embedding
   aside at 50-100k tickets. Named revisit trigger: needing >1 writing
   server process (scale-out / overlapping deploys); later Postgres move
   stays mechanical via the write door.

5. ERRORS — E1: every failure leaves a durable record. E2 rule: a ticket
   error = a ticket that isn't moving forward (outcome-based); agent-level
   troubles display on the agent. Cancel changes nothing (status stays; the
   status-vs-liveness mismatch is the honest signal). Busy is NOT an error:
   readiness checks occupancy before sending; slipped collisions queue; the
   queue stays dumb (rare race accepted).
   TOOLING: structlog → one JSON-lines file under data/, size-rotated with
   fixed retained count (bounded disk; old entries age out — fits
   trace-when-it-happens; durable significance lives on the ticket). Ids
   bound at boundaries (request → ticket id; runner → session/step ids);
   stderr tails (64KB, today built for backend-provenance only) flow on ALL
   failure paths into the same records; the ~34 existing stdlib log sites
   feed the same file. Backends' own diary logs: out of scope entirely.
   Current fact: NO logging config exists anywhere; logs_dir always empty;
   deployed planner output reaches journald only.

6. STATUSES (ruled 2026-07-24, spec for the reshape ticket) — final list of
   8: empty, blocked, agent (was agent_running_step), paired (was
   paired_work), awaiting_approval, needs_user, user (was user_takeover),
   errored. Axis: what is ACTUALLY happening right now; purpose: segment by
   what the owner needs to do.
   - proposal_discussion REMOVED; proposal object has no lifecycle state.
   - Review = pure filter status == awaiting_approval.
   - Loop acts on EMPTY only; paired never loop-startable. Every stage ends
     in an approval; approval → empty → loop sends next stage's opener.
     Re-entry to review = worker re-proposes.
   - Ripples: eligibility's paired marker scan deleted; ownership gate
     collapses to "empty and not user-owned"; eligibility drops is_blocked
     (status IS the answer); the empty/agent distinction is load-bearing in
     6 consumers.
   - needs_user vs user both kept (plea vs claim).
   - blocked: durable sibling of empty (only ever replaces empty).

7. DESIGN DETAILS (closed one-by-one, 2026-07-25):
   a. Worker start is OPTIMISTIC: all checks (readiness, occupancy) BEFORE
      sending; then one guarded atomic flip out of empty to the stage's
      resting status (the flip IS the claim — DB picks one winner; applies
      to ANY message-sending departure from empty: agent or paired); send;
      revert to empty + trace only on send failure. No claim stamp.
   b. Chat-reply to a proposal = the status flip awaiting_approval → paired
      and NOTHING else. No machinery, no display treatment; the proposal
      persists untouched; queue exit falls out of the status filter.
   c. Watch-and-settle DISSOLVED: nothing needs to notice a turn ending.
      step_runs table and settle machinery die with the old correctness
      model ("is a step running" = status + occupancy check; tickets move
      only via explicit actions; crash/cancel leaves the honest mismatch).
      Sole survivor: a failed turn writes one error-log line (ids + stderr
      tail). The transcript is the what-ran record.
   d. Blocked mechanics: blocked only stands in for empty; blockers
      consulted only when a ticket comes to rest. The completing writer
      (done/drop/delete/link-remove) deletes the blocking links it holds
      AND — because each link names its blocked ticket — explicitly
      rewrites each named ticket's status (empty/blocked) in the same
      transaction. No discovery step, no scan; atomic; the empty flip fires
      the ordinary combined signal so the loop starts the freed ticket.

8. NAMING MAP: employee/"Automatic Employee" → worker; discovery loop →
   readiness loop (owner lukewarm, revisitable); eligibility decision →
   readiness check; eligibility wake → readiness wake; EmployeeStepRunner →
   step runner; employee_step_runs → step runs (table itself dies per 7c);
   resolution engine → proposal resolver. Event log dies unrenamed; rebuilt
   conversation parts named at design time.

## Ratified wants (carried into the verdicts, kept for ticket-writing)

- Conversation (ratified 2026-07-24): one conversation copy, one way in,
  reconnect-as-fetch is the TYPICAL case; every failure carries its reason
  to the screen (no taxonomy — just carry it); a new worker has nothing to
  do with the old one other than killing it; conversation is ticket-ignorant,
  role set by caller at kickoff; empty-state new conversation must be
  visibly distinct from a broken blank screen; the link to a RUNNING CLI is
  bulletproof (losing track of a running CLI ~never happens); tmux = a
  copyable command under the pane's "new" dropdown to attach to the agent's
  CLI session; restart = blank new conversation (same action as new worker);
  UI look overall roughly fine — machinery is what's unreliable.
- Live page (the five wants): change from anywhere shows everywhere within
  a moment; only affected parts update; self-recovery after disconnect;
  screen always shows server truth; new state kinds need zero live-update
  bookkeeping.
- CLI (closed 2026-07-24): ONE real problem — accessibility. "Obviously the
  CLI should be accessible from anywhere." Mechanisms located: panels binary
  never injected into worker-child PATH (launch luck); PLAN_* env stripped
  so PLAN_SERVER_URL never reaches workers. Everything else surfaced (web
  can't mint tickets, ideas no CLI, thin day verbs) is as-meant.
- Queries (2026-07-24): Workspace buckets are a frontend hand-copy of the
  status enum (bucketFor, BoardRoute) — dies into the statuses reshape;
  segmentation principle applies to blended surfaces (spinner = status OR
  activity fused); sprint-surface N+1s parked as within-system work.
  Workspace itself cheapest view (5 flat queries).

## Still open / gated / parked

- GATE: final VPS operating-model ticket has NOT landed (one more to go;
  "Make VPS operation single-user" is on origin/staging but the program's
  talk-only gate stays ON per owner).
- After it lands, owner-deferred to a later session: deploy roll-forward
  (live generation predates cc03c505 default-to-today fix; skills-teaching
  half already fixed by owner) and TICKET-CUTTING against these verdicts.
  Known cutting-order constraint: statuses reshape deletes the event log's
  last logic reader, so it plausibly precedes the live-update swap.
- PARKED by owner: skills system (own system, works fine; repo copy under
  src/planner/skills/ not to be treated as canonical); backends' diary
  logs; connect-failures topic (raw evidence from the earlier premature dig
  retained in git history of this file / transcript — owner defines the
  symptom first if ever reopened); embeddings aside (solutions input,
  satisfied by sqlite-vec under the stack verdict); CLI accessibility
  ("don't worry about it", 2026-07-25 — dropped from the ticket-cut
  program; mechanisms stay documented under ratified wants if reopened).
- Open owner concern on record: "readiness loop" name is tolerated, not
  loved.

## Research artifacts (session scratchpad: /private/tmp/claude-501/-Users-khushaljagota-Coding-planning-v2/70f16019-8153-458b-875a-a69227b610d9/scratchpad/)

- acp-ecosystem-inventory.md — sweep A: ACP spec/session semantics, SDKs,
  AHP, hosts (Codeg/Mitto/Tidewave), adapters capability matrix.
- host-shapes-inventory.md — sweep B: opencode, OpenHands, Happy, Clay,
  CloudCLI, official SDK session APIs, protocol layer, the H1-2026 die-off.
- openhands-deep-dive.md — runtime-verified deep dive (separability,
  ACPAgent path, kickoff surface, storage, browser contract, governance).
- live-update-inventory.md — sync engines, query subscriptions, SQLite
  primitives, server-rendered patterns, want-5 cost table.
- minimal-baseline-report.md — coarse signal/version/ETag/sequence patterns,
  TanStack facts, practitioner testimony (Figma bracket).
- Plus: sdk/ (OpenHands clone), venv-as/, stub_acp.py, ws_probe.py,
  openapi.json, asrun/ (real persisted conversation) — OpenHands repro.

## Key code pointers (for ticket-writing)

- core/db.py (2109 lines, schema v37, hand ladder — Alembic replaces the
  ladder mechanism); core/events.py append_event (sole event writer — dies).
- tickets/contracts.py:30 TicketStatus; tickets/logic/machine.py
  resting_ticket_status; tickets/data.py (writer map; employee config
  freeze :500-521); tickets/views.py:387 review_view.
- runtime/: automatic_employee_step_discovery_loop.py,
  automatic_employee_step_eligibility.py (~11 conditions, shrinks to
  "empty, today, ceiling, closeout-lane"), employee_step_runner.py (674
  lines; provenance gate :658-671 — dies with step_runs),
  automatic_employee_step_eligibility_wake.py (becomes half of the combined
  signal).
- conversation/ ~18,400 lines, ~30 files (hub.py, employee_registry.py,
  turn_broker.py each >100KB) — replaced by the new host.
- web/src/routes/BoardRoute.svelte:138-153 bucketFor (dies into statuses).
- Conversation layer today: composer queue/send-now/steer + unbounded deque;
  permission card 300s server-owned timeout; those behaviors are the
  keep-the-experience reference for the rebuild.

## Gate detail (owner shared, 2026-07-25)

The final gating ticket (t_51pphnqv) is the live VPS cutover itself: one-time
root executor migrates the estate to the single vps user (~/Coding/Panels),
takes authority in a maintenance window, proves backups/ACP/reboot, merges
the rolling staging→main PR as the production deployment gate, proves real
deploy + rollback through the new runner, then cleans up the old root estate
from an enumerated manifest. Consequence for this program: its completion
DEPLOYS current staging — the cc03c505 placement fix reaches live as part of
the cutover, likely absorbing our "deploy roll-forward" step entirely.

## Gate REINTERPRETED — build begins locally (owner, 2026-07-25)

The cutover ticket touches no application source (it deploys accepted
revision 0bede332 and migrates the estate). Owner ruling: planning and
implementation may begin NOW, in isolated worktrees branched off local
staging. THE ONE HARD RULE until the cutover ticket completes: NOTHING
pushes to origin/staging — the cutover pins the staging→main PR head and
ABORTS ON DRIFT. Local branches/worktrees fine; Closeouts that would
advance origin/staging must wait.
Next step after compaction: draft the ticket-cut program plan — proposed
order: statuses reshape first (deletes the event log's last logic reader),
live-update swap second, conversation host build as the major program,
errors log / CLI accessibility / naming / Alembic as small independents;
parallelization decided by file overlap.
