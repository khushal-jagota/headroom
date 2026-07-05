# T18 implementation review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` over the complete change (tests/e2e/conftest.py,
tests/e2e/test_flows_a.py) against ticket.md, plan.md (incl. amendments A1/A2), SPEC §18.3
items 22–27, and the cited ground-truth source/asset files.

## Verdict

**IMPL OK** — "No concrete violations found across the requested categories."

Checks codex performed, per its transcript:

- Assertion strength vs SPEC exact values (items 22–27): no weakening found.
- Determinism: no spurious-pass waits, no sleep-for-state, no missing timeouts, no teardown
  gaps; the wsOpens gate / catch-up settle / A2 two-send pattern judged sufficient.
- Fences: exactly one test per anchor `test_e22_` … `test_e27_` across tests/e2e; it also ran
  the repo's own scanner directly — `scan_test_files(['tests/e2e'])` returned `[]` (clean).
- Owned files: nothing outside the two owned test files is required or touched.
- Plan conformance: no unjustified deviations from plan.md §A/§B/§H.

Codex noted it did not execute the e2e suite itself (its sandbox is read-only); execution
evidence comes from the orchestrator's own runs below.

## Orchestrator disposition

Accepted as-is; nothing to fix. Independent execution evidence (run by the orchestrator, not
the implementer):

- `.venv/bin/pytest tests/e2e/test_flows_a.py -q` — run twice consecutively, both `6 passed`
  (flake gate).
- Fresh `./verify` — gates ruff/mypy/unit/build/e2e all ok; items 22–27 flipped to PASS;
  final line `VERIFY: 28/36 PASS` (remaining FAILs are items 28–35, owned by T19/T20/T21).

One residual observation for the record (not a violation): pytest-playwright suffixes junit
ids with `[chromium]`. `scripts/verify_lib.py` matches `bare_name.startswith("test_eNN_")`,
so each item still has exactly one anchored match — proven by the fresh scoreboard above.
