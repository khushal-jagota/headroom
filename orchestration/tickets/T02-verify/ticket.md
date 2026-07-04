# T02 — The verify instrument (SPEC stage 2)

## Scope

`./verify` at repo root: the single source of truth for completeness, per SPEC §18.2. Built before any implementation; on the stage-1 tree it must run end to end and report all 36 items FAIL with `VERIFY: 0/36 PASS`, exiting non-zero, while the code gates (ruff, mypy, compileall, node --check) pass.

This ticket text is also the implementation blueprint; the codex plan review runs against this file.

## Deliverables

1. `verify` — executable at repo root: `#!/bin/sh` exec of `.venv/bin/python scripts/verify.py "$@"`.
2. `scripts/verify.py` — a preflight skip-scan of `tests/`, then the gates in the spec's stated order: (a) `ruff check .`; (b) mypy strict over `src/`; (c) unit suite `pytest tests/unit`; (d) build check: `python -m compileall -q src/` and `node --check` on every `*.js` file under `assets/`; (e) e2e suite `pytest tests/e2e` (Playwright, headless). A scan violation prints an explicit message naming file+pattern, skips running the suites, and still prints the full scoreboard (all 36 FAIL — a tainted suite has no valid results). Each gate's raw output is streamed. Then the scoreboard: one line per item `[PASS|FAIL] item NN — <label>`, then exactly `VERIFY: N/36 PASS` as the final line. Exit 0 iff the scan is clean, every gate passed, and N == 36. Gate failures never suppress the scoreboard. verify.py's header comment records the instrument's documented limitation: it proves the named tests ran and passed; assertion strength is enforced by the §18.3 fences and the audit.
3. `scripts/verify_lib.py` — importable logic (unit-testable, no side effects):
   - `ITEMS`: the registry of all 36 acceptance items: number, suite (`unit`/`e2e`), required test-name token (`test_a01`..`test_a21`, `test_a36` unit; `test_e22`..`test_e35` e2e), short label taken from SPEC §18.3.
   - `scan_test_files(paths) -> list[Violation]`: detects `@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out tests (comment lines containing `def test_`), and empty test bodies (AST: a `test_*` function whose body is only `pass`/`...`/docstring). Returns file+pattern per violation.
   - `score(junit_unit_xml, junit_e2e_xml) -> list[ItemResult]`: an item PASSes iff exactly one collected test name is anchored to its token (name starts with `test_aNN_` / `test_eNN_`, underscore required) and that test passed; zero matches, multiple matches, failure, error, or skip all mean FAIL.
4. Pytest invocations write junit XML to a temp/`data/`-ignored location for scoring; verify runs both suites even when the earlier suite fails (the scoreboard must always be complete), but records gate failure. A missing or unparseable junit file scores every item of that suite FAIL; verify never crashes on gate failure.
5. Zero-test tolerance: pytest exit code 5 (no tests collected) is not a crash at stage 2; the affected items simply score FAIL.
6. The scan implements all six §18.2 patterns (`@pytest.mark.skip`, `pytest.skip(`, `xfail`, `.only`, commented-out test, empty body); item 21's test will assert detection of the four its text lists plus the remaining two.

## Constraints

- No test files are created by this ticket (the scan and scorer are exercised for real by item 21 in stage 3).
- `scripts/` is not under mypy strict (instrument, not product), but must pass ruff.
- The scan must be genuinely wired into `./verify` — the audit checks this.
- Timeouts: e2e pytest invocation gets a generous overall timeout (config constant at top of verify.py), so a hung server can never wedge verify forever.

## Acceptance for integration

On the integrated stage-1 tree: `./verify` streams gates a–f, prints 36 FAIL lines and `VERIFY: 0/36 PASS`, exits non-zero. Ruff/mypy/compileall/node gates green. A synthetic file containing `pytest.skip(` dropped into `tests/unit/` makes verify abort with the explicit skip-scan message (manually spot-checked, then removed).
