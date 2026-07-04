# T08 — Instrument integrity test (stage 3, trivial — pipeline collapsed per decisions.md D7)

## Scope

One test file, `tests/unit/test_instrument.py`, containing `test_a21_instrument_integrity` (acceptance item 21):

- The skip-scan (`scripts/verify_lib.scan_test_files`) detects each forbidden pattern in a synthetic file set written to tmp_path: `@pytest.mark.skip`, `pytest.skip(`, `xfail`, empty test body — plus `.only` and a commented-out test (the scan implements all six §18.2 patterns) — and passes a clean set.
- `PLAN_FAKE_NOW` is ignored when `PLAN_TEST_MODE` is unset: with both env vars set vs only `PLAN_FAKE_NOW` set, the clock honors the fake time only in the first case (drive through `core/clock.py`'s constructor/env reading, monkeypatched env).

One named test per item; both halves live in `test_a21_...` since item 21 states both. Files owned: only `tests/unit/test_instrument.py`. ruff clean; test green.
