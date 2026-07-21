# ACP-01 implementation report

## Round-one implementation-review correction

All seven findings in `implementation-review-round-1.md` were corrected within ACP-01 scope:

1. Raw reservation is now transactional and ACP-alias-only. The observer validates with
   `by_alias=True, by_name=False`, maps unsafe/missing discriminators into the frozen display-safe
   rejection domain, appends one complete slot, and only then advances the ordinal. Snake-case,
   unsafe-control, missing, partial, and future load frames each produce exactly one rejection and a
   promptly completed load barrier.
2. Every fatal, forced, and non-draining retirement publishes one terminal cause before cancelling
   the consumer. That cause fails an unfrozen load target or any response-observed consumption
   waiter. Deterministic tests cover overflow, sink failure, force close, unexpected process death,
   and registry shutdown while a load/sink is blocked.
3. Overflow stops new raw acceptance but retains every already-reserved occurrence. The child keeps
   the SDK connection alive until those typed callbacks arrive and the serial sink drains them in
   wire order. The mutation-strength test delays both callbacks by a full event-loop turn after the
   overflow and receives the entire accepted prefix exactly once.
4. Every failure after `session/new` and before replacement publication now generation-retires and
   closes the matching child. Tests cover CAS exception with old-binding recovery through a fresh
   child/load, CAS-loser winner adoption, and post-CAS durable-reread failure followed by a fresh
   load of the persisted winner.
5. Registry shutdown now uses `asyncio.wait` against one absolute deadline. It reserves part of the
   budget for concurrent `force_close`, never serially awaits force work, never gathers after the
   deadline, and reports sorted unfinished employee IDs. Partial factory/initialize/load,
   cancellation-resistant work, blocked sink, stderr flood, and two-child shared-budget tests are
   deterministic. SDK force-close publishes the ingress terminal cause and kills the process before
   any teardown await.
6. `get_or_spawn`, `attach`, and `new_conversation` re-evaluate a joined wave against the complete
   requested `ConversationEmployee` and backend. Incompatible callers perform the explicit backend
   transition and never receive the joined wave's mismatched record.
7. The focused lifecycle evidence now includes independent employee progress, failed-load/no-remint,
   persistence teardown, CAS-loser adoption, late old-generation death, partial shutdown stages,
   shared hard deadlines, every initialize mismatch class, delayed overflow, and death during a
   response-observed load. Production probes 1, 6, and 9 and their three targeted mutations remain
   unchanged and passing.

The corrected focused set is now **70 passed**, up from 44. Ruff passes over every ACP-01-owned
production/support/test file, strict Mypy passes over all nine conversation modules, the ACP
contract JS test passes, and Svelte check reports zero errors and warnings. Exact output and the two
unchanged concurrent ACP-03 integration dependencies are recorded in `focused-checks.txt`.

## Outcome

Implemented the uncomposed official-SDK ACP child runtime, ordered typed ingress, durable
employee/session registry, and pinned Hermes 0.18.2 backend definition. No route, database,
configuration YAML, legacy conversation code, browser component, shared asset, dependency pin, or
Hermes-checkout file was changed.

The production runtime now:

- spawns through `acp.stdio.spawn_agent_process`, validates ACP v1 and exact backend identity/load
  capability, confines environment and Panels employee identity, continuously drains bounded
  stderr, distinguishes intentional close from unexpected exit, and settles death once;
- reserves every raw `session/update` in observer order, fulfills valid slots only from the SDK's
  typed callback, turns invalid replay into the frozen visible rejection, reduces through one
  serial sink, and treats overflow, mismatch, sink failure, and retirement as explicit
  generation-fatal outcomes;
- holds `session/load` until every pre-response valid or rejected slot has passed through the
  ordered consumer, with the same deterministic barrier correcting the ACP-00 direct reference
  subject;
- separates in-memory child generation from durable binding generation, coalesces first demand and
  attach/new waves, persists by full-value compare-and-swap before publication, adopts a CAS winner
  through a fresh child, reloads the same binding after death, and quiesces generation-N sink work
  before N+1 publication;
- resolves process/new/load cwd through the selected backend definition and maps additional roots
  in declared order; and
- defines Hermes as `hermes acp`, agent `hermes-agent` `0.18.2`, first-root cwd, Steer and
  compaction observation enabled, permission enabled, filesystem/terminal disabled, with exact
  Hermes home/source overrides and injected turn strategy.

The scripted support now has deterministic identity, bounded-stderr flood, burst,
Hermes-shaped-history, malformed-load, and process-death switches. The Hermes-shaped fixture keeps
thought, assistant text, missing IDs, tool progress, plan replacement, commands, usage, and
provenance as exact SDK notifications. The production ACP-01 subject passes reusable conformance
probes 1, 6, and 9, and each targeted mutation fails its matching assertion.

## Load-bearing review dispositions made during implementation

Three orchestrator spot-checks found concrete lifecycle risks; all were corrected before handoff:

1. Durable re-reads in `_publish()` and `_replace_conversation()` were moved outside the global
   registry lock while the employee publication gate remains held. A stalled read for employee A
   now provably does not block employee B from initializing and publishing.
2. Forced/cancel/error shutdown now retires ordered ingress without draining, and orderly close
   explicitly fails an unfulfillable reservation after draining the fulfilled accepted prefix.
   Pending-reservation ordinary and forced-close tests prove neither can hang beyond the caller's
   deadline.
3. A downstream sink exception is now generation-fatal, wakes the active load barrier, reaches the
   child fatal/death path once, and cannot leave a dead consumer task behind an unbounded waiter.

No ACP-01 contract contradiction remains.

## Changed files

Production:

- `src/planner/conversation/ordered_ingress.py` (new)
- `src/planner/conversation/sdk_child.py` (new)
- `src/planner/conversation/employee_registry.py` (new)
- `src/planner/conversation/hermes_backend.py` (new)
- `src/planner/conversation/backend_contracts.py`
- `src/planner/conversation/configuration.py`
- `src/planner/conversation/__init__.py`

Proof/support:

- `tests/unit/test_acp_employee_child.py` (new)
- `tests/unit/test_acp_employee_registry.py` (new)
- `tests/unit/test_hermes_acp_backend.py` (new)
- `tests/support/acp_in_memory_binding_repository.py` (new)
- `tests/support/acp_runtime_subject.py` (new)
- `tests/support/acp_reference_subject.py`
- `tests/support/acp_scripted_agent.py`
- `tests/support/acp_fixture_writer.py` (Ruff import-order normalization only)
- `tests/fixtures/acp/hermes-shaped-replay-v1.json` (new)
- this report and `focused-checks.txt`

## Verification

The exact focused output is in `focused-checks.txt`.

- ACP-01/source/support Ruff: pass.
- strict Mypy over all nine conversation source modules: pass.
- ACP-01 plus ACP-00 conformance harness: **70 passed** after round-one corrections.
- Python-emitted TypeScript ACP contract test: pass.
- Svelte check: 0 errors and 0 warnings.
- Scoped `git diff --check`: pass.

The combined ACP-00 Python contract file and full web script each currently reach one unrelated
ACP-03 in-progress integration assertion: the donor source hash/provenance assertion and the managed
markdown caller inventory respectively. These are documented concurrent-scope dependencies, not
ACP-01 defects; no ACP-03 file was touched or reverted. The orchestrator will rerun the complete
focused commands once ACP-03 settles and alone owns canonical `./verify`.
