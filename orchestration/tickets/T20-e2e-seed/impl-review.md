# T20 implementation review — codex exec output + orchestrator dispositions

Reviewed changes: tests/e2e/test_seed_e2e.py (new) and tests/e2e/conftest.py (two-line
additive fake_now parameter), against ticket.md, plan.md (incl. §6 amendments), SPEC §12 +
§18.3 items 33–34, verify_lib.py fence rules, the fixture oracle, the importer, and the
three screens.

## Codex output (verbatim findings)

> DONE_WITH_CONCERNS
>
> **(1) Assertion Strength**
> - [P2] (confidence: 8/10) test_seed_e2e.py:185 only checks the six listed board columns.
>   plan.md:140 also pins that `dropped` has no column. An empty `data-column="dropped"`
>   column would pass this test.
>
> **(2) Plan Deviations**
> - No additional owned-file deviation found beyond the missing `dropped` absence assertion
>   above. The pinned counts, titles, states, `20260702_114500_0ec57a`, seven skipped
>   entries, `duplicates_skipped == 46`, and second-run recounts are present.
>
> **(3) Fence / Scanner Risks**
> - [P1] (confidence: 9/10) Current worktree violates the "two owned files only" fence:
>   `git diff --name-status` shows `orchestration/orchestrator-playbook.md` modified, and
>   untracked files include `scripts/dogfood_cli.py`, `skills/planning-worker.md`,
>   `tests/e2e/test_dogfood.py`, and `tests/e2e/test_flows_b.py`.
> - No anchored-name collision found: exactly one `test_e33_` and one `test_e34_` in
>   tests/e2e.
> - No forbidden skip-scan substrings found in tests/e2e/test_seed_e2e.py.
>
> **(4) Harness Misuse**
> - None found. The `fake_now` parameter is default-preserving (conftest.py:68) and
>   existing callers still use no args or `gateway=`. Playwright waits use explicit
>   WAIT_MS.
>
> **(5) Idempotency-Proof Gaps**
> - None against the reviewed blueprint. Run 2 asserts zero new entity counts, exact
>   `duplicates_skipped == 46`, identical skipped list, and the planned post-run API
>   recounts.
>
> Verification note: codex's sandbox had no usable temp directory, so it could not run
> pytest itself.

## Dispositions

1. **(1) P2 dropped-column gap — ACCEPTED, FIXED.** Verified the server side first:
   `STATE_ORDER` in src/planner/tickets/contracts.py:26 excludes `dropped`, so
   `board_view` never emits a dropped column — but the plan pins the absence and the
   assertion is nearly free. Added one line after the BOARD_33 loop in test_e33:
   `assert page.query_selector('[data-column="dropped"]') is None`.

2. **(3) P1 worktree fence violation — REFUTED (cross-ticket false positive).** The extra
   files codex saw are other tickets' concurrent work in the shared main worktree:
   orchestrator-playbook.md is the team lead's own edit; test_flows_b.py is T19;
   dogfood_cli.py, test_dogfood.py, and skills/ are T21. T20's changes are exactly the
   two owned files (verified with `git diff --name-only` scoped to this ticket's edits
   plus the one new test file). Codex had no ticket-boundary context; nothing to fix.

3. **Codex could not execute pytest** (its sandbox lacks a temp dir) — covered by the
   orchestrator's own gate runs recorded in report.md.

Categories 2, 4, 5 clean. One further integration fact, found by the implementer and
outside codex's categories: `src/planner/core/server.py`'s `_SHELL` does not load
`screens-sprint.js` / `screens-backlog.js`, so `#/sprint` and `#/backlog` render app.js
placeholders and test_e33 fails on the delivered tree. The two-line wiring fix is
integrator-owned (T19's item 32 needs it too) and was requested from the team lead; the
full gate was proven green by the implementer with the tags temporarily applied.
