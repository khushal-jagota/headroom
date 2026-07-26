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
- acp-patchbay-findings.md — owner-pointed repo check (2026-07-25): young
  solo VS Code ACP client, not adoptable (inverted shape, persistence-free
  by design) but high-value reference for the host build — wire-verified
  claude/codex bridge dossiers (replay lossy+shape-shifted; session ids
  recycled → our conversation id is identity, ACP sessionId rebindable;
  one-process-per-conversation safe floor; -32000 auth convention),
  liftable recipes (process-tree PID-reuse-guarded orphan reap, stop
  ladder, stderr ring buffer, npx warmup/poisoned-cache repair, replay-
  window discipline, lying-fake-agent test harness), Apache-2.0, clone
  kept at scratchpad/acp-patchbay.
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

## Program pre-start rulings — STATUS 2026-07-25: seam decisions VOID.
Owner killed the seam orchestrator: the contract was proposed without
grounding (roles/permissions at session start never traced) and decided in
isolation. The seam contract below is a DEAD PROPOSAL kept for reference
only; the real decision gets made jointly after the role-tracing agent
reports. Alembic orchestrator still alive, HELD at plan gate — its
ladder-deletion call also awaits joint ratification. Statuses mapping
proposal likewise unratified. Seam worktree
(planning-v2-worktrees/conversation-seam) left in place for the re-dispatch.

## Dead proposal (reference only — do not implement)

