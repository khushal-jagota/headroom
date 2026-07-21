# MR-04 implementation report — Claude model and reasoning selection

## Result

Claude now uses the shared stable ACP employee-configuration adapter. The materialized
Claude registration gives that adapter the same locked backend definition, repository
root, and compaction-normalizing `ClaudeAcpEmployeeChildFactory` used for durable work
and startup preflight. The decorator forwards configuration and temporary-session close
operations without bypassing the ACP child boundary.

This keeps the provider behavior launch-only: the first unbound session applies an
explicit model and then the refreshed semantic `thought_level` option before binding.
Bound loads, replacement children, and later New Conversation calls do not reapply the
historical Ticket values.

## Provider proof

The focused Claude tests prove:

- semantic `model` and `thought_level` discovery with deliberately unrelated option IDs;
- exact labels, descriptions, native values, and the explicit effort value `default`;
- model-scoped server-lifetime caching and temporary session/child cleanup;
- omission of Reasoning when the selected model no longer advertises `thought_level`;
- visible, uncached failures for unavailable, missing, duplicate, malformed, rejected,
  and close-failing configuration paths;
- model-before-reasoning ordering before first binding and first prompt;
- no binding or prompt after a disappeared/unsupported reasoning selection;
- no reapplication on bound load, replacement, or explicit New Conversation; and
- decorator forwarding while the existing Claude compaction-ingress normalization stays
  active.

The existing Claude backend regression remains unchanged and covers the locked adapter,
environment confinement, startup preflight, and the rest of the decorator contract.

## Focused gates

```text
.venv/bin/ruff check src/planner/conversation/claude_backend.py tests/unit/test_claude_employee_configuration.py
All checks passed!

.venv/bin/mypy src/planner/conversation
Success: no issues found in 29 source files

.venv/bin/pytest tests/unit/test_claude_acp_backend.py tests/unit/test_claude_employee_configuration.py
38 passed in 0.57s
```

The orchestrator still owns the combined implementation review, final `./verify`, and
real Safari dogfood after every provider ticket has settled.
