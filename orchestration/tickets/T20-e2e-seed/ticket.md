# T20 — E2E items 33–34: seed and snapshot migration (stage 6)

## Scope

Two e2e acceptance tests on the T18 harness.

- `tests/e2e/test_seed_e2e.py`:
  33. `plan seed --source tests/fixtures/planning-md/` (CLI subprocess against the test server) → Board/Sprint/Backlog show the fixture's expected titles and counts (assert the specific fixture values from T07's fixture design).
  34. `plan seed --source migration/source-snapshot/` → EXACTLY the §12 ground truth: 1 sprint 2026-07-01→2026-07-12; 12 items split 6/5/1 with "Ship waitlist mechanics." done; tickets mic-publish + app-typography at needs_plan and landing-gate-1 + durable-personas at in_progress with Chat ID `20260702_114500_0ec57a` preserved on landing-gate-1; 9 deferred items (sprint NULL); 20 ideas. The migration report (CLI `--json` output) lists zero silently-skipped sections — anything unparsed is enumerated (assert the skipped list is exactly the known set: Necessary Calls, tracker/overview files, historical dailies, preamble rule blocks). Second run → zero new entities (assert counts unchanged + duplicates_skipped > 0).

## Constraints

- Item 34 runs against `migration/source-snapshot/` in-repo — never any live directory.
- Assert via API + UI (titles/counts on the three screens for 33; API-level exactness for 34's full ground truth).

## Acceptance for integration

Both tests green headless; `./verify` flips 33–34 to PASS.

**Fence reminder:** exactly ONE test whose name starts with each item's `test_eNN_` anchor may exist across the whole e2e suite — the scorer fails an item with multiple anchored matches. Supplementary tests use non-anchored names.
