# T19 implementation review — codex output + orchestrator dispositions

Reviewer: `codex exec` (gpt-5.5, xhigh), raw transcript in
`codex-impl-review-raw.txt` (202,724 tokens). Reviewed `tests/e2e/test_flows_b.py`
against ticket.md, plan.md (incl. §8 binding amendments), SPEC §18.3 items 28–32,
the harness, and the real app surfaces. Three findings, all of the
assertion-tightening class (no functional or fence violations); all three ACCEPTED
and APPLIED by the orchestrator (small fixes, no fix agent needed).

**F1 — [e29] partial boundary-tick report asserts.** The two replan-drain ticks
asserted subsets (child tick omitted `planning_date`; root tick asserted only
`replan.attempts`), while the fence demands exact values and `run_boundary_tick`
returns a pinned four-key report.
**ACCEPTED, applied:** both ticks now assert the full
`{"planning_date": DAY_CUR, "ran": False, "judgment": "ok", "replan": {"attempts": [...]}}`
dict.

**F2 — [e30/e31] dispatcher-tick report asserts weakened.** Neither asserted the
pinned five-key shape, the three empty failure lists, or the deterministic fake pid
(fresh server per test ⇒ FakeSpawnAdapter's first pid is 90001, fakes.py:18).
**ACCEPTED, applied:** both now assert `set(rep)` equals the five pinned keys,
`skipped is None`, `reclaimed == timed_out == spawn_failed == []`, exactly one
spawned entry with `ticket_id == mid` and `pid == 90001` (run_id truthiness kept
where used).

**F3 — [e30] accepted result value not pinned end-to-end.** The claimed
`propose result` asserted only the state flip; the auto-accept stores the body as
the field value and the Review "review" card renders `detail.fields.result.value`
in a single `markdownBlock` (verified: components.js reviewCard, review branch —
one `.markdown-block`, no notes block in this flow).
**ACCEPTED, applied:** after the propose, assert
`fields.result.value == E30_RESULT` and `fields.result.proposal is None`; on the
review card before approve, assert `inner_text(card .markdown-block) == E30_RESULT`.

## Post-fix verification

- `.venv/bin/ruff check tests/e2e/test_flows_b.py` — All checks passed.
- Two consecutive `pytest tests/e2e/test_flows_b.py -q` runs, identical results:
  e28, e29, e30, e31 PASS in both; e32 FAILS in both with the one sanctioned
  external signature — `TimeoutError` in `open_page`'s
  `wait_for_selector('[data-status-group="active"]')` — because
  `src/planner/core/server.py` `_SHELL` again lacks the `screens-sprint.js` script
  tag (the out-of-fence fix landed mid-session, then was clobbered by concurrent
  server.py churn; re-application escalated to the team lead). e32 itself is proven:
  while the tag was present, the implementer recorded two consecutive full runs at
  `5 passed` (7.10s / 7.13s) plus a standalone e32 pass, and the e32 test body is
  byte-identical since (the post-review fixes touched only e29/e30/e31 sections).
- Unit suite on the shared tree: `81 passed`. Anchors `test_e28_..test_e32_` occur
  exactly once each across tests/e2e/ (flows_a, seed_e2e, dogfood checked). No
  skip/xfail/parametrize/empty bodies.
