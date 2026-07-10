# t_hs04 implementation report

## Result

The temporary product drain boundary is removed. Production has one child-wide ordered Hermes event
feed per role child, claimed by `LiveSessionManager`. Human and employee callers consume only the
consequence registered for their accepted operation. The standalone runner and smoke tool claim the
raw child feed only because each owns its child, and both reject cross-session delivery.

Removed surfaces include `SessionEventStream`, `GatewayChild.open_session_events`, the gateway fanout
registry, `SharedGateway._submit_and_drain`, the obsolete `_stream_prompt` compatibility name,
receipt-only `LiveSession.submit`, session-wide `LiveSession.next_observation`, and the generic session
observation queue.

## Acceptance coverage

- An AST contract limits raw ingress claims to the session manager, standalone runner, and smoke tool,
  and rejects reintroduction of the removed product drains.
- The one raw feed preserves stdout order, excludes process-only events, is single-claim, and wakes on
  child death.
- Public gateway tests cover idle detach/resume, accepted/queued/unknown shutdown, role-child death and
  sibling survival, restart/resume without replay, and two isolated employee sessions on one child.
- Existing Stop-then-immediate-send, queued employee, command, image, activity, worker-context, and
  transcript behavior remains covered without frontend changes.

## Focused verification

The final focused gateway, session, chat, employee-runner, loop, and role-wiring gate passed 157 tests.
Ruff passed. Mypy passed across 104 source files. `git diff --check` passed.

## Authoritative verification

The one final integrated `PYTHONPATH="$PWD/src" ./verify` run passed:

- Ruff: passed.
- Mypy: passed across 104 source files.
- Unit suite: 391 passed, with three existing warnings.
- Compile/static checks: passed.
- Frontend check/build/test: passed, with the existing three Svelte warnings.
- Browser suite: 57 passed.
- Final result: `VERIFY: PASS`.

This captured run preceded the final review-only fixes. After both independent follow-ups returned
`NO VIOLATIONS`, the parent ran the required post-review integrated gate. It passed Ruff, Mypy across
104 source files, 394 unit tests, compile/static and frontend gates, 57 browser tests, and ended with
`VERIFY: PASS`. The original complete captured output is retained below as the ticket's full
authoritative transcript.

```text
=== /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/.venv/bin/ruff check . ===
All checks passed!

=== /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/.venv/bin/mypy src/ ===
Success: no issues found in 104 source files

=== /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/.venv/bin/pytest tests/unit --junitxml=/Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/data/verify/unit.xml ===
........................................................................ [ 18%]
........................................................................ [ 36%]
........................................................................ [ 55%]
........................................................................ [ 73%]
........................................................................ [ 92%]
...............................                                          [100%]
=============================== warnings summary ===============================
../planning-v2/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  /Users/khushaljagota/.hermes/planning-v2/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

src/planner/core/clock.py:27
  /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/src/planner/core/clock.py:27: PytestCollectionWarning: cannot collect test class 'TestClock' because it has a __init__ constructor (from: tests/unit/test_core_loops.py)
    class TestClock:

src/planner/core/clock.py:27
  /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/src/planner/core/clock.py:27: PytestCollectionWarning: cannot collect test class 'TestClock' because it has a __init__ constructor (from: tests/unit/test_ticket_readiness_loop.py)
    class TestClock:

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
391 passed, 3 warnings in 9.04s

=== /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m compileall -q src/ ===

=== css check /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/assets/app.css ===

=== node --check /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/assets/markdown.js ===

=== css check /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/assets/tokens.css ===

=== npm --prefix web run check ===

> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/web
Getting Svelte diagnostics...

/Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/web/src/routes/TicketRoute.svelte:30:51
Warn: This reference only captures the initial value of `id`. Did you mean to reference it inside a closure instead?
https://svelte.dev/e/state_referenced_locally (svelte)

/Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/web/src/routes/TicketRoute.svelte:39:61
Warn: This reference only captures the initial value of `id`. Did you mean to reference it inside a closure instead?
https://svelte.dev/e/state_referenced_locally (svelte)

/Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/web/src/routes/TicketRoute.svelte:46:42
Warn: This reference only captures the initial value of `id`. Did you mean to reference it inside a closure instead?
https://svelte.dev/e/state_referenced_locally (svelte)

====================================
svelte-check found 0 errors and 3 warnings in 1 file

=== npm --prefix web run build ===

> build
> vite build

vite v6.4.3 building for production...
<script src="/assets/markdown.js"> in "/index.html" can't be bundled without type="module" attribute

/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
11:45:14 AM [vite-plugin-svelte] src/routes/TicketRoute.svelte:30:50 This reference only captures the initial value of `id`. Did you mean to reference it inside a closure instead?
https://svelte.dev/e/state_referenced_locally
28:   let { id }: { id: string } = $props();
29:
30:   const ticket = resource<TicketDetail>(`ticket:${id}`, (signal) =>
                                                        ^
31:     fetchJson(`/api/tickets/${id}`, { signal })
32:   );
11:45:14 AM [vite-plugin-svelte] src/routes/TicketRoute.svelte:39:60 This reference only captures the initial value of `id`. Did you mean to reference it inside a closure instead?
https://svelte.dev/e/state_referenced_locally
37:     fetchJson("/api/projects", { signal })
38:   );
39:   const chatStatus = resource<GatewayStatus>(`chat-status:${id}`, (signal) =>
                                                                  ^
40:     fetchJson(`/api/chat/${id}/status`, { signal })
41:   );
11:45:14 AM [vite-plugin-svelte] src/routes/TicketRoute.svelte:46:41 This reference only captures the initial value of `id`. Did you mean to reference it inside a closure instead?
https://svelte.dev/e/state_referenced_locally
44:   );
45:
46:   const ticketInvalidations = [`ticket:${id}`, "board", "queues", "sprint:current"];
                                               ^
47:   const emptyTicketFieldText = "Not written yet.";
48:   const emptyTicketRecapText = "No recap yet.";
✓ 143 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.75 kB │ gzip:  0.37 kB
dist/assets/index-CIWvaqnW.css    0.05 kB │ gzip:  0.07 kB
dist/assets/index-CCoueoKI.js   137.55 kB │ gzip: 46.86 kB
✓ built in 528ms

=== npm --prefix web test ===

> test
> node tests/event-mapping.test.mjs

=== /Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/.venv/bin/pytest tests/e2e --junitxml=/Users/khushaljagota/.hermes/planning-v2-hermes-session-ingress/data/verify/e2e.xml ===
.........................................................                [100%]
57 passed in 74.48s (0:01:14)

[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok

VERIFY: PASS
```
