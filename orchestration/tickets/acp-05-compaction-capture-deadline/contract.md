# ACP-05 compaction capture deadline correction

## Why this ticket exists

Real Hermes Computer Use proved that the durable fork design works but its production budget does not.
Explicit `/compact` produced a valid summary and Hermes durably created fork session
`66c4a130-1555-4d9b-b0b7-eb2aaad6acaa` with the expected 13-message compacted history. Panels started
the capture transaction with the generic 10-second conversation-service shutdown timeout. Hermes
finished `session/fork` at that boundary, leaving no budget for private fork load, durable binding CAS,
or exact-original restoration. Panels therefore failed the generation and correctly left the durable
binding on generation 6.

Compaction is a normal, potentially slow backend transaction. It still needs one hard deadline so a
hung ACP backend cannot permanently hold the employee gate and block chat, reconnects, queued work, or
Automatic Employee work, but that deadline is not a shutdown cleanup deadline.

## Frozen behavior

1. Add one explicitly named production compaction-capture timeout with a 300-second (five-minute)
   default. Compaction taking a couple of minutes is normal; this is only an extreme deadlock breaker. It owns the
   entire post-prompt transaction: hub admission, official fork, private load/barrier, normalization,
   durable CAS/resolve, original restore or winner adoption, actor rekey, browser commit, and settlement.
2. The actor still mints one absolute monotonic deadline and passes that exact value unchanged through
   every owner. No component creates a sub-budget, resets the clock, shields past it, or waits forever.
3. The 10-second conversation-service shutdown timeout remains the bound for shutdown/cleanup paths. It
   is not globally increased to make one normal operation fit.
4. The deadline is only a deadlock breaker. Expiry must report the exact failed phase—fork, private fork
   load, normalization, durable CAS/resolve, original restore/winner adoption, actor rekey, or browser
   commit—and the configured elapsed budget. The visible boundary and server log must not collapse it
   to `Conversation runtime generation failed during compaction`.
   A non-timeout backend, protocol, persistence, or publication error fails immediately and likewise
   retains its exact phase, exception type, and underlying message in the visible boundary and server
   log. The five-minute deadline must never replace a concrete earlier failure with timeout or generic
   text. An operation that itself raises `TimeoutError` while the Panels deadline still has time
   remaining is such a concrete backend error; only Panels' own wait timer winning is budget expiry.
5. Expiry retains the existing fail-closed pre/post-CAS disposition: Panels never reuses a child whose
   active session is uncertain. It retires that exact child and preserves the authoritative durable
   binding so ordinary fresh attach can load the known session. The failure text says whether the
   binding stayed on N, committed N+1, or could not be resolved inside the expired budget and therefore
   must be read authoritatively on fresh attach; it never invents certainty or calls an uncertain child safe.
6. This ticket does not weaken exact-generation validation, change fork/restore/CAS ordering, invent a
   compaction retry, or adopt the orphaned fork from the failed dogfood run. A later fresh attach is
   ordinary runtime recovery, not an extension of the expired compaction transaction.
7. The Hermes checkout remains read-only. Panels continues to use official ACP `session/fork` and typed
   private replay only.
8. Successful capture still advances the durable binding exactly N -> N+1, publishes one reset/replay/
   ready transition plus a compacted boundary, survives hard browser reload and process restart, and
   accepts a later prompt on the new session.

## Required proof

- Configuration/broker regression proves the production compaction budget is the dedicated 300-second
  value and is distinct from the 10-second shutdown budget.
- Deterministic scaled transaction regression consumes more than the old shutdown-budget analogue but
  remains inside the compaction budget analogue and proves fork/private load/CAS/browser commit succeeds
  as N -> N+1. It must not add a real multi-second sleep or an elaborate event-loop clock harness.
- Existing exact-deadline tests still prove a genuinely expired capture releases gates and waiters and
  takes the existing fail-closed disposition without a second budget. Named assertions require the
  exact failed phase/budget and durable-binding disposition in the surfaced reason.
- A deterministic non-timeout failure proves its phase, exception type, and underlying message reach
  the failed boundary and server log immediately and are not described as deadline expiry.
- Existing durable-fork official-SDK e2e, registry, broker, hub, and composition suites remain green.
- Focused Ruff and strict Mypy pass. Do not run canonical `./verify`; ACP-10 owns the final run.

## Allowed files

- `src/planner/conversation/configuration.py`
- `src/planner/conversation/__init__.py`
- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/turn_broker.py`
- relevant focused tests under `tests/unit/` and `tests/e2e/test_acp_conversation.py`
- this ticket's diagnosis, implementation report, and focused evidence
- `PROGRESS.md`
- `decisions.md`

Do not change the wire schema, browser source, generated `web/dist`, durable database schema, Hermes,
or unrelated runtime behavior. If dogfood disproves five minutes, report the measured operation before
changing the contract; do not silently remove the deadline.
