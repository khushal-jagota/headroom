# ACP-07 exact compaction prompt failure reason — implementation report

Status: IMPLEMENTATION COMPLETE; ready for the ticket's independent focused review.

## Delivered

- The broker retains the concrete exception only at its existing uncancelled
  `session/prompt` exception branch.
- It consults the optional synchronous `prompt_failure_reason` hook only while Panels owns at least
  one pending compaction boundary. Ordinary prompt failures cannot expose provider error detail.
- Hook lookup uses `active.lease.handle.definition.turn_strategy`: the provider strategy frozen into
  the exact acquired runtime lease. Production `strategy_for_lease()` deliberately returns the
  generation-bound capture/steer wrapper, which does not own this provider-local synchronous hook.
- Only a nonempty `str` that is already equal to its trimmed form replaces
  `Employee connection failed`. A missing hook, `None`, empty/whitespace/untrimmed/non-string output,
  or a hook exception preserves the generic reason.
- The selected reason enters the pre-existing `_fail_generation` call exactly once. No new
  settlement path, compaction lifecycle, timer, tagged failure update, fork, or rebind was added.

## Deterministic proofs

- Exact explicit Codex `/compact` rejection publishes one failed explicit boundary, settles the
  tracked turn and queued prompt once, and uses `RuntimeError: explicit compaction rejected`.
- An ordinary prompt with an already-active automatic Codex compaction publishes one failed automatic
  boundary and uses `ValueError: automatic compaction rejected`.
- Both cases complete under the tests' one-second wait; the five-minute observation breaker is never
  entered.
- Ordinary Codex failure remains generic; an arbitrary valid hook is not even consulted without a
  pending boundary.
- Missing, `None`, empty, whitespace-only, untrimmed, non-string, and raising hooks all preserve the
  existing generic failure while a compaction boundary is pending.
- Existing turn-broker, Codex, in-place-compaction, and Hermes strategy suites remain green.

## Scope

Changed only:

- `src/planner/conversation/turn_broker.py`
- `tests/unit/test_conversation_turn_broker.py`
- this ticket's implementation report and focused-check ledger

No provider module, runtime port, catalog/composition/server file, documentation/memory file, package
file, or Hermes source was changed. Canonical `./verify` was not run.
