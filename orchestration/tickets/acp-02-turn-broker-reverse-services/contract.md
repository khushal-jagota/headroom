# ACP-02 — turn broker, compaction, permission, and reverse services

Contract-scoped server-domain ticket for `orchestration/acp-migration/plan.md`. ACP-00/00a freeze the
typed values and browser wire; ACP-01 freezes child, binding, and ordered-ingress ownership. Read the
owner brief, research, reviewed program, those ticket contracts/reviews, `PRINCIPLES.md`, and
`AGENTS.md` before planning. ACP-01 and ACP-00a must be integrated before implementation begins.

This ticket adds uncomposed conversation-domain services. It does not add an HTTP/websocket route,
database migration, product runtime composition, Svelte code, or legacy cutover.

## Outcome

Panels owns active ACP prompt delivery, a visible FIFO, Send Now interruption, capability-gated
Steer, explicit and automatic compaction, transient ACP permission settlement, confined reverse
filesystem access, and scoped reverse terminals. Every state change reaches one injected typed event
publisher; no Chat row or event-log row is treated as agent delivery. Generic services contain no
backend-name branch. A Hermes strategy supplies only its real `/steer` and provenance/summary rules.

## Event and child ports

Implement purpose-named modules under `src/planner/conversation/`. ACP-02 consumes the settled ACP-01
registry/child API through a small typed port; it does not duplicate child ownership or bind directly
to a concrete SDK connection. It defines one injected `ConversationRuntimeEventPublisher` protocol
with named methods for the already-frozen activity, delivery receipt, queue snapshot, compaction,
permission request/outcome, and terminal-state payloads. ACP-04 will allocate envelope sequence and
broadcast. Do not introduce a generic event `data` object, a second transcript vocabulary, or route
knowledge.

The publisher owns `ConversationActivity.sequence`, permission `opened_sequence` /
`settled_sequence`, and the outer envelope sequence. Services pass exact typed inputs and await each
publication before the next externally visible transition. Tests use a recording publisher and
assert the complete order.

## Turn broker ownership and exact state machine

One async broker record exists per employee + durable binding generation. It is the single writer of
the active prompt, cancellation epoch, pending compaction boundary, and ordered queue.

- `normal` is accepted only while idle. Publish `accepted`, then invoke the child's exact
  `PromptRequest`, then publish `started` once invocation owns the active slot. Completion returns the
  activity to idle and starts at most one queued prompt. ACP `PromptResponse` is settlement, never
  transcript content.
- `queue` is accepted only while a prompt is active. Append one `QueuedPrompt`, publish its `queued`
  receipt with exact one-based position, then publish the replacement queue snapshot. When the active
  prompt settles successfully or by user cancellation, remove the head once, publish the new
  snapshot, and deliver that exact prompt once with `started`. No agent `/queue` command is sent.
- `send_now` is accepted only while a prompt is active. Publish acceptance for the new client message,
  send ACP cancel to the active prompt, await its one settlement/cleanup path, publish `interrupted`
  for the old client message, then start the submitted prompt and publish `started`. Existing queued
  prompts remain behind it in their original order. A cancel timeout retires the child generation;
  the submitted message and queued items are rejected rather than risk duplicate execution.
- `steer` is accepted only while a prompt is active and the backend declares `supports_steer`.
  Delegate the exact submitted `PromptRequest` to `BackendTurnStrategy.steer`; publish the returned
  accepted/rejected receipt. It neither replaces the active slot nor enters the FIFO. The Hermes
  strategy sends a real concurrent ACP prompt whose sole text content is `/steer ` plus the submitted
  text; non-text steer is rejected visibly. Unsupported, idle, or failed steer is never converted to
  queue or Send Now.
- Browser stop/cancel without a queued ID cancels the active prompt and publishes one interrupted
  receipt. Cancel with a queued client-message ID removes only that item, publishes its interrupted
  receipt, recomputes later one-based positions, and publishes one replacement snapshot. Unknown IDs
  are rejected without mutation.
- Duplicate client-message IDs within the live binding are rejected. Public methods serialize state
  mutation, but never hold the state lock while awaiting the agent or publisher.

The prompt task owns one monotonic local epoch. Late response/cancel/death from epoch N cannot settle
epoch N+1. A child failure interrupts the active prompt, rejects every queued prompt in FIFO order,
publishes the empty queue, fails any in-flight compaction, and leaves no item to auto-resume. This is
the frozen child-death recovery rule: after an agent crash the user resubmits deliberately; Panels
does not execute stale queued intent against a reloaded process.

Use named configured timeouts for cancel settlement and shutdown. Broker shutdown stops new delivery,
cancels active prompts, rejects queued prompts, settles pending compaction/permission through their
owners, and completes within the supplied deadline.

