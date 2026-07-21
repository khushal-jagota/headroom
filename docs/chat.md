# Conversation

Panels has one conversation system. The Chief of Staff, a Ticket's human discussion,
and that Ticket's Automatic Employee steps all use the same ACP session machinery.
There is no second Chat database, relay, neutral protocol, or history adapter.

## What the browser connects to

The Svelte pane opens one WebSocket at `/api/conversation` and attaches with an
employee id. A Ticket's employee id is its Ticket id. The Chief of Staff uses the
fixed Chief employee id.

The server is the ACP client. It uses the official ACP client library to start each
agent backend as a child process and speak ACP over standard input and output. The
browser never connects to an agent process directly.

The server answers with typed ACP state. The browser renders human and agent messages,
thought disclosures, tool calls, plans, terminal output, permissions, delivery
receipts, connection state, and compaction boundaries. It does not parse a second
Panels transcript format. Reloading or reconnecting attaches to the durable ACP
binding and rebuilds the pane from the backend's replay.

Replay is integrity-checked and held as a subscriber-local bootstrap. It does not
occupy the bounded queue used for live browser updates. A connected browser crossing
a session refresh or replacement receives one ordered replay cutover before later
live updates, so it cannot see a partial replacement transcript. Production keeps up
to 1,024 live envelopes per browser; exceeding that limit means the browser is
genuinely not consuming and closes only that subscription. Replay-unavailable and
slow-browser closures write content-free structured warnings with the employee,
session, generation, connection, counts, and configured limits. Replay bootstrap and
cutover envelopes do not count as queued live envelopes in those warnings.

Each envelope carries the employee, ACP session, binding generation, and sequence.
The browser accepts only one contiguous generation. A gap or identity mismatch fails
closed and forces a fresh attach instead of silently showing mixed conversation state.

The conversation composition has one owner for each responsibility:

- `ConversationHub` owns browser attachment, typed replay, and publication.
- `AcpEmployeeRegistry` owns the live ACP child for an employee.
- `ConversationTurnBroker` owns active delivery, Steer, Send Now, Queue, Stop, and
  compaction transitions.
- `ConversationPermissionBroker` owns each pending ACP permission request and its
  first valid answer.
- `SqliteConversationBindingRepository` owns durable employee-to-session bindings.

_Code paths:_ `src/planner/conversation/`, `web/src/lib/acp/`, and
`web/src/components/acp/`.

## Sending work

When the employee is idle, a prompt starts normally. While work is active, the pane
offers the choices the shared broker can honestly provide:

- **Steer** sends guidance into the active turn when the backend supports native
  steering.
- **Send Now** interrupts the active turn and starts this prompt next.
- **Queue** keeps prompts in server-owned FIFO order.
- **Stop** requests cancellation of the active turn.

Delivery receipts say whether a prompt was accepted, queued, started, interrupted,
or rejected. The UI does not invent completion from a timeout. Backend failure is
shown as failure; a slow operation may remain slow.

Hermes supports native Steer. Codex and Claude Code do not, so their active-turn
choices are Send Now or Queue instead. The broker and browser use the same delivery
states for all three backends. The pinned Claude adapter can keep publishing old
provider-side background work after a terminal requested-cancel response, so Stop and
Send Now retire its exact child and privately load the unchanged durable session into
a fresh child before more work starts. Hermes and Codex reuse their child after a
normal terminal cancellation response.

Commands come from ACP `available_commands_update`. The composer offers those commands
directly, without a second Panels command catalogue. Picker, paste, and drop image
intake all create ordered ACP image content blocks. Images are sent inline to the
agent; Panels does not upload them to a managed conversation-file tree.

Tool requests use the ACP reverse services owned by the conversation composition.
Permission choices are tied to the exact employee, session, generation, and active
turn. A stale browser or stale worker cannot answer a later permission request.

## Durable session identity

`conversation_session_bindings` is the durable ACP owner. It stores the employee,
entity, backend, ACP session id, binding generation, and compacted-boundary provenance.
A Ticket mirrors the same ACP session id in `tickets.employee_session_id` because
Ticket correctness and worker lookup need it. The Chief has no second mirror.

Creating or replacing a binding is compare-and-swap work. A Ticket binding and its
mirror change in one transaction, and one ACP session cannot belong to two employees.
After a server restart, the first demand loads the stored binding and replay. Starting
a new conversation deliberately creates a new ACP session and advances the generation.

The production backend catalog contains exactly `hermes`, `codex`, and `claude`.
Each Worker type supplies a starting Worker, Model, and Reasoning effort; a new Ticket
copies that trio once and then owns it. During pristine Kickoff, those controls appear
only inside the Kickoff approval area. Hermes offers Model and no Reasoning. Codex and
Claude Code offer Model plus the Reasoning choices supported by that model. The first
durable binding, or moving beyond Kickoff, freezes the Ticket's launch setup and removes
the controls. Gemini is not registered.

