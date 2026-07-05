# Codex sanity review — audit-violation fixes (2026-07-05)

Non-interactive `codex exec` review of the six audit fixes (V1–V5 code changes; V6 and
the evidence copies are prose/artifacts and were not in scope). Prompt asked for
concrete violations per item against SPEC §7.6/§13/§14/§18.2, file:line, CLEAN per
clean item, and a verdict.

## Codex output (verbatim findings)

```
1. CLEAN

2. scripts/verify_lib.py:203 — check_css_syntax only treats backslash + LF as an
   escaped line continuation. CSS with CRLF line endings like "x\\\r\ny" is falsely
   reported as unterminated strings/unclosed brace, so valid CRLF CSS can fail the
   build gate.

3. CLEAN

4. CLEAN

5. src/planner/seed/api.py:48 + src/planner/seed/demo.py:32 — the API passes only
   clock.now_unix(), then demo reconstructs with the process-local timezone. This
   loses an explicit PLAN_FAKE_NOW timezone, so 2026-07-05T04:59:00-04:00 plans as
   July 4 via clock.now() but demo seeds July 5 on a Europe/London host, violating
   fake-clock/planning-date consistency.

VERDICT: FAIL
```

## Dispositions

- **Finding 2 (CRLF continuation) — accepted, FIXED.** `check_css_syntax` now treats
  backslash + CRLF as a legal string continuation (`scripts/verify_lib.py`), with a
  regression assertion added to `tests/unit/test_instrument.py`
  (`test_check_css_syntax_clean_and_each_failure_mode`). Re-verified: `./verify`
  36/36 PASS after the fix.

- **Finding 5 (timezone loss in demo date reconstruction) — acknowledged, not fixed;
  unreachable in every configured usage.** Unix seconds cannot carry a timezone, and
  the fix directive pinned `seed_demo(conn, now: int, ...)`. The divergence needs a
  PLAN_FAKE_NOW carrying an explicit UTC offset different from the host's, at an
  instant near the boundary hour. Nothing in the system does that: production uses
  RealClock (local-aware `now()`; `fromtimestamp(now_unix()).astimezone()` reconstructs
  the identical local instant, so the derived date always matches), and every test
  and e2e fake uses a naive local ISO (`2026-07-04T12:00:00`), which `parse_fake_now`
  interprets as local time — again reconstructing identically. §6.1 defines
  planning-date math over local time, which is exactly what the reconstruction uses.
  Flagged for the lead: if cross-timezone fakes ever become a supported mode, the
  demo should take the aware datetime (or the resolved date) instead of unix seconds.
