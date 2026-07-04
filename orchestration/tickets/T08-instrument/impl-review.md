# T08 impl review — Codex on tests/unit/test_instrument.py

Reviewed against SPEC.md item 21 (~line 298) and the six forbidden patterns of §18.2 (~line 263), for weakened assertions.

## Codex verdict

**Not fully clean** on first pass — 4 findings. Coverage of both halves of item 21 confirmed present (all six forbidden patterns detected in a synthetic dirty set, clean set yields none, `PLAN_FAKE_NOW` honored with `PLAN_TEST_MODE=1` and ignored without it). Category 4 clean: no literal forbidden patterns in the test's own source; a direct `scan_test_files` call on the file returned `[]`.

### Findings and dispositions (all fixed)

1. **Scanner assertion weakened by reducing `Violation(file, pattern)` to `{v.pattern}`** (was lines 81–90). Proved the six pattern names appear but not the count or the file attribution; a scanner that over-reports duplicates or misattributes a pattern would still pass.
   - **Fixed.** Now asserts `set(violations) == expected_violations` against an explicit exact `(file, pattern)` set — each pattern bound to its own synthetic file — plus `len(violations) == len(expected_violations)` to reject duplicates. Exact per SPEC 18.3.

2. **Vacuous `assert Path(v.file).exists()` loop** (was line 94). Only checked reported files exist, not correct attribution.
   - **Fixed / removed.** Subsumed by the exact-mapping assertion in (1), which checks attribution precisely; the weak loop is deleted.

3. **`on_clock.now().replace(tzinfo=None) == expected` strips tzinfo** (was line 123) — approximate, would miss a timezone-awareness regression in the test clock.
   - **Fixed.** Computes `fake_aware = datetime.fromisoformat(fake_iso).astimezone()` (exactly what `parse_fake_now` does for a naive ISO) and asserts `on_clock.now() == fake_aware` on the full tz-aware instant, plus `now().tzinfo is not None` and `now_unix() == int(fake_aware.timestamp())`.

4. **`off_clock.now().year != expected.year` time-dependent and weak** (was line 132) — only proved the off clock is not in 2021.
   - **Fixed.** Samples real time around the call: `before = now(); observed = off_clock.now(); after = now()`, asserts `before <= observed <= after` (proves it reads live wall time) and `observed != fake_aware` (proves it is not the discarded fake).

## Post-fix gates

- `.venv/bin/pytest tests/unit/test_instrument.py -q` — 1 passed.
- `.venv/bin/ruff check tests/unit/test_instrument.py` — All checks passed.
- Self-scan (`scan_test_files(['tests/unit/test_instrument.py'])`) — `[]` (does not trip its own scanner).

Findings 1–4 all addressed with exact, non-flaky assertions; no findings refuted.
