# S0 spike record — relay design proven live (2026-07-17)

Verdict: **GO.** Every phase passed against a real `hermes serve` backend with a real model
turn. Script and raw outcome are in this directory (`s0_spike.py`, `s0-summary.json`).

## Setup

- Scratch `HERMES_HOME` (config.yaml + auth.json copied from `data/hermes-home`), fully
  isolated from the live Panels home and the user's `~/.hermes`.
- `HERMES_DASHBOARD_SESSION_TOKEN=<token> hermes serve --port 9911 --skip-build`.
  Readiness is one parseable stdout line: `HERMES_BACKEND_READY port=9911`.
- WS endpoint `ws://127.0.0.1:9911/api/ws?token=<token>` — same loopback token scheme the
  desktop app uses (`_SESSION_TOKEN`, `hermes_cli/web_server.py:274`). Without the token
  the upgrade is refused with 403 even on loopback.

## What was proven

1. **Connect** — first frame is `gateway.ready`.
2. **Create** — `session.create {source, cols}` → live id `804d51bb`, stored id
   `20260717_134720_a88cf5`.
3. **Prompt** — `prompt.submit {session_id, text}` responds `{status}` and streams
   `message.start → thinking.delta × n → message.delta × n → reasoning.available →
   message.complete` (final text exactly the requested word).
4. **Persistence** — `session.list` shows the stored session with source, preview, and
   message count.
5. **Hard drop** — socket closed with no `session.close` (simulated relay crash).
6. **Resume after drop** — new connection, `session.resume {session_id: stored}` returns
   full history (`messages`, `message_count: 2`, `resumed`, new live id) and a follow-up
   prompt proves conversational context carried: the agent repeated the earlier word.
7. **Concurrent-resume probe** — a second live connection may `session.resume` the same
   stored session without error: the server does not lock ownership. The relay design never
   does this (single upstream owner), recorded as a constraint, not a defect.

## Observed event vocabulary → neutral pane vocabulary

| Hermes frame | Neutral event |
|---|---|
| `message.start` | turn started |
| `message.delta` | text delta |
| `thinking.delta` / `reasoning.delta` | thinking delta |
| `reasoning.available` | thinking finished (expandable) |
| `message.complete` | turn done (final text) |
| `session.title` | session titled |
| `session.info` | session metadata |
| `clarify.request` / `approval.request` | agent-asks-user / needs-approval (not exercised in S0 — no tool use in a one-word turn; wire shape verified in source, exercised in S2) |
| `error` | turn failed |

Request ids are per-connection integers — the relay namespaces per-tab ids onto its one
upstream connection (plain bookkeeping, proven trivially by the spike client's own id table).
