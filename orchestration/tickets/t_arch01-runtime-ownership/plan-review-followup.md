# Independent Codex plan follow-up

Command:

```sh
codex exec --skip-git-repo-check -m gpt-5.5 \
  --config model_reasoning_effort="xhigh" --sandbox read-only \
  - < orchestration/tickets/t_arch01-runtime-ownership/plan-review-followup-prompt.txt \
  2>/dev/null
```

Full output:

```text
Both accepted findings are fully and minimally resolved.

Finding 1: the ticket and plan now require strict revision resume, forbid `session.create`/prompt/context acknowledgement on not-found, preserve automatic create-or-resume, and explicitly leave completion correlation untouched ([ticket.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/ticket.md:20), [plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:141), [plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:154)). This directly addresses the current fallback in `SharedGateway._resume_or_create` ([shared_gateway.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:514)) and keeps worker-context behavior at submit only ([shared_gateway.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:564)).

Finding 2: the plan now rejects a pre-existing running chat turn inside the canonical revision transaction before proposal/status mutation, and the action cancels the parked handoff on that exception ([plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:71), [plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:84)). It also covers the post-commit `start_worker_turn` race by submitting no gateway prompt and settling the claimed Ticket errored ([plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:130)). This matches the current one-running-turn invariant and `already_running` path ([db.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/core/db.py:123), [chat/data.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/chat/data.py:130)).

I found no contradiction or new in-scope correctness gap introduced by the amendments.

NO VIOLATIONS
```

Disposition: accepted. Implementation may begin after the planning checkpoint.