For the first unbound session, Panels applies an explicit Model before an explicit
Reasoning choice, then writes the binding only if the Ticket still owns the setup used
to prepare that session. The stored model and reasoning remain after binding as the
historical Kickoff request, not the worker's current settings. A bound load, child
replacement, compaction recovery, or later New Conversation never reapplies or presents
them as current. Human prompts and Automatic Employee steps still share the selected
backend and durable binding.

Claude Code runs one initialize-only preflight when Panels starts. That temporary
child is closed before startup completes and creates no worker session. Codex is lazy:
its child starts only on first demand. Actual Ticket and Chief sessions for every
backend still start or resume through the same registry and binding machinery.

## Employee role skills

Panels keeps one canonical copy of its skills under `skills/`. The repository exposes
that directory at the backends' native project locations: `.agents/skills` for Codex
and `.claude/skills` for Claude Code. Hermes startup exposes the same source directories
under the configured planner Hermes home. These are links, not copied skill files, so
every installed Panels skill comes from the same source.

A newly created ACP conversation adds its employee role to the first real prompt sent
through that session. A Ticket adds `panels-worker`; the Chief adds
`panels-chief-of-staff`. The first prompt may come from the browser or Automatic
Employee work. Text-only slash commands pass through unchanged and leave the role ready for the
next ordinary prompt. Later ordinary prompts and a loaded, forked, or replacement child
do not add the role again. Starting New conversation successfully creates a new session
and therefore adds the role to that conversation's first ordinary prompt.

The role line is ACP delivery context, and it is visible in the transcript as a
\`System message · role\` entry. Panels does not remove or normalize role echoes from
live updates or replay. A genuine message that happens to equal the role line remains
visible too. Claude receives the same ordinary ACP prompt behavior as the other
backends; Panels does not modify Claude's system prompt.

Automatic Employee prompts are also visible immediately as \`System message · worker\`.
The broker publishes the exact prompt admitted to ACP, including any role prefix, at
prompt start. The browser keeps that typed event in its ordered timeline, so it does
not require a hard refresh to reveal a programmatic prompt. ACP replay remains the
source for rebuilding the conversation after reload.

## Human and Automatic Employee work share the session

A human prompt in the Ticket pane and an Automatic Employee step reach the same
durable ACP session. The automatic path is not a hidden transcript writer. It uses
`AcpStepGateway`, which submits the real worker prompt through the same hub and broker.

Pending worker context is prepared into that real model prompt before delivery. Its
exact revisions are acknowledged only after ACP admits the prompt. If admission fails,
the pending context stays pending for the next legitimate attempt. Writing an event or
correctness row is never treated as delivery to the model.

`employee_step_runs` records only execution ownership and settlement. It contains the
step id, Ticket id, status, employee session id, error, and timestamps. It contains no
prompt, reply, transcript, usage, activity, tool data, image, or browser state.

## Compaction

Compaction has a deliberately small Panels surface. The pane shows one content-free
started boundary and then one content-free completed boundary or exact failure. It
never displays the backend's compacted context or asks the user to inspect a summary.

Compaction may be automatic or requested through `/compact`. Panels waits up to 300
seconds to observe its terminal result. It preserves the typed boundary in replay and
keeps all compacted context private. Reload and restart return to the resulting durable
session. A genuine protocol, backend, or observation-budget failure remains visible
with its exact reason.

## Files and previews

Conversation images are inline ACP content, not stored files. Durable Ticket artifacts
remain under the Ticket file tree and use `/files/tickets/...`. Tool locations and
message links can still use the shared generic/Ticket preview component. Markdown,
HTML, image, audio, video, download, and external-link safety remains owned by the
shared file-preview code.

There is no Day conversation, conversation-file route, or Employee-session-history
HTTP route. The ACP pane and backend replay are the one conversation view.

## One-way database cutover

Schema version 25 performs the one-time removal of the former Chat/session tables and
Day conversation field. It preserves only worker-step correctness history in
`employee_step_runs`, interrupts a row that was running during migration, clears old
session bindings and Ticket mirrors, and starts later conversation demand fresh.
Fresh databases contain only the ACP-era schema. This migration is not a compatibility
mode and no runtime code reads the removed tables.

## Handoffs

- **The employee runtime** (`employee-runtime.md`) owns discovery, execution, and
  Employee-step settlement.
- **Tickets & the gates** (`tickets-and-gates.md`) owns Stage, scope, proposals, and
  the Ticket session mirror.
- **The front end** (`frontend.md`) owns the ACP pane and shared preview rendering.

---

_Last verified: 2026-07-21 (single ACP conversation with three production backends)._
