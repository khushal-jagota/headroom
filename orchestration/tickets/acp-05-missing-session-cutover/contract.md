# ACP-05 missing-session cutover correction

> **CANCELLED by owner decision on 2026-07-19.** This was a one-time migration artifact, not a
> product compatibility requirement. No source or test change from this ticket is to be implemented.
> ACP-05 instead starts a fresh conversation through the existing Panels control and proves ACP on
> that clean binding. ACP-06 will deliberately replace the remaining legacy bindings rather than
> teach the permanent runtime to recover legacy `planner-chat` session IDs. The material below is
> retained only as the diagnosis that led to that decision.

## Why this ticket exists

Real computer-use dogfood on the production-served Panels Workspace exposed one exact cutover
failure. The Chief attached as ready to the backfilled durable binding
`20260710_212941_937a6f`, accepted a browser prompt, published a human echo and `started` receipt,
then returned to `idle` without an agent response. A second raw ACP attach proved that
`session/load` emitted only `reset -> ready` and replayed no history.

The stored Hermes row is source `planner-chat`. Hermes's read-only ACP adapter deliberately restores
only rows whose source is `acp`, so its successful `session/load` result is JSON `null` for this
legacy ID. Panels currently types every load as non-null and publishes the unchanged binding. That
makes a session which the backend says does not exist look ready and lets prompts appear to start.

This ticket corrects only that boundary. It does not edit the Hermes checkout, delete legacy code,
redesign Panels, or broaden ACP-05 dogfood.

## Frozen behavior

1. `AcpEmployeeChild.load_session` tells the truth: a successful backend response may be
   `LoadSessionResponse | None`. `None` means the named session does not exist in that backend. A
   raised exception still means load failed.
2. On first initialization, ordinary refresh attach, or CAS-winner adoption, a non-null durable
   binding whose load returns `None` is replaced deliberately on the same initialized child:
   `session/new` -> candidate with the same employee/backend and exactly `old generation + 1` ->
   durable compare-and-swap -> publish only the committed winner. The old binding is never
   published ready and never receives a prompt.
3. A raised load error does **not** mint a session, preserving ACP-01's fail-closed recovery rule.
   A null response is the backend's positive not-found result, not a transport failure.
4. CAS races remain exact. If another writer wins, the candidate child/session is not published;
   the registry adopts the complete durable winner and applies the same null-vs-error rule. No
   recursion or retry loop may spin indefinitely.
5. Controlled compaction capture must preserve its session. A null capture load is fatal and must
   not create a different conversation.
6. The hub detects that attach replaced the binding it initially resolved. Its ready state carries
   the precise detail `Started a new conversation because the previous session was unavailable`.
   The existing single status line shows that detail while the replacement has no activity, then
   yields normally to thinking/working/permission/idle activity. No modal, card, toast, or layout
   change is allowed.
7. A browser prompt after the transition uses only the replacement session ID, reaches the official
   child, emits typed updates, and settles. Refresh loads that replacement binding without another
   generation change.

## Required proof before the fix is accepted

- Unit registry proof for null initial load, null refresh attach, null winner adoption/CAS race,
  raised-load no-remint, candidate cleanup, and null capture failure.
- Official-SDK subprocess proof whose scripted agent returns JSON `null` for a seeded legacy
  session, then records exactly one new session and receives the subsequent prompt on it.
- Hub/WebSocket proof for old generation -> successor generation, reset/replay/ready ordering,
  replacement detail, prompt delivery, and stable refresh.
- Runtime Svelte component proof that the existing status line exposes the replacement detail and
  gives way to activity without introducing another status element.
- Focused Ruff, strict Mypy, exact Python suites, ACP web suites, Svelte diagnostics, and production
  build. Do not run canonical `./verify`; ACP-10 owns the one final run.

## Allowed files

- `src/planner/conversation/backend_contracts.py`
- `src/planner/conversation/sdk_child.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/hub.py`
- `web/src/components/acp/ConversationStatus.svelte`
- `tests/unit/test_acp_employee_child.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_conversation_hub.py`
- `tests/e2e/test_acp_conversation.py`
- `tests/support/acp_scripted_agent.py`
- `web/tests/acp-browser-components.test.mjs`
- this ticket's plan/report/review/evidence files
- `PROGRESS.md`
- `decisions.md`

No other source, test, generated distribution, config, runtime database, or Hermes-checkout file is
in scope.
