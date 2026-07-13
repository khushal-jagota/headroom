# Plan review disposition

Codex reported three concrete gaps. All are accepted and the implementation plan was corrected before dispatch.

1. **Worker turn after proposal commit:** accepted. The plan now separately settles a worker-origin running turn when its ticket has already left `agent_running_step`, without another prompt or ticket-status mutation.
2. **Exact recovery message:** accepted. The plan now requires assertions for every owner-directed clause and proves `_next_step_prompt` is not used.
3. **One deadline across both role gateways:** accepted. The plan now requires one monotonic absolute deadline threaded through runtime, worker gateway, Chief gateway, session manager, router, child waits, and thread joins; serial cleanup cannot restart the clock.

No finding was refuted or deferred.