- CONTRACT (seam package, settled): new package src/planner/conversation_host/
  holding contract.py (types + Protocols) and the shim wrapping today's
  machinery. Surfaces, sync, callable off the event-loop thread:
  start(kickoff) -> conversation ref — kickoff carries OUR conversation id
  (identity; ACP session id is a host-internal rebindable attribute, per
  patchbay), backend key, role/worker kind, workspace; shim era: shim
  self-resolves as today and VERIFIES caller-supplied kickoff matches (no
  hub rewiring, honest fields). send(conversation_id, text, on_admitted) ->
  turn outcome — blocking through settle (today's semantics); busy is a
  stated fact; on_admitted fires after ACP admission (worker-context prep +
  acknowledge MOVE caller-side; gateway drops WorkerContextService).
  is_free(conversation_id) -> bool — occupancy fact, future pre-send check.
  Facts/transcript feed — types declared now (fetch-after-N + snapshot),
  implemented by the real host; browser stays on old wire until swap;
  runner-visible facts today = turn outcome + host availability.
  interrupt(conversation_id) — NOT in the ruled four; carried because
  today's cancel path requires it for behavior preservation.
  Permission-settlement guard stays conversation-internal (broker wiring,
  not a runner surface); dies later with step_runs.
  Contract file is owned by the program orchestrator; packages propose
  changes, approved serially.
- Import inventory: 11 files outside conversation/ import its internals
  (core/db, core/server, environments x3, runtime gateway, tickets/api,
  tickets/data, worker_settings/api, worker_types x2). Seam repoints the
  runner path fully; the rest get classified dispositions (dies at swap /
  moves at swap / legitimate composition) + a guard against NEW internal
  imports on the runner path.
- ALEMBIC — OWNER-RATIFIED 2026-07-25 ("delete them"): baseline = current
  schema v37 as the initial Alembic revision; existing v37 DBs stamped;
  the hand ladder and all pre-v37 steps DELETED (a pre-v37 snapshot
  upgrades via an old checkout — git keeps the ladder forever).
- STATUSES mapping — OWNER-RATIFIED 2026-07-25 ("proposal discussion can
  become paired correct" + "all good"): proposal_discussion rows migrate to
  `paired` (discussion IS paired work; proposal persists — matches
  chat-reply-flip verdict). Other renames map one-to-one. Also ratified same
  breath: package scope (see Package status) and the blocked-derivation
  migration rule (empty tickets with a live blocker become `blocked`, same
  links rule as today's is_blocked).
- OWNER CALLS OUTSTANDING: (a) transcript storage — RULED 2026-07-25,
  same Panels database (see RULED boundary section); (b) old conversations
  at swap — CLOSED 2026-07-25 by owner decision rule ("if migrating old is
  cheap we do it, if it proves too difficult we don't"): CLEAN CUT. Grounds:
  Panels durably stores NO transcript text anywhere (bindings/metadata/
  projection flags only; browser replay is the in-memory projection) — the
  only reconstruction path is per-backend ACP session/load replay, which is
  lossy by design (patchbay finding: context restoration, not faithful
  history) and needs each old session spawned through the old machinery.
  That is a build, not a copy → too difficult by the rule. Backends keep
  their own session files, so old history survives in their storage; the
  new pane simply starts fresh at swap.

## Conversation start request — owner wants (2026-07-25, during seam grounding)

VOCABULARY (owner correction): do NOT say "kickoff" (collides with the
ticket kickoff stage) — it is the START REQUEST, handed over when a new
conversation's first message is sent (the moment the agent process is
created). Do NOT say "host" — it is the conversation system. Stop coining
words; use existing system names or plain descriptions.

OWNER SAID (exactly this, nothing more):
- Backend/model defaults are application settings, settable in the UI,
  and cover standalone agents (chief of staff) the same way as workers.
  Never hardcoded ("that's stupid") — kills the unreachable hermes
  constructor default and the chief's code fallback.
- Caller sets backend/model etc., or at minimum can override the default:
  on any NEW conversation the owner can change model etc. up until the
  first message is sent. What is changeable afterwards: discuss later.
- Ownership (who owns what) NOT settled — open discussion. Context the
  owner gave for shaping it: more agents and more workers are coming;
  agents may start in their own project instead of all in ~/Coding.

RESOLVED — see "RULED: start request + ownership boundary" below.

## RULED: start request + ownership boundary (owner, 2026-07-25)

- The conversation system takes finished, concrete values only. It keeps
  its own floor defaults so an empty start request still runs (codex,
  ~/Coding, full access). Permission modes are fixed constants — the only
  value there — not settings. Everything else configurable is an
  application setting, UI-settable, workers and agents (chief) alike.
- RESOLUTION = TWO FUNCTIONS, Panels-side ("two functions is fine"):
  worker resolve(ticket, overrides) — worker type defaults → ticket's
  last-chosen columns (see below) → overrides; agent resolve(overrides) —
  agent defaults → overrides. Both return the completed start request
  (backend, model, role materials, folder). They compute values only —
  no creating, no sending.
- START ACTION (first message of a new conversation): creates the
  conversation row, writes the ticket's conversation link AND the ticket's
  last-chosen backend/model columns — one transaction (same database).
- CONVERSATION SYSTEM RECORDS (the "how does it know from just the id"
  mechanism): a `conversations` table (conversation id, backend, model,
  folder, role materials, ACP session id — a rebindable attribute written
  when the backend mints it, latest sequence) and an `events` table
  (conversation_id, sequence, one JSON event per row; fetch-after-N is one
  indexed query). Record write is STEP ONE of creation — no path creates a
  conversation without it. Live process table in memory keyed by
  conversation id; recovery = record minus table (spawn backend, load
  session, forward). RULED: these tables live IN THE SAME PANELS DATABASE.
  "Able to exist as its own product" means clean code boundary (own
  tables, own module, one contract) — NOT physically separate data.
- NEW CONVERSATION: pressing New kills the worker AND clears the ticket's
  link, immediately and durably — tab away/return shows blank new state,
  never the old worker. Values changeable until the first message; the
  first message creates the conversation and writes the new link.
- SAME-AGENT-BY-DEFAULT: KEPT. It cannot be derived after New (link is
  gone), so the ticket keeps backend/model columns meaning "what the last
  conversation STARTED with" — written once in the start transaction,
  read as a default layer at the next new, never touched otherwise.
  Explicitly NOT a live mirror of conversation state; redefining them to
  track current state would recreate the sync/guard disease.
## RULED: send (owner, 2026-07-25)

- ONE operation: send(text, mode). Mode is a single param, three values,
  default RUN-WHEN-FREE (idle starts; busy holds, runs when free) — the
  loop and the idle composer pass nothing; the mid-turn "queue"-labeled
  button is also just the default. SEND NOW (mode value): make this the
  running turn — idle same as default; busy kills the incumbent
  (interruption recorded as an event) and the new message runs NEXT,
  ahead of existing queue (today's jump-ahead preserved). STEER (mode
  value): inject into the running turn — capability-stated per backend
  (hermes only today), returns its real fate (injected), never a
  pre-outcome "accepted". Send now and steer surface as UI options only
  while a turn is in progress.
- INTERRUPT: its own crossing (stop without sending).
- RETURN = FATE OF THE DELIVERY, and fate means it happened. Precise
  layer: delivered TO THE AGENT BACKEND — request written to the wire of
  a live process under a valid bound session (ACP offers no acceptance
  ack; the prompt response only arrives at turn end, so "accepted by the
  backend" is unknowable synchronously). Fates: started / queued at N /
  injected / refused with named reason. Refusals ONLY for genuine
  delivery impossibility (no conversation, spawn/load/write failure) —
  NEVER for busyness; nothing is refused that the system can hold.
  QUEUED is explicitly a conversation-system fact (not yet at a backend);
  a dequeued item's later delivery fate is recorded as events.
- Turn endings are NEVER return values — recorded events only; the
  conversation system writes its own error-log line on failed turns.
  Loop consequence (its design, not the contract's): loop flips status
  and sends; started and queued are both success ("slipped collisions
  queue"); refusal → flip back. Runner repoint + optimistic-start rework
  = one piece of work.
- SENDER LABEL: kept — one display-only field on the prompt event (loop
  vs owner), no other consumer.
- Queue internals (cap, restart durability) = conversation-system
  build-time details, flagged at build.

- PERMISSIONS (ruled 2026-07-25): a permission ask ALWAYS SHOWS AND
  WAITS — instant auto-deny when nobody's watching is dead ("built for no
  reason without my knowledge"). Ask + answer are recorded events;
  answer lands only on a still-pending request of the live turn —
  conversation-internal bookkeeping, looks at nothing outside itself;
  the ticket-state cross-check guard dies with the mirrors. The ask must
  be VISIBLY SURFACED — ticket status to needs_user "or something", at
  minimum an attention indicator; exact mechanism (who flips ticket
  status, given conversation ticket-ignorance and no watch-machinery) is
  decided in the orchestration/build design. Owner caveat: with all
  agents on full-access modes asks are rare — behavior matters for the
  rare real one.
- READING SIDE (ruled 2026-07-25): collapses to the ONE existing
  crossing — "is this agent running right now" (loop pre-send check,
  workspace spinner; same query). Backend/model are TICKET properties
  (kept columns from the same-agent ruling) — no facts feed needed for
  them. "How the last turn ended" has no live-query consumer (transcript
  + error log cover it) — not a contract read. Transcript read
  (fetch-after-N + tail, event shapes) is typed AT THE BUILD against the
  real events table and the rebuilt pane — no parallel package needs it
  sooner (statuses, live-update, errors all checked: none touch it).

Still open in the seam queue: contract package location/name, permission
guard placement.

RULED 2026-07-25 (owner: "for now, yeah, Python I'm fine with", after
the T3 tracer facts): the conversation build is PYTHON with NATIVE
front doors per backend — claude via the official Python Agent SDK (no
ACP bridge; revises the all-ACP ruling), codex via its native app-server
JSON-RPC with bindings code-generated from the pinned schema (as T3
does), hermes via the Python ACP SDK (its native protocol). T3's code is
NOT lifted (Effect-framework entanglement at the protocol seam; adapter
bulk normalizes for needs Panels doesn't have; no stable upstream to
diff against); T3's KNOWLEDGE is lifted — quirk catalog + recipes live
in scratchpad/t3-code-findings.md with file:line refs (owner: "happy to
copy evidence"). Second tracer pass DISPATCHED for a steal catalog of
T3's agent-driving product decisions (scope narrowed by owner: working
with backend agents only, not general product furniture) — named
priorities: mid-session model/reasoning selection mechanics, clickable
access/permission modes, provider snapshot (binary/auth/models) —
findings to scratchpad/t3-adoptable-decisions.md; owner and Claude walk
the catalog together and pick adoptions.
CONVERSATION BUILD WALK — going through everything one item at a time;
an item is settled only when the owner says so ("It's settled when I say
it's settled"). Rulings so far (2026-07-25):
- DEFAULTS are minimal: only what the picker starts on, or the applied
  value where there is no picker. Pre-start only. Not a system, no
  cross-system role ("defaults are very minimal things… nothing more").
- Once a conversation is running, there is no default: the value "is
  just what it is currently" — the picker on a live conversation shows
  the conversation's current backend/model/effort. Whatever a
  conversation is on, it continues on ("whatever a conversation is on
  continues"); the loop's sends continue the existing conversation
  as-is.
- Manual mid-session change: allowed ("like they do"); T3's model RULED
  fine — picking alone does nothing, the change rides the next send
  (claude realized as restart+rebind under the same conversation id).
- New/picker-start RULED: press New → default comes from the ticket if
  there is one to get it from, else from the worker/agent defaults —
  "whatever our things were, and importantly, that should be kept up to
  date": the ticket columns FOLLOW mid-session switches (updated when a
  switch-carrying send commits; the Panels-side caller writes them on a
  successful fate — conversation stays ticket-ignorant). This supersedes
  the earlier "written once at start" phrasing.
- SNAPSHOT RULED: per backend — binary present + version, logged-in
  identity where the CLI exposes one (subprocess probe, no API cost),
  available models (feeds the picker), UPDATE CHECKING and ONE-CLICK
  UPDATE pulled back from the skip pile by owner ("I like it a lot"):
  update advisory + update action with the command inferred from the
  install method (T3 recipe: detect npm/homebrew/native → matching
  update command → re-probe → report succeeded/unchanged/failed
  distinctly). Advisory everywhere, never blocking. No hermes
  special-casing.
- ASK FLOW RULED: adopt T3's four buttons for now (Cancel turn /
  Decline / Always allow this session / Approve once), escalating-
  commitment order. Mechanics verified in T3's code first: the
  conversation layer REMEMBERS NOTHING — the card renders the options
  the backend's ask carries (claude: SDK-suggested permission update,
  honored inside the claude CLI; codex: answer enum passes through
  byte-identical; ACP: the ask's own option ids) plus our Cancel turn;
  the answer goes back to the vendor, who does all remembering
  (grants die with the vendor session — why they vanish on restart).
  Our system records ask + answer as events, holds zero grant state.
- EVENT RECORD: notebook shape well-received ("sounds good") — one
  events table per-conversation sequence/kind/JSON/timestamp, immutable,
  fetch-after-N + live tail, message-level granularity, streaming
  ephemeral. TOOL CALLS + THINKING RULED (owner: "I'm fine on their way
  for now… we just adopt their system, ours sucks, better to just
  adopt"): tool calls recorded T3's way — full lifecycle (start +
  finish lines; display may fold later, pane design); thinking DROPPED
  at ingestion like T3 — not stored, not shown, for now. Claude's
  live-only middle option REJECTED by owner: "live is a very unlikely
  occurrence given ticket switching" (reconnect-as-fetch is the typical
  case), so live-only thinking serves a case that barely happens.
  Irreversibility flagged before ruling: dropped thinking is not
  recoverable for past turns if revisited later.
- PROCESS MODEL RULED ("their process"): T3's idle janitor adopted —
  idle agent processes stopped after an idle window, silently and
  lazily resumed on the next send (resume-on-send is already our normal
  path; named refusals cover its failures). Everything else as already
  ruled: one child per conversation, lazy spawn, vendor cursor stored
  on the conversation row (rebindable), no re-attach at boot, nothing
  shows running after a restart (is_running is process-backed), failed
  resume surfaced, asks die with their turn. Contrast kept from T3's
  gaps: our failure paths are named where theirs are silent.
WALK COMPLETE (all topics been through: selection, snapshot, ask flow,
event record incl. tool-calls/thinking, process model). Remaining
build-time verify: hermes per-turn switch mechanics.
RESERVED DISCUSSIONS CLOSED (owner, 2026-07-25):
- DOGFOODING: browser dogfooding is the required gate — hold real
  conversations through the new pane on a dev route, fix what it
  reveals ("we do need to dogfood in the browser and fix"); the
  mechanical tiers (scripted-agent conformance subject, real-CLI
  exercises) ride on trusting T3-proven event handling. DESIGN
  CONTINUITY constraint: the new pane RETAINS THE ROUGH LOOK of
  today's conversation UI — intelligent adapting of the existing
  design, holistic redesign explicitly not the intention. BUILD
  STRUCTURE: shared core first, then the build orchestrator may spawn
  THREE CHILD ORCHESTRATORS (one per backend) for adapter parallelism.
- NEEDS-ME DOT: computed on read (pending ask = ask line with no
  answer line in the live turn; freshness rides the live-update
  signal; no watch machinery, no status writer). Appearance: PURE
  WHITE dot, for now. Three row signals: working / reply waiting /
  needs-me.
- SEAM EXTENSION LANDED on staging (caa6f6f5): send carries optional
  model_change / reasoning_effort_change (commit-on-send, ruled
  semantics), fake + 5 conformance tests + README updated; model and
  effort now conformance-observable from the backend side. uv.lock
  leftover deleted (owner: "figure it out").
BUILD-PACKAGE ADDITIONS (owner, 2026-07-25): the build orchestrator's
job includes TESTING AND DOGFOODING — real conversations against real
backends (local hermes at minimum), not just conformance/unit suites;
dogfooding shape to be discussed with owner BEFORE the dispatch is
written. ASK-SURFACING DIRECTION (owner): belongs to the same surface
that says whether an agent turn is active (today's workspace signals);
that surface also learns of waiting asks and shows a DISTINCT DOT KIND
for "ticket needs me / permission waiting" — discussed before the
owning package (orchestration rework) sets off.

T3 ADOPTION SLATE — owner-ruled 2026-07-25 (catalog:
scratchpad/t3-adoptable-decisions.md, 72 entries; walk done in-session):
- ADOPTED, owner-named: permission ask TAKES OVER the composer (input
  disabled, ask becomes placeholder; escalating-commitment buttons);
  backend snapshot as a FIRST-CLASS OBJECT (installed/version/logged-in
  identity/models — advisory, never blocking; login out-of-band naming
  the terminal command; errors as diagnoses with fixes).
- ADOPTED from the earlier want ("able to change models within a
  session"): mid-session model/effort switching — per-turn param where
  the backend allows (codex, hermes), kill-and-rebind under the same
  conversation id where not (claude); requires a signed seam extension
  at build time (send-time override or set operation — owner sign-off
  when the build is specced). Plus commit-on-send semantics (choice
  lands with the message, browsing mutates nothing).
- DESIGN-AGAINST (from T3's lifecycle section, validates our rulings):
  two named build obligations — (1) pending asks die VISIBLY with their
  turn, never replayed as live; (2) a resume that didn't actually
  restore the agent's memory is detected and surfaced, never silently
  accepted (codex silent-fresh-thread trap). Also: always render an
  unknown-typed ask (no card = agent blocks forever).
- CORRECTION (owner): invisible transformation is NOT a banned pattern
  ("I'm not against it") — my anti-pattern framing of T3's silent
  effort remapping was wrong; no standing rule against it.
- DEFERRED to pane design, owner: in-thread interaction choices
  (reasoning visibility, tool-call folding, compaction display) —
  "don't mind that for now."
- SKIPPED (unobjected): multi-account machinery, four-mode access
  surface, utility-model generations, browser handover, model curation.
- Owner: "a lot of good stuff here we should be taking account of" —
  the catalog is standing design input for the conversation build spec.
History of the reopening, kept for the record: the wire-layer choice
for the real conversation build — official Python ACP SDK for ALL three
backends (earlier ruling) vs per-backend native protocols (T3 Code's
production-proven choice: claude via official Agent SDK, codex via native
app-server, ACP only for ACP-native agents; our hermes is ACP-native).
Owner: contract package unaffected — this lands behind the ruled seam.
Tracer dispatched over the T3 Code repo (facts only, patchbay-style);
joint decision when it reports. Findings will land at scratchpad/
t3-code-findings.md.
Owner widened it same day to the conversation build's LANGUAGE/HOME.
Adopting T3 directly is OFF the table (owner). Two live options:
(1) Python rebuild that deep-lifts T3's design — go through everything,
pick what we love, port it; (2) our own TS conversation system carrying
T3's actual driver code (open source; licensing waved off by owner —
personal use). Shared cost either way: fully understanding their code.
Named hinge facts for the tracer: (a) driver extractability from the
Effect framework (if extraction = rewrite, the options converge and the
steal advantage shrinks to upstream diffability); (b) TS sidecar cost to
the ruled one-transaction start action — two processes can't share a
transaction; thin-adapter shape (TS speaks protocols, Python writes all
rows) preserves the ruling but shrinks the steal surface to drivers only.
Known fact standing either way: Claude Agent SDK exists officially in
Python; codex app-server is language-agnostic JSON-RPC; hermes is
ACP-native via the Python ACP SDK.
Want rulings (owner, 2026-07-25): (1) vendor fidelity — yes, DAY-ONE
MODELS are the part that really matters; big new features wanted, a notch
less urgent (kills bridges, doesn't pick a language). (2) LEVERAGE over
ownership for now — code is understandable, rebuilding anyway, reshape
later stays open; T3's decisions ADOPTABLE over our downstream rulings
where theirs are better (seam contract itself stays ruled); "they've
gone what we want and further" in ~10x the time we spent. (3) one
process preferred, softly — tiebreaker, not veto. Net: extract maximum
value from T3 either way; open question is the vehicle, hinging on
driver extractability (clean lift → thin TS sidecar serious; extraction
= rewrite → Python deep-lift wins on leverage AND one-process).

## Package status (2026-07-25)

- CONTRACT PACKAGE: COMPLETE and MERGED into local staging 2026-07-25 on
  owner go (merge commit dab3d026; package commit 0d742a43 off db4e17f8;
  12 files, 2,414 lines, pure additions). src/planner/conversation/
  contract module + in-memory fake + 42-test conformance suite (backend-
  side proof, mutation-tested) + README naming the three obligations
  conformance can't check (record-first, error-log line, start values
  honoured). Spot-check PASSED: ruled properties structural (no fate
  field can carry a turn outcome; no busy-shaped refusal member).
  Owner ruled 2026-07-25: one-member ConversationAccess enum KEPT;
  integration approved. Orchestrator's flagged "pre-existing failures"
  (codex config + frontend mapping tests) dissolve on current staging —
  both pass at 86101e50+; they were worktree-environment artifacts.
  Package gates re-run green on the merged tree (52 tests, mypy 168
  files, ruff). Nothing pushed (cutover rule). Seam worktree/branch
  still in place pending cleanup. Package is upstream of the T3
  language/vehicle fork by construction (no wire code, no schema, no
  transcript read; a TS sidecar's thin Python side would implement the
  same Protocol and run the same suite).
- ALEMBIC: spot-check PASSED; MERGED into local staging 2026-07-25 on
  owner go — fast-forward db4e17f8→86101e50, deps installed into the
  main checkout venv, tests/unit/test_db.py green on staging (23 tests).
  Nothing pushed (cutover rule). Alembic worktree/branch still in place
  pending cleanup.
- LIVE-UPDATE PACKAGE: DISPATCHED 2026-07-25 to a Fable orchestrator in
  worktree planning-v2-worktrees/live-update, branch simplify/live-update
  off eeda828c (unblocked by the statuses merge deleting the event log's
  last logic reader). Scope per the ruled design: one commit-time
  contentless signal from the single write door fanned to browser SSE +
  readiness wake (explicit commit→wake call sites die); auto-reconnecting
  SSE; TanStack Svelte Query cache keyed like today's resources,
  invalidate-on-signal, focus/reconnect refetch; DELETE events table
  (second Alembic migration), append_event writers, envelope push,
  event-kind→resource mapping + completeness test, events API; convert
  the two remaining history readers (review waiting_since → stored
  timestamp; CLI events printout → stored facts or retired,
  conservative). Acceptance: new Playwright e2e — refetch never steals
  focus/wipes composition/moves scroll — run as a TARGETED package gate
  (only e2e exception to the reserved ./verify). Conversations wholly
  outside; conversation/ and conversation/ untouched.
- STATUSES PACKAGE: COMPLETE and MERGED into local staging 2026-07-25 on
  owner go (merge eeda828c; 4 commits, ~70 files). Spot-check PASSED
  (migration: two-rebuild widen→map→derive→narrow, full table declared
  both times, nullable-PK subtlety preserved, honest no-downgrade;
  blocked writer: single canonical transition, change-only writes,
  same-transaction release). Two codex reviews (plan: 3 blockers fixed +
  1 refuted with grounds; diff: zero blockers, 2 minors fixed). Gates on
  merged tree: 1,451 unit tests, ruff, mypy 168 files, web build — all
  green. Semantic calls recorded (owner aware pre-merge): paired now
  config-editable (proposal_discussion's image); paired NOT preserved
  through take_over (paired_work's image). Carry-forwards: e2e remapped
  blind (program-end ./verify is the backstop; riskiest:
  test_board_stage_indicators group order); tickets id column is a
  nullable TEXT PRIMARY KEY — future rebuilds must declare
  nullable=True; script.py.mako's "seven CHECKs" comment is stale (five
  — fix in passing); live loop now decides from status alone (intended,
  ahead of swap). Worktree/branch pending cleanup. Originally DISPATCHED
  2026-07-25 to a Fable orchestrator (owner call: "fable orchestrator")
  in worktree
  planning-v2-worktrees/statuses-reshape, branch simplify/statuses-reshape
  off local staging 86101e50. Ratified scope IN: enum reshape (8 final
  values; renames agent/paired/user; proposal_discussion removed; blocked
  added), first real Alembic migration (tickets rebuild per script.py.mako
  hazards; renames one-to-one; proposal_discussion→paired; empty-with-live-
  blocker→blocked derivation), whole-tree consumer sweep (src/ + web/, no
  named list; bucketFor dies; Review = pure awaiting_approval filter;
  chat-reply writer flips to paired), blocked mechanics (7d: completing
  writer rewrites named tickets' statuses in-transaction), eligibility
  simplification (paired marker scan deleted = event log's last logic
  reader; is_blocked dropped; gate = "empty and not user-owned"). OUT:
  optimistic-start rework (goes with runner repoint), step_runs/
  watch-and-settle death (goes with swap), needs_user surfacing mechanism,
  anything in conversation/ or conversation/. Stated consequence owner
  accepted: live loop decides from status alone as soon as this merges,
  ahead of the conversation swap.

- ALEMBIC BASELINE: COMPLETE on branch simplify/alembic-baseline
  (commits fda69910, 86101e50 — local only, nothing pushed). db.py
  2,110→227 lines; schema frozen as baseline revision v37 under
  core/migrations/; ladder + ~3,700 lines of ladder tests deleted; net
  -5,059 lines. Gates green: schema-identity diff (24 objects, 0 diffs),
  real pre-change v37 DB adopted + server boot, 1386 unit tests, ruff,
  mypy, packaging check. Two codex gpt-5.6-sol reviews (12 plan findings,
  6 diff findings — the worst: worked example dropped 3 FKs silently;
  fixed + reproduced first). NOT yet integrated into local staging —
  pending my spot-check of the migration/adoption code. Statuses package
  unblocks after integration.
  Process wrinkle (honest): the plan-gate hold never actually held it —
  its acknowledgments were written as plain text, never delivered, and
  the package ran to completion; the later ratification happened to match
  its brief exactly, so nothing needed redoing. Luck, not process.
  Carry-forwards it surfaced: (1) pre-existing race in connect() — WAL
  pragma can return "database is locked" when several processes open the
  same brand-new file; (2) deleting the execution-route cleanup removed
  the only filter for legacy event rows in the events API — now an
  assumption about the live DB, not enforced (moot if events die in the
  live-update swap, but true until then).
- SQLite migration hazards recorded for the statuses package (from the
  Alembic report, apply regardless of design): reflection-based batch
  rebuild drops CHECK constraints; copy_from drops undeclared indexes/FKs
  (incl. unique alias index); rebuild with FK enforcement on fires ON
  DELETE CASCADE silently — migrations run enforcement-off + PRAGMA
  foreign_key_check after.

## Build process (owner rulings, 2026-07-25)

- Worktree orchestrators: Fable-level orchestration driving Opus
  implementation agents per worktree; one agent first before parallelising
  is acceptable; planning loops within each orchestrator.
- Ticket granularity is the orchestrator's judgment — a package need not
  be one ticket per orchestrator; do NOT over-break work down.
- Every orchestrator is briefed on the program intent: simplification.
- Plans are reviewed by codex 5.6 sol; orchestrators route, not implement.
- ./verify runs ONCE at the end of the program, not per ticket; packages
  use named focused gates in between (the AGENTS.md reserved-final-verify
  clause).
- Success conditions include testing; fake databases and local hermes make
  real end-to-end tests feasible per package.

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
