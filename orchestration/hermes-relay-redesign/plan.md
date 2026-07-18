# Hermes relay redesign — staged plan

Owner-approved direction (2026-07-17). Decisions: `D-hermes-relay-architecture`,
`D-runtime-neutral-pane-vocabulary`, `D-child-per-employee`, `D-stock-hermes-only`,
`D-transcript-ownership-open` in `decisions.md`.

## The shape

Panels' server stays the single thing the browser connects to (multi-tab fan-out, VPS
hosting, auth). It becomes a transparent relay instead of a translator: conversation frames
pass through to the browser in flight; Panels tees only the facts the planner needs (turn
settled/failed, session ids, optional audit copy). The database leaves the conversation's
path.

Upstream, each employee (each active ticket, plus the Chief) is its own child process
holding exactly one session (`D-child-per-employee`). Identity is the child's spawn
environment — Panels sets ticket id and actor at spawn; every shell inherits them; `panels`
reads them. Crossover is impossible by construction, stock Hermes suffices, and a future
Claude CLI / Codex CLI employee is just a different binary behind a thin adapter. No idle
reaping in v1: children live until their ticket closes or the server shuts down.

The chat pane speaks a runtime-neutral, ACP-shaped event vocabulary (turn started, text
delta, thinking, tool call, agent-asks-user, needs-approval, turn done/failed); only the
Hermes translator is built now.

```
browser tabs ──(neutral vocabulary)──> Panels server ──(stdio JSON-RPC)──> employee child
                                        │ relay: forwards frames,          (one per ticket
                                        │ rewrites request ids,             + the Chief;
                                        │ routes by employee/entity,        hermes now,
                                        │ tees lifecycle facts              claude/codex later)
```

Unchanged: tickets, gates, approvals, the resolution engine, the `panels` CLI surface,
skills provisioned into the Hermes home, one durable session per ticket employee.

## Stages

- **S0 — live protocol spike.** DONE, PASS — see `s0-spike.md`. (Ran against `hermes serve`
  over WS; the wire format is transport-identical over stdio, and respawn + `session.resume`
  with preserved context is exactly the child-restart recovery path.)
- **S1 — relay foundation.** Employee child pool (spawn on demand via the existing
  `GatewayChild` stdio transport, one session per child, per-child spawn env identity) plus
  the relay endpoint: per-tab request-id namespacing, routing by employee, tee seam.
  Config-gated off in production; no user-visible change; old role-children paths untouched.
- **S2 — native-vocabulary chat pane.** Rebuild the chat pane internals on the neutral
  vocabulary (Hermes translator at the relay), ticket + Chief chat: streaming, clarify,
  interrupt, commands, model picker. Swap only at parity. Open design point carried from
  S1: the relay denylists `slash.exec`/`cli.exec` as binding-capable escape hatches, so
  the pane's slash-command menu needs a mediated path (relay-injected or pool-mediated),
  not raw passthrough. S1's two trigger-bound limitations (final-frame-vs-death ordering,
  unbounded queues) are re-examined here, when real browser consumers arrive.
- **S3 — worker steps through the child pool.** Step prompts submitted to the ticket's own
  child; settlement/busy from teed lifecycle events plus Hermes's busy rejection; `panels`
  CLI identity reads the Panels-set spawn env instead of Hermes session env.
- **S3b — de-patch Hermes.** With one session per child, stock behavior is correct — revert
  the three local terminal-session-isolation commits (`D-stock-hermes-only`). Only after S3
  is live, or current-path workers break.
- **S4 — deletion and data.** Retire the session demux, DB-streamed turns, polling, and
  per-feature chat endpoints; re-anchor the Playwright suite to the new pane (largest test
  burden); transcript-ownership ruling lands here (`D-transcript-ownership-open`).

## Known risks and facts to design around

- A child dies with the Panels process (stdio): in-flight turns are lost mid-stream, as
  today; recovery is respawn + `session.resume` (proven in S0 — history and conversational
  context carry). The transcript remains durable in the home's `state.db` regardless.
- The stored-session store does not lock across processes (S0 phase 7): exactly one child
  may own a stored session; the pool enforces one-child-one-session, and until S2/S3 swap a
  surface over, pool children must not resume sessions owned by the live role children.
  S1 ships config-gated off; its tests use isolated homes.
- Respawn resets the child's shell environment (Hermes keys its shell snapshot to the
  process instance): accumulated exports/cwd from earlier steps are lost. The transcript
  carries what the worker did; treat lasting state the worker needs as files, not shell env.
- Stock-Hermes-only stands (`D-stock-hermes-only`): no Hermes-side changes; the local
  patches are reverted in S3b.
