# T21 implementation review — codex findings + orchestrator dispositions

Raw codex session log: `codex-impl-review-raw.txt` (verdict: VIOLATIONS: 4).

## Finding 1 — planning-worker.md overstated the claim-env rejection. ACCEPTED, FIXED

The doc claimed a write without `PLAN_RUN_ID`+`PLAN_CLAIM` is rejected `stale_claim`.
Server truth (§7.6, `authctx.py:46`, `tickets/api.py:291`): header-less proposal/recap
writes classify as a plain (non-dispatched) agent and are permitted; only heartbeat/
close hard-require the claim, and stale/foreign claims are rejected. Fixed in place:
the env bullet now says heartbeat and close require the lease, and lapsed/foreign
claims are rejected — the "never unset them" guidance stands.

## Finding 2 — result routing misstated (ceiling=done shortcut). ACCEPTED, FIXED

Both planning-worker.md and planning-executor.md described only the
`result → needs_review` path. Real behavior (SPEC §4.4.5, `machine.py:40`): with
ceiling `done` an accepted result lands directly in `done`, skipping review. Fixed in
both files: worker gains a "special case" bullet in the write-model list; executor now
states the two ceilings separately.

## Finding 3 — midnight race on step 9's day date. REFUTED (adjudicated in plan A1)

Plan amendment A1 explicitly pinned client-side real-today with no fallback and
documented the race as negligible: it requires a local-midnight crossing between
`seed --demo` and the script's step 9 within a single run (seconds apart in the e2e
test; same sitting in the standalone gate). Codex's proposed fixes are both out of
bounds for T21: making the demo seed honor the fake clock is a `src/` change
(forbidden), and "discovering" the demo date has no HTTP surface — widening the
script's read-only DB access beyond the single claim-token read would contradict the
documented one-purpose seam. Risk accepted as planned; noted for the integrator in
report.md.

## Finding 4 — working-tree changes outside the owned files. REFUTED (not T21's)

`tests/e2e/conftest.py` (additive `fake_now` parameter) and
`orchestration/orchestrator-playbook.md` were modified by the concurrently running
sibling tickets/team-lead, not by T21's implementer — the T21 diff is exactly the six
owned files, all new (`??` in git status), plus this ticket's orchestration artifacts.
Codex reviewed the whole dirty tree without ticket-ownership context. Nothing to
change in T21; the integrator serializes the sibling work.

## Post-fix gates

Findings 1-2 touched two skills files only; the full gate battery was re-run fresh
after the fixes (results pasted in report.md): dogfood e2e test green twice,
standalone script 13/13 PASS exit 0, ruff clean, all four skills ≤150 lines.
