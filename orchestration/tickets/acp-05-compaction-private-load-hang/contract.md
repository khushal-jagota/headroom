# ACP-05 asynchronous compaction handoff correction

## Why this ticket exists

Real Hermes dogfood proved that the existing same-child `session/fork -> session/load` capture is
incorrect. Hermes returns the fork ID and then legally emits a candidate-session
`available_commands_update`. The pinned ACP Python SDK can deliver that notification before it observes
Panels' next outgoing load request. Panels currently treats that interleaving as a callback mismatch and
kills the child.

A second deterministic reproduction proved an independent deadlock. Compaction holds the per-employee
publication/update gate across fork and private load, while ordinary ordered ingress needs the same gate.
One older public slot can therefore block the private load barrier behind itself until the five-minute
emergency breaker expires. Hermes completes the same raw operations in seconds.

The primary-source findings are recorded in [research.md](research.md) and
[web-compaction-research.md](web-compaction-research.md). This ticket corrects Panels. It does not change
Hermes, add a quiet-period timer, or change the five-minute breaker.

Owner lifecycle amendment (2026-07-20): the human needs to know only that compaction started, finished,
or failed with the exact reason. Backend context is private and never readable or expandable in Panels.
Hermes legitimately reports success for a short `2 -> 2` no-op compaction with no summary marker, so
successful controlled backend/session completion—not marker presence—is authoritative. This amendment
supersedes the summary-validation and expandable-summary clauses below.

## Frozen behavior

### Asynchronous ACP ingress

1. `session/update` ownership is decided by the notification's exact `sessionId`, never by an assumed
   quiet interval between JSON-RPC requests. A private load capture is armed with its expected session ID
   before the load call. Matching updates route privately even when they arrive before the outgoing load
   observer; valid updates for another session continue through ordinary ingress. The outgoing request ID
   still identifies the load response whose already-observed prefix must finish consuming.
2. The child keeps one bounded, wire-ordered ingress and the existing raw/typed fingerprint check. There
   is no second transcript parser or timing-based reorder. Missing/malformed session identity and genuine
   raw/typed mismatches still fail visibly; legal inter-request notification ordering does not.
3. A backend may send session notifications after lifecycle responses. The active compaction transition
   owns one aggregate-capacity-bounded FIFO quarantine whose entries retain the exact child source key,
   validated session ID, and typed payload. Before commit, a candidate update emitted by the original
   source child and an update emitted by an unpublished child both enter that quarantine; ordinary exact-N
   updates continue publishing normally. The winner's permitted entries drain once in admitted wire order
   during its stream transition. They never appear in the old browser epoch and are not lost merely
   because they arrived after a lifecycle response.

### Fresh-child durability proof

4. After the compacting prompt and its ordered response-consumption barrier, the exact leased source child
   calls advertised ACP `session/fork` for its exact bound session. The source child is never loaded to the
   returned fork.
5. The registry creates and initializes a fresh unpublished candidate child generation, privately loads
   the exact fork on that child, and waits through the load response's already-observed notification
   prefix. This is the durability proof: the fork must be loadable from another process rather than only
   visible in the source process's in-memory session manager.
6. Successful Hermes command completion followed by the controlled fresh-child load is sufficient for
   commit. A marker-free replay is valid, including Hermes's short no-op compaction. The existing Hermes
   marker recognizer is only a privacy filter: one valid private context item is suppressed, while zero
   preserves the ordinary replay unchanged. Protocol rejection or malformed/multiple marker-like private
   items still fails closed so backend context cannot leak. Panels never fabricates or exposes a summary
   from replay, `/compact` prose, or token counts.

### Lifecycle exclusion and publication

7. Per-employee lifecycle mutation has a distinct reservation from the publication/update gate. The
   lifecycle reservation serializes attach, new conversation, requested-cancel recovery, compaction, and
   other child/binding transitions. No ACP child call, repository call, response barrier, or restore/load
   operation is awaited while holding the publication/update gate.
8. The publication/update gate is used only for short exact-handle state marks and final publication
   quiescence. Before N+1 becomes the registry winner, all admitted generation-N public sinks finish. The
   durable repository/mirror wins first; then the candidate child/record is published as a new child
   generation and record identity; then the source child is retired.
9. Child, registry, requested-cancel, and compaction-transition replay remains raw typed ACP replay. The
   Hub is the sole replay classifier: once per complete controlled-load batch it asks the bound backend
   strategy to suppress a private marker if present. With one marker, persisted content-free completion
   boundaries replace it at that position; with none, ordinary replay is preserved and those boundaries
   follow it. The hub then publishes winning transition quarantine entries, `ready`, retained FIFO human
   echoes, and the queue snapshot. The broker does not append a second successful completion. Every
   quarantine entry is consumed or discarded exactly once. No N update appears after reset. A post-commit
   N+1 update uses ordinary ingress after ready.
10. The one compare-and-swap remains binding N to fork binding N+1 and atomically compares N's exact
    persisted compaction provenance while writing only the ordered boundary IDs/triggers settled by this
    capture. IDs are unique within that current tuple. It does not append N's older tuple because the N+1
    summary has replaced that earlier transcript; a loser adopts only the durable winner's provenance.
    Pending FIFO prompts and worker intent are retargeted once to the complete N+1 runtime handle.
    Explicit and automatic compaction use this same path.
