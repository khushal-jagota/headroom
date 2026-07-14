# Codex implementation review

Model: `gpt-5.5`
Reasoning effort: `high`
Sandbox: `read-only`

Full output:

```text
- src/planner/runtime/employee_step_runner.py:260: `stop(deadline=expired)` can return with active worker threads still running because the `wait_for(..., timeout=0)` result is ignored. Those threads are daemon threads (line 135), and visible-turn settlement only happens later inside the worker thread (lines 576 and 607). This violates the shutdown-time visible-turn settlement requirement and can race process exit. Remediation: synchronously settle still-running matching worker turns from the shutdown snapshot, using first-wins chat settlement, before `stop` returns when the drain deadline is exhausted.

- tests/unit/test_server_shutdown_process.py:152: the subprocess regression sends `SIGINT` and waits, but never asserts `process.returncode == 0`. Lines 159-169 also assert only logs and Ticket session recoverability, not visible worker-turn settlement. This can pass despite a non-clean exit or the settlement race above. Remediation: assert zero exit status and read `chat_turns` to require the active worker turn is settled `interrupted` with the stored session id.

- PROGRESS.md:7 and decisions.md:1293: these files are modified in the implementation diff, but the implementation plan explicitly excludes `PROGRESS.md` and `decisions.md` from the implementation sub-agent scope (implementation-plan.md:19). Remediation: keep those memory updates out of the implementation diff, or separate them from the ticket implementation changes.
```
