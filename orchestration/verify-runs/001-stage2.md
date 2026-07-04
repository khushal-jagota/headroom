# verify run 001 — stage 2

- Date: 2026-07-04 11:49 BST
- Tree: HEAD 2143ce1058606198c8a9dc2b1247168a12f859e4 (dirty)
- Command: `./verify`
- Exit code: 1 (non-zero)

```
=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/ruff check . ===
All checks passed!

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/mypy src/ ===
Success: no issues found in 36 source files

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/unit --junitxml=/Users/khushaljagota/.hermes/planning-v2/data/verify/unit.xml ===

no tests ran in 0.00s

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m compileall -q src/ ===

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/e2e --junitxml=/Users/khushaljagota/.hermes/planning-v2/data/verify/e2e.xml ===

no tests ran in 0.00s

[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: FAILED (no tests collected)
[verify] gate build check: ok
[verify] gate e2e suite: FAILED (no tests collected)

[FAIL] item 01 — planning-date math
[FAIL] item 02 — gating chain
[FAIL] item 03 — ceiling auto-accept
[FAIL] item 04 — at-cap stop vs propose
[FAIL] item 05 — one pending proposal per field
[FAIL] item 06 — edit-accept
[FAIL] item 07 — result routing
[FAIL] item 08 — recap
[FAIL] item 09 — blocking
[FAIL] item 10 — sprint-item permissions
[FAIL] item 11 — dispatch ordering
[FAIL] item 12 — day-ticket removal
[FAIL] item 13 — sprint assignment rules
[FAIL] item 14 — claim CAS
[FAIL] item 15 — TTL/reclaim
[FAIL] item 16 — circuit breaker
[FAIL] item 17 — day-plan tree
[FAIL] item 18 — boundary job
[FAIL] item 19 — seed fixtures
[FAIL] item 20 — freeze rules
[FAIL] item 21 — instrument integrity
[FAIL] item 22 — CLI create to live board
[FAIL] item 23 — env-pinned propose
[FAIL] item 24 — accept in Review
[FAIL] item 25 — edit-accept in Review
[FAIL] item 26 — chat panel
[FAIL] item 27 — auto-accept chain e2e
[FAIL] item 28 — day view
[FAIL] item 29 — invalidation
[FAIL] item 30 — dispatcher e2e
[FAIL] item 31 — refresh restores state
[FAIL] item 32 — sprint view live
[FAIL] item 33 — seed e2e
[FAIL] item 34 — snapshot migration e2e
[FAIL] item 35 — dogfood Level A
[FAIL] item 36 — onward grant
VERIFY: 0/36 PASS
```

## Expectation match

Matched the stage-2 expectation exactly:

- Gates ruff / mypy / build check all PASS.
- Unit and e2e suites report "no tests ran" and are marked FAILED (no tests collected) — handled without crash.
- All 36 scoreboard items FAIL.
- Final line is exactly `VERIFY: 0/36 PASS`.
- Exit code is non-zero (1).

Gate order observed: ruff, mypy, unit, build check, e2e — matches contract. No gate anomalies. The skip-scan preflight produced no output (clean), so the run proceeded to the gates as expected.
