# ACP-07 exact compaction prompt failure reason

## Outcome

When a provider's ACP `session/prompt` fails while Panels has a pending compaction boundary, the
transcript and activity show the provider-approved exact reason immediately. Panels does not wait for
the five-minute observation breaker, invent a failed tool update, expose arbitrary errors from other
prompts, or change the existing generation-failure policy.

This closes the exact pinned Codex behavior: its `/compact` command awaits same-thread completion,
emits tagged start and completed updates only, and rejects the ACP prompt on command/process failure.

## Contract

- A backend turn strategy may expose an optional synchronous
  `prompt_failure_reason(session_binding, prompt, error) -> str | None` hook.
- The broker consults it only for an uncancelled prompt exception and only through the exact active
  runtime lease. `None`, an empty/untrimmed result, a hook exception, or no hook preserves the existing
  generic `Employee connection failed` behavior.
- A valid provider reason replaces only the user-visible/tracked failure reason passed to the existing
  generation-failure path. Existing child retirement, queue rejection, permission/terminal settlement,
  compaction-boundary failure, and lifecycle semantics remain unchanged.
- Codex returns a display-safe concrete `TypeName: message` only when the prompt is exact `/compact`
  or its exact binding-generation compaction state is already active. It resets that provider-local
  state as the generation fails. Ordinary Codex prompt errors remain generic.
- Do not create or accept tagged `status=failed` Codex compaction updates: the locked adapter does not
  emit them. Do not parse prose, inspect private state, fork/rebind, or add a timer.

## Allowed files

- `src/planner/conversation/turn_broker.py`
- `tests/unit/test_conversation_turn_broker.py`
- `src/planner/conversation/codex_turn_strategy.py` and its test only if not already completed in the
  provider lane
- this ticket's report/check ledger

## Acceptance

Deterministic tests prove exact explicit and active-automatic Codex failure reasons, generic ordinary
prompt failure, absent/invalid/raising hook fallback, one failed compaction boundary with the same
reason, normal generation failure/queue settlement, and no five-minute wait. Existing turn-broker,
in-place strategy, Hermes strategy, and Codex strategy focused suites pass with Ruff/strict Mypy.
Do not run `./verify`.
