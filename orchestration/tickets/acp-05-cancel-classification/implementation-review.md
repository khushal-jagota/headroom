# ACP-05 cancellation classification implementation review

## Verdict

**READY**

## Findings

No concrete frozen-behavior violation, race, or missing required proof found.

## Review

- `_Actor._settle_prompt` captures the recorded cancellation cause before reading the prompt task.
- It waits for both ACP cancel delivery and permission cancellation to finish successfully before
  classifying a prompt exception as an interruption.
- User, Send Now, New conversation, and shutdown cancellations retain their existing
  cause-specific settlement. `child_failure` and an exception without a requested cancellation
  still fail the generation.
- The Stop regression proves interrupted then idle, no runtime retirement, reuse of the same child,
  and a successful later prompt.
- The Send Now regression proves one predecessor interruption and one captured-successor start.
- The no-cause control proves an ordinary prompt exception still settles errored and leaves the
  actor failed.

## Focused check

```text
.venv/bin/pytest -q \
  tests/unit/test_conversation_turn_broker.py::test_user_cancelled_prompt_exception_settles_as_interrupted_and_keeps_child_usable \
  tests/unit/test_conversation_turn_broker.py::test_send_now_delivers_one_successor_when_cancelled_prompt_raises \
  tests/unit/test_conversation_turn_broker.py::test_uncancelled_prompt_exception_still_fails_generation
...                                                                      [100%]
```

Canonical `./verify` was not run.
