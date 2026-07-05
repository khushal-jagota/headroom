# T17 plan review — codex output + orchestrator dispositions

Reviewer: `codex exec --sandbox read-only` against plan.md, ticket.md, SPEC §10/§5/§3.1/§3.2/§3.5,
decisions.md D11, PRINCIPLES.md, the T14 foundation files, sprints/api.py, sprints/views.py,
seed/demo.py, and the T14 smoke. Verdict as returned: FAIL (4 findings). Dispositions below;
amendments are binding and appended to plan.md §10.

## Codex findings (verbatim)

1. plan.md:43/:48-66 omits the `ticket` create-form variant and says adding it later is "one more
   entry." That contradicts decisions.md:65, where D11 item 13 is "Create form" with variants
   `ticket`, `item`, `idea`, and ticket.md:12, which makes T17 the owner of D11 item 13. This leaves
   part of the owned component contract unimplemented.

2. plan.md:437-444 says to adapt the T14 boot scaffold with a fake clock, then seed with
   `seed_demo`. T14's scaffold pins FAKE_NOW at T14 smoke.py:39, but `seed_demo` anchors the sprint
   to real wall-clock today at src/planner/seed/demo.py:29-30, while /api/sprint/current uses the
   injected clock at src/planner/sprints/api.py:323-326. That makes s1 structurally flaky: the
   seeded "current" sprint is only current if real today and fake today stay within the same
   generated sprint range.

3. plan.md:445-448 and :478-483 assume s4 has an "ample" pre-refetch window after freezing before
   the editor disappears. The WS tailer can deliver the freeze event as soon as its current poll
   wait expires (src/planner/core/ws.py:56-66), then the client schedules a flush after only the UI
   debounce (assets/api.js:101-103) and app.js reroutes on flush (assets/app.js:76-79). The click
   racing the re-render is therefore structurally flaky.

4. plan.md:449-456 and :510-511 make the smoke inject screens-sprint.js / screens-backlog.js
   instead of testing the real shell. The real _SHELL only loads through /assets/app.js
   (src/planner/core/server.py:57-61), and app.js leaves sprint/backlog as placeholders until
   screen scripts register over them (assets/app.js:26-31). The smoke can pass while the actual app
   still serves placeholders for the ticket's required #/sprint and #/backlog screens.

## Dispositions

1. **REFUTED (scope ruling).** The orchestrator dispatch for T17 pins the components.js addition to
   "D11 item 13 (Create form variants: item, idea)" — narrower than D11's full three-variant
   inventory line. No v2 screen consumes a `ticket` create form (SPEC §10: Board/Ticket define no
   create form; quick capture routes through the day chat + CLI per §10.1/§17), so building the
   variant now would be dead code with no consumer to test it against. FORM_SPECS is table-driven
   precisely so the `ticket` variant is a one-entry addition by whichever ticket first consumes it.
   Escalated in the T17 report as an open assignment question for the integrator, not silently
   dropped.

2. **ACCEPTED.** Amendment A1: the smoke runs on the REAL clock — `PLAN_TEST_MODE=1` stays (build
   flow parity) but `PLAN_FAKE_NOW` is NOT set; `build_clock` (core/clock.py:51-54) then returns
   RealClock, matching `seed_demo`'s wall-clock anchoring (today−3 … today+10 always contains the
   planning date, boundary shift included). s1 is deterministic.

3. **ACCEPTED.** Amendment A2: the pre-refetch window is made deterministic, not probabilistic.
   `ui_debounce_ms` is env-tunable (`PLAN_UI_DEBOUNCE_MS`, core/config.py:148); the smoke sets
   `PLAN_UI_DEBOUNCE_MS=1500` and `PLAN_WS_POLL_MS=250`. The debounce alone guarantees ≥1500ms
   between the freeze event and the earliest re-render; the Save click lands ~tens of ms after the
   freeze POST returns. Re-render ceiling ≈ 250+1500 = 1750ms, so post-write waits stay snappy;
   all wait_for timeouts ≥ 8000ms. The smoke asserts `meta.ui_debounce_ms == 1500` at boot so a
   silent config regression cannot reintroduce the race.

4. **PARTIALLY ACCEPTED.** The constraint is structural and stands: `_SHELL` lives in
   src/planner/core/server.py, a file shared with T15/T16 and outside every UI ticket's owned list —
   editing it concurrently is the exact two-writers hazard the playbook forbids, so wiring the two
   `<script>` tags is a serial-integration step (explicit request in the T17 report), and the
   integrated shell is asserted by the T18 e2e suite (item 32). Amendment A3 closes the gap codex
   identified as far as this ticket can: the smoke GETs `/` first and only injects the screen
   scripts when the served shell does NOT already reference them — so the same smoke re-run
   post-integration exercises the real shell path with zero injection, and it prints which mode it
   ran in.
