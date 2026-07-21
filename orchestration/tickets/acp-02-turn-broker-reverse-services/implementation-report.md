# ACP-02 implementation report

## Round-one implementation-review correction

All five findings in `implementation-review-round-1.md` were corrected within ACP-02 scope:

1. Runtime adoption is now an actor command, never a lookup-lock mutation. Every normal, queued,
   Send Now, cancel, observation, permission, and Steer command validates its submitted complete
   runtime handle. A prompt paused at accepted publication either leases that exact generation or
   fails stale; it cannot reread and reach N+1. FIFO intent is rebound only by the broker-owned,
   serialized controlled-capture handoff, preserving the reviewed rule that queued N intent resumes
   once on the returned N+1 handle.
2. Command admission and actor-runner exit share one admission lock and accepting state. Closed or
   runnerless actors reject synchronously, runner exit drains admitted futures, and forced disposal
   cancels them. Late child-death, permission-publication-failure, and terminal-publication-failure
   callbacks after both new conversation and shutdown now return promptly instead of queuing behind
   an exited runner.
3. Deadline expiry no longer performs an unbounded cancellation gather. Broker actors, permission
   records/settlement/failure tasks, and terminal handles/process tasks are synchronously detached,
   canceled, and reported at the original absolute deadline. Terminal watcher/release/publication
   failure removes the live handle even when a publisher or reader suppresses cancellation. Tests
   hold actor publication, permission outcome publication, terminal release publication, and a
   terminal reader across cancellation and prove bounded return plus empty ownership tables.
4. Permission outcome publication is now the ACP response commit point. A selected/cancelled response
   is resolved exactly once before activity restoration; later activity failure is generation-fatal
   but cannot rewrite the response. Outcome failure still resolves one cancelled response. Failure
   notification runs in an independently owned task, so it cannot re-enter the actor and cancel/await
   its own permission-cancel task.
5. `_Actor.activity()` uses `ConversationActivityState` and `_TerminalHandle.lifecycle` uses
   `ConversationTerminalLifecycle`. The terminal snapshot type suppression was removed.

The named deterministic correction set is **11 passed**. The complete affected ACP Python set is
**214 passed**, with scoped Ruff, strict Mypy, and diff check green. Exact output is appended to
`focused-checks.txt`. No finding was deferred.

## Outcome

Implemented the uncomposed ACP conversation turn broker, exact-generation runtime seams,
compaction normalization, transient permission ownership, confined reverse filesystem, and scoped
reverse terminals. The implementation composes through the reviewed ACP-01 child/registry boundary
and the official ACP SDK. It does not add a route, websocket, database writer, product runtime
composition, Svelte code, legacy cutover, or backend registration.

The production conversation domain now:

- owns one actor per employee and durable binding generation, with one active prompt epoch, explicit
  FIFO positions, Send Now cancellation/settlement, declared native Steer, exact duplicate/session
  rejection, child-death reject-all recovery, and one hard shutdown deadline;
- waits for ordered typed update consumption before settling prompt responses, and performs
  compaction capture through a private typed ingress on a fresh child generation rather than
  rebroadcasting replay;
- binds prompt, cancel, strategy, capture, permission, and terminal work to a complete runtime handle
  and opaque record identity, so generation N work fails instead of redirecting to N+1;
- normalizes exact `/compact` and Hermes compression provenance into visible compacting/compacted or
  failed boundaries, preserving thought typing and all four inspected Hermes summary placements;
- owns visible permission requests through the exact attached browser, prompt epoch, child
  generation, and live session, with first-valid-response settlement and late-open tombstones;
- confines reverse file reads/writes with descriptor-owned roots and no-follow traversal, preserving
  exact ACP line/limit and UTF-8 behavior while rejecting scope, symlink, traversal, and race escapes;
- owns reverse terminal processes without a shell, confines cwd and environment, continuously drains
  bounded UTF-8 output, publishes exact terminal snapshots, and bounds kill/release/death/new/shutdown
  cleanup; and
- advertises and serves filesystem/terminal reverse methods only when the selected backend declares
  the capability and the complete service is injected. Undeclared real-SDK calls return JSON-RPC
  method-not-found.

No generic turn, permission, filesystem, terminal, or runtime-port module contains a Hermes backend
name branch or imports a Chat/Ticket product writer. Hermes-specific native steer and compaction
parsing live only in `hermes_turn_strategy.py`. The Hermes checkout was read only; no writes or git
commands were performed there.

## Load-bearing corrections made before handoff

The orchestrator spot-checked the implementation during construction and found integration gaps
that green unit subsets did not yet prove. All were corrected in ACP-02 scope:

1. The employee registry now returns the exact-generation strategy proxy, and a controlled capture
   returns and installs its fresh N+1 runtime handle before FIFO admission resumes. Planned
   retirement tokens distinguish exact intentional generation-N teardown from child death; the
   correction pass additionally confines all external runtime adoption to the actor queue.
2. Publisher exceptions on public commands are generation-fatal. The actor cancels active/capture/
   strategy work, rejects the FIFO, settles reverse owners, and cannot start another prompt with
   unpublished UI state.
3. Permission and terminal services are composed through the turn actor. Cancel timeout, child
   death, new conversation, capture, and shutdown now tombstone late permissions and clean exact
   generation terminals before successor work.
4. Terminal publisher failure wakes output/exit/release waiters, stops the process, notifies the
   owning actor without deadlocking it, and cancels plus awaits pending release work within the
   existing deadline.