## Compaction normalizer

ACP v1 has no compaction notification. The broker and selected backend strategy normalize it into the
frozen `ContextCompaction` values.

- An explicit command is only an exact single text block `/compact` (surrounding whitespace trimmed),
  delivered as an idle `normal` prompt for a backend declaring `observes_compaction`. Before agent
  invocation, publish one `compacting` boundary with injected stable ID and trigger `explicit`.
- After explicit prompt settlement, perform one controlled typed load capture through the ACP-01
  capture seam. Capture updates go to a private typed collector and are not rebroadcast. Normalize the
  extracted non-empty summary to the same boundary ID and publish `compacted`; capture/protocol/empty
  summary failure publishes `failed` with display-safe reason.
- Every ordinary ordered `SessionNotification` is also offered to
  `BackendTurnStrategy.observe_compaction`. A Hermes provenance transition with
  `_meta.hermes.sessionProvenance.reason == "compression"` opens one `automatic` compacting boundary
  per distinct provenance transition. After the owning prompt settles, use the same capture path and
  boundary update. Duplicate provenance cannot produce duplicate boundaries.
- The generic broker checks capability and exact `/compact` syntax only; it never checks a backend
  key or parses Hermes metadata. Backend capture results are normalized to the broker-owned boundary
  ID/trigger. Capture replay must preserve thought typing and never reach the ordinary downstream
  update sink.

The Hermes strategy recognizes only the inspected pinned provenance fields, issues actual `/steer`
through its injected child prompt port, and extracts the compressed summary from controlled typed
load. Unknown/partial metadata is ignored or fails capture visibly; it is never assistant text.

## Permission broker and attached-browser rule

ACP permissions are transient conversation state, not Ticket proposals and not Chat rows.

- The child permission callback enters one broker keyed by employee + ACP session + request ID. One
  pending request is allowed per agent callback; request IDs and time are injected for tests.
- With no browser currently attached to that employee, return exact ACP `cancelled` immediately and
  publish no blocking request. Otherwise publish the exact ordered agent options, tool call, backend,
  five-minute configured deadline, and waiting activity to every attached browser through the event
  publisher.
- A response API receives the route-owned browser connection ID, request ID, and option ID. It accepts
  only a browser still attached to the employee and an exact option supplied by the agent. The first
  valid response wins and returns ACP `selected`; later/duplicate/invalid responses receive a typed
  already-settled or rejected result and cannot mutate the agent outcome.
- One browser disconnect does nothing while another attachment remains. The last attachment leaving
  cancels the request. Timeout, active prompt cancel, child death, new conversation, and shutdown also
  cancel it exactly once. Every published request has one matching outcome when it was visible.
- Panels does not remember `allow_always`, invent scope, reinterpret option kind, or route the decision
  through the canonical gate writer. Exact labels, IDs, kinds, and order survive unchanged.

All future/finalizer work is generation-guarded and idempotent. No timeout task or disconnect callback
may settle a newer request.

## Reverse filesystem service

Implement only when the selected backend advertises filesystem support.

