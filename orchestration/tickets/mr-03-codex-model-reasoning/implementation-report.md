# MR-03 implementation report — Codex model and reasoning selection

## Outcome

Codex now exposes MR-01's generic ACP employee-configuration adapter from its production backend
registration. The registration constructs one pinned SDK child factory and shares that exact
definition and factory between durable Codex conversations and temporary catalog discovery.

The adapter uses the ACP semantic categories `model` and `thought_level`. It therefore discovers
the signed-in account's exact values, applies model before reasoning from the refreshed option
list, and retains the generic launch-only lifecycle: configuration runs only for the first
unbound session and is not reapplied to loads, replacement children, or New Conversation.

## Proof

The new provider-boundary tests use deliberately non-Codex option IDs and cover exact catalog
mapping, unrelated-option omission, candidate-model refresh, server-lifetime cache keys,
unsupported reasoning, visible uncached failures, temporary-session cleanup, first-binding and
first-prompt ordering, disappeared reasoning, null/native launch values, and no reapplication
after binding.

Focused gates passed:

```text
$ .venv/bin/ruff check src/planner/conversation/codex_backend.py tests/unit/test_codex_employee_configuration.py
All checks passed!

$ .venv/bin/mypy src/planner/conversation
Success: no issues found in 29 source files

$ .venv/bin/pytest tests/unit/test_codex_backend.py tests/unit/test_codex_employee_configuration.py
.....................                                                    [100%]
21 passed in 0.51s
```

The repository-wide `./verify` and real browser dogfood remain with final program integration.
