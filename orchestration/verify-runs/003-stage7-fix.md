# Verify Run 003 — Stage 7 fix (e2e items 32/33)

- Date: Sun 05 Jul 2026 15:40 BST
- HEAD: `f8075ec30ae7a2ff7ed8ce03c2fd46a057591235`
- Tree: dirty (fix in src/planner/core/server.py: shell was missing screens-sprint.js / screens-backlog.js script tags; plus pre-existing conftest fake_now param and playbook note)
- Command: `./verify`
- Exit code: 0
- Result: VERIFY: 36/36 PASS

## Full `./verify` output

```

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/ruff check . ===
All checks passed!

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/mypy src/ ===
Success: no issues found in 84 source files

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/unit --junitxml=/Users/khushaljagota/.hermes/planning-v2/data/verify/unit.xml ===
........................................................................ [ 88%]
.........                                                                [100%]
=============================== warnings summary ===============================
.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  /Users/khushaljagota/.hermes/planning-v2/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
81 passed, 1 warning in 1.51s

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m compileall -q src/ ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/api.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/app.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/components.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/config.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/markdown.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/screens-backlog.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/screens-board.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/screens-day.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/screens-review.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/screens-sprint.js ===

=== node --check /Users/khushaljagota/.hermes/planning-v2/assets/screens-ticket.js ===

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/e2e --junitxml=/Users/khushaljagota/.hermes/planning-v2/data/verify/e2e.xml ===
..............                                                           [100%]
14 passed in 18.68s

[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate e2e suite: ok

[PASS] item 01 — planning-date math
[PASS] item 02 — gating chain
[PASS] item 03 — ceiling auto-accept
[PASS] item 04 — at-cap stop vs propose
[PASS] item 05 — one pending proposal per field
[PASS] item 06 — edit-accept
[PASS] item 07 — result routing
[PASS] item 08 — recap
[PASS] item 09 — blocking
[PASS] item 10 — sprint-item permissions
[PASS] item 11 — dispatch ordering
[PASS] item 12 — day-ticket removal
[PASS] item 13 — sprint assignment rules
[PASS] item 14 — claim CAS
[PASS] item 15 — TTL/reclaim
[PASS] item 16 — circuit breaker
[PASS] item 17 — day-plan tree
[PASS] item 18 — boundary job
[PASS] item 19 — seed fixtures
[PASS] item 20 — freeze rules
[PASS] item 21 — instrument integrity
[PASS] item 22 — CLI create to live board
[PASS] item 23 — env-pinned propose
[PASS] item 24 — accept in Review
[PASS] item 25 — edit-accept in Review
[PASS] item 26 — chat panel
[PASS] item 27 — auto-accept chain e2e
[PASS] item 28 — day view
[PASS] item 29 — invalidation
[PASS] item 30 — dispatcher e2e
[PASS] item 31 — refresh restores state
[PASS] item 32 — sprint view live
[PASS] item 33 — seed e2e
[PASS] item 34 — snapshot migration e2e
[PASS] item 35 — dogfood Level A
[PASS] item 36 — onward grant
VERIFY: 36/36 PASS
```
