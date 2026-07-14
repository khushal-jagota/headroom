# Codex corrected-implementation review

Model: `gpt-5.5`
Reasoning effort: `high`
Sandbox: `read-only`

Full output:

```text
- src/planner/runtime/employee_step_runner.py:623: the corrected shutdown race is only first-wins for the Chat turn, not for Ticket state. If `stop(deadline=expired)` settles the matched turn as `interrupted` at lines 273-291, the worker thread can later return `RunResult("complete", ...)`; the complete branch then calls `finish_running_step(...)` at line 632 and moves the Ticket out of `agent_running_step` despite the shutdown-time interruption. That violates durable restart recovery. The new regression only returns `"interrupted"` after release at tests/unit/test_employee_step_runner.py:1236, so it does not cover this late-complete race. Remediation: make Ticket completion conditional on this run actually winning the visible-turn settlement as `complete`; if the turn was already settled `interrupted` by shutdown, return `False` and leave the Ticket/session identity unchanged. Add a regression where expired `stop` settles the turn, then the gateway returns `complete`, and assert the Ticket remains `agent_running_step` with the same `employee_session_id`.
```
