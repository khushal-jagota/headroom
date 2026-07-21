# ACP-05 cancellation classification implementation report

## Outcome

Implemented the cancellation-local correction in `_Actor._settle_prompt`.

- Settlement captures the recorded cancellation cause before interpreting the completed prompt.
- A prompt exception after successful user, Send Now, New conversation, or shutdown cancellation
  continues through the existing cause-specific interrupted settlement with `response=None`.
- Cancellation delivery and permission cancellation must both be complete and successful before that
  classification applies. Their existing callbacks remain authoritative for failure, and cancelled
  owner tasks remain with the timeout/closing path that cancelled them.
- No-cause and `child_failure` prompt exceptions still fail the generation as an Employee connection
  failure.
- User Stop publishes interrupted then idle, retains the child generation, and accepts a later prompt
  on that exact child. Send Now starts one captured successor and interrupts the predecessor once.

Only `src/planner/conversation/turn_broker.py` and
`tests/unit/test_conversation_turn_broker.py` changed for this implementation. No hub change was
needed.

## TDD evidence

The Stop regression was written first and failed against the original classification:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_turn_broker.py::test_user_cancelled_prompt_exception_settles_as_interrupted_and_keeps_child_usable
F                                                                        [100%]
E       AssertionError: assert 'errored' == 'interrupted'
1 failed
```

After admitting only user cancellation, that slice passed:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_turn_broker.py::test_user_cancelled_prompt_exception_settles_as_interrupted_and_keeps_child_usable
.                                                                        [100%]
```

The Send Now regression was then written and failed because no successor started:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_turn_broker.py::test_send_now_delivers_one_successor_when_cancelled_prompt_raises
F                                                                        [100%]
E       TimeoutError
1 failed
```

After extending requested-interruption classification to the complete frozen set, Stop and Send Now
passed together. The no-cause control was then added and all three passed:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_turn_broker.py::test_user_cancelled_prompt_exception_settles_as_interrupted_and_keeps_child_usable tests/unit/test_conversation_turn_broker.py::test_send_now_delivers_one_successor_when_cancelled_prompt_raises tests/unit/test_conversation_turn_broker.py::test_uncancelled_prompt_exception_still_fails_generation
...                                                                      [100%]
```

The fake ACP child is still exercised through its public `prompt` and `cancel` boundary. Its response
queue now accepts an exception, and each requested-interruption test waits until the fake child's
cancel call has completed before releasing that exception. This deterministically reproduces the
Hermes unwind ordering from dogfood.

## Focused verification

Complete broker suite, including existing timeout, child-death, New conversation, shutdown,
permission, queue, compaction, and Send Now behavior:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_turn_broker.py
..............................                                           [100%]
30 passed
```

Hub plus exact synchronous-gateway interruption slice:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_hub.py tests/unit/test_acp_step_gateway.py::test_interrupt_targets_only_current_binding_and_honours_deadline tests/unit/test_acp_step_gateway.py::test_late_interrupt_does_not_cancel_successor_turn
..........                                                               [100%]
10 passed
```

Production-composed official-SDK cancellation and active-replay scenarios:

```text
$ .venv/bin/pytest -q tests/e2e/test_acp_conversation.py::test_browser_and_worker_share_one_real_sdk_session tests/e2e/test_acp_conversation.py::test_active_vertical_replay_fails_closed_without_harming_live_browser
...                                                                      [100%]
3 passed, 1 StarletteDeprecationWarning
```

Scoped static checks:

```text
$ .venv/bin/ruff check src/planner/conversation/turn_broker.py tests/unit/test_conversation_turn_broker.py
All checks passed!

$ .venv/bin/mypy src/planner/conversation/turn_broker.py
Success: no issues found in 1 source file
```

Canonical `./verify` was not run; ACP-10 retains the one final run. No generated distribution,
configuration, runtime data, unrelated ACP source, or Hermes-checkout file was touched.