- Accept ACP `fs/read_text_file` and `fs/write_text_file` only for the exact bound session.
- Paths must be absolute. Resolve the declared workspace roots and requested path (including symlink
  targets and a write target's existing parent) before I/O; accept only a path contained by one root.
  Prefix-string checks, `..` escape, sibling-prefix escape, symlink escape, directories, and wrong
  sessions fail closed.
- Reads honor ACP's one-based `line` and non-negative `limit`, preserve text exactly, and use UTF-8
  with explicit decode failure. Writes replace exact UTF-8 text only after confinement validation.
  Do not add glob, delete, mkdir, shell, canonical-product writers, or speculative file APIs.
- Capability negotiation advertises filesystem only when this complete service is injected.

## Reverse terminal service and typed browser snapshots

Implement only when the selected backend advertises terminal support.

- Accept exact ACP create/output/wait/kill/release calls for the bound session. Spawn with
  `asyncio.create_subprocess_exec(command, *args)`—never a shell. Explicit/default cwd must resolve
  inside a workspace root. Validate environment names; build from one injected base environment plus
  exact agent overrides, never ambient process environment.
- Terminal IDs are injected/generated and scoped to employee + ACP session. Unknown, cross-session,
  duplicate, or released IDs fail closed. Merge stderr into stdout in arrival order.
- Retain at most a configured maximum output-byte limit, honoring a smaller agent request. Truncate
  from the beginning at a UTF-8 character boundary and set the exact SDK `truncated` flag. A zero
  limit retains no output. Readers continuously drain output so the child cannot deadlock.
- Publish the ACP-00a `ConversationTerminalState` on creation, each observable bounded-output change,
  exit, and release. `terminal/output` returns the same current snapshot; wait returns the exact exit
  status. Kill terminates the process without releasing the ID. Release kills/waits if necessary,
  publishes one final `released` snapshot, and invalidates the live handle.
- Keep the latest bounded display snapshot separately from live handles for attach replay in ACP-04.
  Process handles/tasks are cleaned on release, child death, new conversation, and shutdown. Cleanup
  is bounded and idempotent. Completed display snapshots may remain only for the live session binding;
  a deliberately new conversation removes them.
- Capability negotiation advertises terminal only when this complete service and publisher are
  injected. Terminal output is never an assistant message.

## Scripted proof and conformance

Extend ACP fixtures/support only as needed for deterministic active prompts, cancel races, steer,
explicit/automatic compaction capture, permission races, filesystem calls, terminal streaming, and
child death. Normal ACP traffic still uses the official SDK.

The production ACP-02 subjects must pass ACP-00 probes 5 (permission exactly once), 7 (declared steer
plus common Queue/Send Now), and 8 (visible compaction). Mutations that double-deliver a queued prompt,
silently reinterpret unsupported steer, settle permission twice, allow path escape, leak terminal
ownership, or rebroadcast capture replay must fail their focused assertions.

## Allowed files

- `src/planner/conversation/**`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_permission_broker.py`
- `tests/unit/test_conversation_reverse_filesystem.py`
- `tests/unit/test_conversation_reverse_terminal.py`
- `tests/unit/test_hermes_acp_turn_strategy.py`
- `tests/support/acp_*.py`
- `tests/fixtures/acp/**`
- `orchestration/tickets/acp-02-turn-broker-reverse-services/**`

Any additional path requires orchestrator approval and a written reason before implementation.

## Must not touch

HTTP/websocket routes, `core/server.py`, config schema/YAML, database/schema/migrations, runtime and
step composition, current `chat/`, `tickets/`, `minds/`, or `hermes_backend/`, legacy registries/tests,
`web/`, CSS/assets, dependency pins/locks, docs, `verify`, or the Hermes checkout. Do not edit an
existing non-ACP test to pass.

## Named acceptance

1. Turn tests prove every allowed/rejected choice and exact publication order; single active owner;
   FIFO positions/replacement; single delivery; send-now/cancel races; duplicate IDs; queue cancel;
   child-death reject-all rule; generation/epoch guards; bounded shutdown.
2. Compaction tests prove explicit live-before-prompt state, automatic distinct-provenance detection,
   same-boundary completion/failure, controlled typed capture without rebroadcast, duplicate
   suppression, and no backend-name branch in generic modules.
3. Permission tests prove exact ordered options, attached-browser ownership, first-valid-response,
   invalid/stale response rejection, one-tab versus last-tab disconnect, timeout/cancel/death/new/
   shutdown settlement exactly once, and no canonical product writer.
4. Filesystem tests prove exact line/limit/read/write semantics and reject relative, traversal,
   sibling-prefix, symlink, wrong-session, directory, decode, and unsupported-capability cases.
5. Terminal tests prove argv-not-shell, cwd/env confinement, cross-session isolation, bounded UTF-8
   truncation, live output/wait/kill/release, exact typed snapshots/replay, process-death cleanup,
   idempotent bounded shutdown, and unsupported-capability rejection.
6. Hermes strategy tests prove real `/steer` prompt delivery, non-text/idle failure, exact provenance
   parsing, controlled summary extraction, and no writes/git operations in the Hermes checkout.
7. ACP-00 probes 5, 7, and 8 run against production subjects and targeted mutations fail.
8. Focused Ruff, strict Mypy over the conversation domain, ACP-00/00a/01/02 Python tests, existing ACP
   web contracts, Svelte check, and full web tests pass. One independent sub-agent reviews the diff;
   the orchestrator integrates serially and runs the canonical `./verify` at checked-in relay default.

## Review and integration

One implementation sub-agent writes a concrete plan and acceptance map. A different sub-agent gives
one focused plan review against this contract, official SDK, ACP-01 lifecycle, and Hermes read-only
source. After disposition, one implementation sub-agent works only allowed files; a different
sub-agent performs one implementation review. Use a second round only for a material unresolved
blocker.

The orchestrator spot-checks turn epochs and cancel ordering, queue single delivery, capture replay
isolation, permission exactly-once settlement, path/symlink confinement, terminal ownership and
cleanup, and generic-vs-Hermes boundaries. ACP-02 integrates after ACP-01 and before ACP-03.
