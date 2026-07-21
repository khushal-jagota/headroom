# ACP-05 cancellation classification correction

## Why this ticket exists

Real Computer Use dogfood clicked **Stop** on an active Chief turn. Hermes accepted cancellation and
the ACP child stayed alive, but the outstanding `session/prompt` await raised as the cancelled turn
unwound. Panels handled that exception before consulting its already-recorded cancellation cause,
failed the generation, and showed `failed` / `interrupted · Employee connection failed` instead of a
normal interrupted then idle transition.

## Frozen behavior

1. Once user, Send Now, New conversation, or shutdown cancellation has been recorded and its exact
   ACP cancel delivery succeeds, an exception from that cancelled prompt await is settlement of the
   requested interruption. It is not independently a child-connection failure.
2. User Stop publishes interrupted then idle and keeps the same child generation usable.
3. Send Now still interrupts the predecessor exactly once and starts only its captured successor.
4. New conversation and shutdown keep their existing interrupted settlement without publishing a
   false connection failure.
5. A prompt exception without a requested cancellation still fails the generation. Child-failure,
   cancel-delivery failure, permission-cancellation failure, and cancellation timeout retain their
   current fail-closed behavior.

## Required proof

- Unit broker regression for user Stop where the prompt raises only after cancel succeeds: tracked
  result interrupted, activity interrupted then idle, generation remains open, and a later prompt
  succeeds on the same child.
- Focused Send Now regression proving one successor delivery after the predecessor raises during
  cancellation.
- Control regression proving an ordinary prompt exception still fails the generation.
- Existing broker, hub, ACP step gateway, and official-child e2e cancellation tests remain green.
- Focused Ruff and strict Mypy pass. Do not run canonical `./verify`; ACP-10 owns the final run.

## Allowed files

- `src/planner/conversation/turn_broker.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_hub.py` only if the visible false-failure regression cannot be proven
  at the broker boundary
- this ticket's plan/report/review/evidence files
- `PROGRESS.md`
- `decisions.md`

No browser, wire contract, child, registry, backend definition, generated distribution, config,
runtime database, or Hermes-checkout file is in scope.
