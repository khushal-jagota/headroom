# Verify Run 002 — Stage 3

- Date: Sat 4 Jul 2026 17:22 BST
- HEAD: `170514b865763ff63092d1abb4b71172d003b18f`
- Tree: dirty (uncommitted stage-4+ scaffolding and orchestration notes present; see note below)
- Exit code: 1 (non-zero)

## Full `./verify` output

```
=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/ruff check . ===
All checks passed!

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/mypy src/ ===
Success: no issues found in 74 source files

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/unit --junitxml=/Users/khushaljagota/.hermes/planning-v2/data/verify/unit.xml ===
............................................                             [100%]
44 passed in 0.19s

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m compileall -q src/ ===

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest tests/e2e --junitxml=/Users/khushaljagota/.hermes/planning-v2/data/verify/e2e.xml ===

no tests ran in 0.00s

[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate e2e suite: FAILED (no tests collected)

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
[PASS] item 36 — onward grant
VERIFY: 22/36 PASS
```

## Expectation check

Reality matched the stated stage-3 expectation.

- Gates ruff, mypy, unit suite, build check: all PASS. Matched.
- e2e gate: reports no tests collected (marked FAILED — no e2e tests exist yet at this stage). Matched.
- Scoreboard: items 01–21 and 36 PASS (22 unit items), items 22–35 FAIL. Matched.
- Final line: `VERIFY: 22/36 PASS`. Matched.
- Exit: non-zero (1). Matched.

## Anomalies

None. No unit item unexpectedly FAILed — every item expected to pass (01–21, 36) passed. The 44 passing pytest cases roll up to the 22 passing scoreboard items as designed.

Note: the working tree is dirty. The uncommitted files are stage-4-and-beyond scaffolding (days/dispatch/sprints/tickets/seed modules, their unit tests, and orchestration ticket notes) plus updated memory files (PROGRESS.md, decisions.md, playbook). This did not affect the stage-3 result — those new unit tests are part of the 44 that pass, and no e2e tests are present yet.
