# T04 implementation review — codex output + orchestrator dispositions

## Orchestrator spot-check fixes applied BEFORE the codex run

Reviewed by hand (resolution.py, machine.py, admission.py, data.py) against plan.md as
amended; two small fixes applied by the orchestrator (playbook-permitted small fixes),
gates re-run green:

1. `machine.resolve_grant` detected the `"none"` sentinel with
   `not isinstance(next_ceiling, TicketState)`, which would have silently treated any
   runtime junk string from an unvalidated caller as "no further" — a silent grant on the
   most load-bearing function. Now: exact `== NO_FURTHER` check; any other
   non-`TicketState` value raises `grant_invalid` ("unknown next_ceiling").
2. `decide_drop`'s re-drop rejection reused the message "done tickets cannot be dropped"
   with detail `{"state": "dropped"}` — misleading text. Message now
   "ticket is already dropped"; code (`validation`) and detail shape unchanged (the plan
   pinned shape, not message wording).

## Codex run

First `codex exec` attempt hung for 45 minutes with no output and was killed; relaunched
with a tighter prompt (same law, same eight scrutiny areas), completed normally. Verdict:
seven of eight areas PASS with file:line citations, one violation.

- 1 Grant pair (§4.4.7): PASS — machine.py:75, resolution.py:162, data.py:207.
- 2 Result routing (§4.4.5, pre-accept ceiling both paths): PASS — machine.py:40,
  resolution.py:54.
- 3 Supersede/accept event ordering: PASS — resolution.py:55,125, data.py:95.
- 4 Recap gate per amendment A1: PASS — admission.py:63, test_tickets_engine.py:361.
- 5 At-cap matrix (§4.3): PASS — admission.py:33,38,40,51.
- 6 Single door / sole constructors: PASS — data.py:76, resolution.py:37,41.
- 7 Test fences: VIOLATION (should-fix) — test_a07's ceiling=done leg asserted
  `("in_progress","done") in seq` plus a no-needs_review guard instead of the exact
  state-change sequence.
- 8 logic/ purity: PASS — admission.py:7, fields_codec.py:8, machine.py:7,
  resolution.py:11.

## Dispositions

Finding 7 — **ACCEPTED and fixed.** The membership assertion matched plan.md §9's own
pinned wording, so the implementer was faithful; but the exact-sequence assertion is
strictly stronger, deterministic here, and closer to the §18.3 fence discipline ("exact
states, exact orderings"). test_a07 now asserts the full sequence
`[(needs_success,needs_approach), (needs_approach,needs_plan), (needs_plan,in_progress),
(in_progress,done)]`, which subsumes both prior assertions. Test-change justification (per
the §18.3 fence rule) recorded here and in report.md for the integrator to log in
decisions.md: strengthening only, no assertion weakened, prompted by codex impl review.

Re-verified after the fix: ruff clean on owned paths; 9/9 tests green via
`.venv/bin/pytest tests/unit/test_tickets_engine.py -q`; zero mypy errors attributable to
tickets files (the 3 repo-wide mypy errors live in src/planner/dispatch/logic/, T05's
in-flight territory).