5. Send Now waits for both cancel transmission and permission cancellation before starting its
   successor. Cancel timeout retires the exact lease and rejects submitted/queued intent rather than
   risking duplicate execution.
6. Broker, permission, and terminal shutdown calls share idempotent tasks and one absolute deadline.
   Closed actors reject late callbacks instead of leaving command futures stranded; deadline expiry
   force-detaches cancellation-resistant work without a second unbounded wait.

No review finding was deferred.

## Changed files

Production modules added for ACP-02:

- `src/planner/conversation/runtime_event_publisher.py`
- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/turn_broker.py`
- `src/planner/conversation/permission_broker.py`
- `src/planner/conversation/hermes_turn_strategy.py`
- `src/planner/conversation/reverse_services/__init__.py`
- `src/planner/conversation/reverse_services/path_confinement.py`
- `src/planner/conversation/reverse_services/filesystem.py`
- `src/planner/conversation/reverse_services/terminal.py`

Existing conversation modules extended at the reviewed integration seams:

- `src/planner/conversation/configuration.py`
- `src/planner/conversation/ordered_ingress.py`
- `src/planner/conversation/sdk_child.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/__init__.py`

Proof and deterministic support:

- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_permission_broker.py`
- `tests/unit/test_conversation_reverse_filesystem.py`
- `tests/unit/test_conversation_reverse_terminal.py`
- `tests/unit/test_hermes_acp_turn_strategy.py`
- `tests/support/acp_runtime_subject.py`
- `tests/support/acp_scripted_agent.py`
- `tests/fixtures/acp/terminal_fixture.py`
- this report and `focused-checks.txt`

Frozen ACP-00 contract/model shapes were not changed.

## Named acceptance map

1. **Turn ownership and recovery:** deterministic actor tests cover every allowed/rejected choice,
   publication order, FIFO replacement, one delivery, duplicate IDs, queue cancellation, Send Now
   races/timeouts, child-death reject-all, stale generation/epoch callbacks, publisher failure, and a
   cancellation-resistant hard shutdown deadline.
2. **Compaction:** tests cover live explicit state before prompt start, distinct automatic provenance,
   duplicate suppression, same-boundary completion/failure, permission/terminal settlement before
   capture, exact N to N+1 adoption, response-consumption ordering, private replay isolation, and the
   absence of backend-name/product-writer branches in generic modules.
3. **Permission:** tests cover exact ordered options, attached-browser source ownership, first valid
   response, invalid/stale/duplicate rejection, one-tab versus last-tab detach, timeout, prompt
   cancel, child death, new conversation, shutdown, SDK-callback cancellation shielding, late-open
   tombstones, and permission-outcome publication failure.
4. **Filesystem:** tests cover one-based line/limit including zero limit, exact replacement writes,
   invalid UTF-8, relative/traversal/sibling/directory/session/generation rejection, internal and
   escaping/dangling symlinks, missing parents, descriptor race resistance, declared-service startup,
   real official-SDK calls, and undeclared method-not-found behavior.
5. **Terminal:** tests cover literal argv without a shell, confined explicit/default cwd and injected
   environment, cross-session/employee isolation, bounded UTF-8 truncation including zero retention,
   streaming output/wait/kill/release, typed active/exited/released snapshots, display replay and new
   conversation clearing, process-death cleanup, publication failure, concurrent idempotent bounded
   shutdown, real official-SDK calls, and declared-service startup rejection.
6. **Hermes strategy:** tests cover one real `/steer` prompt, non-text rejection, exact compression
   provenance, the pinned inspected prefix hash, all standalone/merged user/assistant summary
   placements, missing/ambiguous capture failure, and no redirect from paused generation N to N+1.
7. **Conformance:** the production ACP-02 subject passes ACP-00 probes 5, 7, and 8; targeted double
   permission settlement, queue/Send Now delivery, unsupported-Steer reinterpretation, compaction
   visibility, and private-capture mutations fail their focused assertions.
8. **Focused gates:** all reviewed source, compatibility, TypeScript, Svelte, full-web, and scoped
   whitespace gates pass. ACP-03's standalone browser state, conformance, and real-component suites
   also pass explicitly; they are recorded separately because the current package test script does
   not invoke them. Exact commands and complete outputs are in `focused-checks.txt`.

The five ACP-02-owned Python suites contribute **75 passing tests** to the combined focused run.

## Verification

The settled final sequence was rerun from the beginning after the orchestrator reconciled one stale,
out-of-scope ACP-00 provenance assertion with ACP-03's already-reviewed local donor correction. No
ACP-02 file was changed to bypass that integration failure.

- Scoped Ruff: pass.
- Strict Mypy: pass, 18 conversation source files checked.
- ACP-00/00a/01/02 Python acceptance after review correction: **214 passed in 8.05s**.
- Named implementation-review race/cleanup reproductions: **11 passed**.
- ACP TypeScript contract test: pass.
- Svelte diagnostics: 0 errors and 0 warnings.
- Full `npm --prefix web test`: pass.
- Standalone ACP browser state test: pass.
- Standalone ACP browser conformance test: pass.
- Standalone ACP browser component interaction test: pass.
- Scoped `git diff --check`: pass.

The web gates above retain their already-recorded green outputs and were not rerun during the bounded
review correction because no web source changed.

Canonical `./verify` was not run by this ticket implementer. Per the contract, the orchestrator owns
serial integration and the canonical verification run after independent implementation review.
