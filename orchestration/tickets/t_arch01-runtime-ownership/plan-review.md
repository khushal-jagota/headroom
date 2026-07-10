# Independent Codex plan review

Command:

```sh
codex exec --skip-git-repo-check -m gpt-5.5 \
  --config model_reasoning_effort="xhigh" --sandbox read-only \
  - < orchestration/tickets/t_arch01-runtime-ownership/plan-review-prompt.txt \
  2>/dev/null
```

Full output:

```text
1. **Same-session revision is not guaranteed.** The ticket requires same-session resume ([ticket.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/ticket.md:40)) while preserving `SharedGateway.run_ticket_step` delivery ([ticket.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/ticket.md:48)). The plan says revision calls `run_ticket_step(existing_key, ...)` and asserts no `session.create` ([plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:124), [plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:229)), but also excludes touching `SharedGateway` ([plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:364)). Current `SharedGateway.run_ticket_step` delegates to `_resume_or_create` ([shared_gateway.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:236)), and `_resume_or_create` falls back from missing resume to `session.create` ([shared_gateway.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:514), [shared_gateway.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:528), [shared_gateway.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/minds/shared_gateway.py:533)). A stale Hermes key can therefore create a new session, violating exact same-session semantics.

2. **The plan misses a pre-existing running chat-turn collision.** `chat_turns` allows only one running turn per entity ([db.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/core/db.py:123)), and `chat_data.start_turn` raises `already_running` on that conflict ([chat/data.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/chat/data.py:130), [chat/data.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/chat/data.py:163)). Current `SystemB` starts the worker turn after the Ticket has already been claimed/mutated, before the gateway exception handling begins ([system_b.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/runtime/system_b.py:158), [system_b.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/runtime/system_b.py:165), [system_b.py](/Users/khushaljagota/.hermes/planning-v2/src/planner/runtime/system_b.py:211)). The plan reserves a parked thread before mutation and releases after commit ([plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:76), [plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:79), [plan.md](/Users/khushaljagota/.hermes/planning-v2/orchestration/tickets/t_arch01-runtime-ownership/plan.md:114)), but never requires the revision mutation to reject/cancel when a running chat turn already exists. That leaves an HTTP-success path where release cannot open the worker turn, no prompt is submitted, and settlement does not run.
```

Disposition: both findings accepted. The ticket and plan must require strict existing-session
resume, pre-mutation rejection of an existing running turn, and non-stuck settlement for the
post-commit collision race. A follow-up no-violations review is required before implementation.
