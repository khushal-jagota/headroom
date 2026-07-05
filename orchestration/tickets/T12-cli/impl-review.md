# T12 implementation review — codex exec output + orchestrator dispositions

Files reviewed: src/planner/cli/http.py (new), src/planner/cli/main.py (handlers filled),
orchestration/tickets/T12-cli/smoke.py (new), against ticket.md, plan.md §8 amendments
A1–A4, SPEC §8, D4, and the live routers.

## Codex findings (verbatim, summarized)

**Violation 1 (the only one).** main.py:183 uses click's `envvar=PLAN_TICKET_ID` on the
optional `ticket_id` positional. If `PLAN_TICKET_ID=-`, click passes `ticket_id="-"`;
`read_body` then reads stdin even though the user did not explicitly pass `-`, and
`resolve_ticket_id` can return `"-"` from the env fallback. Correct fact: body stdin must
come only from an explicit positional `-` or `--body-file -`, and `-` must never be sent
as a ticket id. Affects propose/recap/note (stdin) and ticket show/set (id fallback).

**Confirmations.** Every http.send call matches the live mounted routes and request
fields (tickets, items, ideas, day, links, run, queues, seed). http.py preserves server
`{"error": ...}` envelopes, exits 1 for structured and non-structured non-2xx, implements
the A1 2xx-envelope check, and only `_fail_connection` exits 2; codex verified against the
installed httpx 0.28.1 that ConnectError/ConnectTimeout/ReadTimeout/PoolTimeout/ReadError/
RemoteProtocolError/ProxyError/UnsupportedProtocol are all TransportError subclasses.
A2/A3 output shapes implemented. No resolution verbs added; D4 layering respected
(http.py stdlib+httpx; main.py stdlib+click+local http; serve lazy imports intact).
smoke.py covers the full acceptance path with real serializer keys, PLAN_* scrubbing,
timeouts, tempdir-only writes, sound teardown. "NO OTHER VIOLATIONS".

## Orchestrator disposition

**Violation 1: ACCEPT — fixed by the orchestrator** (small fix per playbook), same
session:

- `main.py`: new `_positional_is_stdin_marker()` — the `-` positional counts as the stdin
  marker only when `click.get_current_context().get_parameter_source("ticket_id")` is
  `ParameterSource.COMMANDLINE`; an env-injected `-` no longer triggers a stdin read
  (`read_body` then exits 1 "body required", before touching stdin — no hang risk).
- `main.py` `resolve_ticket_id`: the env fallback now rejects the literal `-`
  (`if env and env != "-"`), so `-` can never be sent as a ticket id.
- `smoke.py`: new step 11 locks both behaviors — with `PLAN_TICKET_ID='-'` and piped
  stdin, `plan propose success --json` exits 1 with `error.code == "validation"` (stdin
  not consumed as body), and `plan ticket show --json` exits 1 client-side.

Re-verified after the fix (all fresh runs):
- `.venv/bin/ruff check src/planner/cli orchestration/tickets/T12-cli/smoke.py` — All checks passed!
- `.venv/bin/mypy src/` — Success: no issues found in 84 source files
- `.venv/bin/pytest tests/unit` — 81 passed in 1.35s
- `.venv/bin/python orchestration/tickets/T12-cli/smoke.py` — steps 0–11 all PASS, SMOKE OK

One further orchestrator-side edit outside the codex findings: the stale T01 module
docstring paragraph in main.py ("every handler except serve raises NotImplementedError…
lands in stage 4") was replaced with a current description of the http seam.