11. The binding row stores ordered compaction boundary provenance only: boundary ID and exact
    `explicit | automatic` trigger, never summary text. The ACP-05 column is a no-version-bump amendment
    to unlanded schema v24. Canonical v24 DDL and an idempotent add/backfill support both new and already-
    created dogfood v24 databases without changing `user_version`; initial/New conversation bindings use
    an empty tuple. ACP-06's direct pre-column -> v25 terminal migration must create/backfill and preserve
    this column before deleting binding rows.

### Failure ownership

12. Before durable CAS, a candidate failure closes only the unpublished candidate. Because `/compact` may
    have changed the source process without durably rewriting N, Panels must not pretend that source child
    is an authoritative restored runtime. It retires/fails that source generation and resolves the exact
    durable binding through normal fresh attach/recovery; it never performs the obsolete same-child
    switch-back load.
13. If durable CAS committed N+1, candidate/publication failure is N+1 generation-fatal and never rolls the
    binding back. An ambiguous CAS is resolved from the durable repository. An external winner is adopted
    through the normal fresh-child load path. An unused fork may remain because ACP supplies no required
    transactional delete.
14. Concrete protocol, backend, child-death, persistence, and recovery errors surface immediately with
    their exact phase and cause. The existing 300-second absolute lifecycle budget remains only the
    emergency deadlock breaker. No sleep, quiet window, retry loop, or extra sub-budget is added.
15. Every reservation, private epoch, transition quarantine, child, and waiter settles on success,
    failure, cancellation, and shutdown. Normal commit discards non-winning entries; abort, fatal failure,
    external-winner cleanup, expiry, and shutdown synchronously clear the whole quarantine. One failed
    compaction cannot strand later Ticket or Chief work.

## Required proof

- An official-SDK scripted-agent regression emits a fork-candidate update after the fork response and
  before Panels observes the next outgoing load. It also places an ordinary source update ahead of the
  candidate replay. The test proves no callback mismatch, no FIFO/gate deadlock, exact session routing,
  and settlement under a short test-only outer timeout.
- Ordered-ingress tests prove matching pre-request updates route privately, other-session updates stay
  public, the load response barrier waits for its observed prefix, malformed identity still fails, and
  capacity/fingerprint behavior is unchanged.
- Registry tests prove source-child fork -> fresh-child private load -> successful lifecycle validation -> durable CAS
  -> fresh N+1 publication; no ACP or repository I/O occurs under the publication gate; admitted N sinks
  drain before N+1; source retirement happens after the winner is published; and pre-publication N+1
  updates are drained once.
- Failure tests cover fork/load/validation/CAS errors, ambiguous CAS, external winner adoption, candidate
  death, source death, cancellation, and deadline expiry without same-child restore, leaked gates, leaked
  capture queues, or pending tasks.
- Broker/hub tests preserve reset/replay-private-marker-filter/quarantine/ready/queue order with no trailing
  successful compaction duplicate, two attached browsers,
  exact-handle rejection, FIFO/Send Now retargeting, and explicit/automatic parity. They cover both legal
  candidate origins: the original source child after fork and the unpublished fresh child after load;
  ordinary N stays before reset, admitted post-commit N+1 stays after ready, and every settlement path
  leaves the transition quarantine empty.
- Real Panels Computer Use proves explicit compaction shows started then completed, commits N -> N+1,
  exposes no backend context, survives a hard browser reload and server restart on the same binding, and
  permits a later prompt in that exact conversation.
- Focused Ruff, strict Mypy, affected Python/browser tests, and Svelte diagnostics pass. Canonical
  `./verify` remains reserved for ACP-10.

## Allowed files

- `src/planner/conversation/backend_contracts.py`
- `src/planner/conversation/contracts.py`
- `src/planner/conversation/hermes_turn_strategy.py`
- `src/planner/conversation/sdk_child.py`
- `src/planner/conversation/ordered_ingress.py`
- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/turn_broker.py`
- `src/planner/conversation/hub.py`
- `src/planner/conversation/composition.py`
- `src/planner/conversation/sqlite_binding_repository.py`
- `src/planner/core/db.py`
- `src/planner/conversation/__init__.py` only if an internal public type changes
- `tests/unit/test_acp_employee_child.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_acp_conversation_composition.py`
- `tests/unit/test_acp_binding_repository.py`
- `tests/unit/test_db.py`
- `tests/unit/test_hermes_acp_turn_strategy.py`
- `tests/e2e/test_acp_conversation.py`
- `tests/support/acp_scripted_agent.py`
- `tests/support/acp_in_memory_binding_repository.py`
- `src/planner/conversation/wire_contracts.py`
- `tests/unit/test_conversation_contracts.py`
- `tests/unit/test_acp_conformance_harness.py`
- `tests/support/acp_conformance.py`
- `tests/support/acp_component_runtime.py`
- `tests/support/acp_reference_subject.py`
- `tests/support/acp_runtime_subject.py`
- `tests/support/acp_fixture_writer.py`
- `tests/fixtures/acp/**`
- `web/src/lib/acp/contracts.ts`
- `web/src/lib/acp/panelsTransport.ts`
- `web/src/lib/acp/conversationState.ts`
- `web/src/lib/acp/conversationController.ts`
- `web/src/components/acp/AcpConversationPane.svelte`
- `web/src/components/acp/TranscriptView.svelte`
- `web/tests/acp-*.test.mjs`
- `orchestration/tickets/acp-05-durable-compaction-fork/contract.md`
- this ticket's plan, report, review, and evidence files
- `PROGRESS.md`
- `decisions.md`

No shared visual asset, generated distribution, config, unrelated domain, runtime database, or
Hermes-checkout file is in scope.
