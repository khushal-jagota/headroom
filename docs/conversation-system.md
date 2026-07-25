# The conversation system (new)

This is the new home for talking to an AI agent. One conversation = one agent
process (hermes, codex, or claude) working in a folder, plus a permanent notebook
of everything that happened in it. The rest of the planner can do exactly five
things to a conversation — start it, send text into it, interrupt its running
turn, kill its activity outright, and ask whether it is running — plus one more
question: is a permission ask waiting. Nothing else crosses the boundary.

It currently serves the development pane at `#/dev/conversation`. Production
screens still run on the old conversation layer (`chat.md`); this system replaces
that layer when the swap is ruled.

```
  caller (pane, loop)                the conversation system                agent CLIs
  ───────────────────                ───────────────────────                ──────────
  start / send / interrupt   ──▶   one core: queue, turns, asks,   ──▶   hermes (ACP)
  kill / is-running / ask?          notebook, janitor                     codex (app-server)
                                          │                               claude (Agent SDK)
  read: events after N  ◀──   conversations + conversation_events
        + live tail                 (SQLite, written once)
```

## The notebook

Every conversation owns an append-only run of numbered rows in the database —
its notebook. A row is a finished thing: a prompt that was actually delivered
(who sent it and how), a completed agent message, a tool call starting, a tool
call finishing, a permission ask, its answer, a model change, a discarded held
message, a turn ending (completed, failed, or interrupted). Rows are written
once and never edited. Streaming output (the text growing word by word) is live
decoration only — it is never stored, and the agent's private reasoning is
dropped entirely, not stored and not shown.

Reading is one rule everywhere: fetch the rows after the last one you hold, then
listen for new ones. Opening a conversation, reconnecting after a dropped
connection, and a second device are all that same fetch. Nothing re-downloads
mid-read.

## Sending

Send has one knob with three settings. The default runs the message when the
agent is free — if it is busy, the message waits in line. "Send now" makes the
message the running turn: a busy agent's current turn is stopped (recorded
honestly as interrupted) and the new message runs next, ahead of the line.
"Steer" injects text into the running turn without ending it — only hermes can
do that.

The answer to a send is the fate of that delivery, and fate means it happened:
started (the text reached a live agent), queued at a position, injected, or
refused with a named reason. The only refusals are genuine impossibilities — no
such conversation, the agent would not start, its session would not load, the
write failed. A busy agent is never a refusal. How a turn later ends is never
part of the answer — endings are notebook rows.

A send may also carry a model or reasoning-effort change. The change rides the
message: browsing a picker does nothing, the change lands when the message is
delivered, a waiting message applies it when it runs, and a refused delivery
changes nothing. Codex and hermes take the change in place; claude is restarted
under the same conversation with its memory carried over.

## Permissions

When an agent asks permission, the ask always shows and always waits — nothing
in this system ever answers for you, for any tool, after any amount of time. The
choices on the card are the backend's own choices (plus "cancel the turn", which
is ours). An "always allow" answer is passed to the agent's vendor, who does all
remembering — this system keeps no grant state at all. Ask and answer are both
notebook rows; an answer only lands on an ask that is still waiting on the live
turn, and an unanswered ask dies — visibly — when its turn ends.

## Processes, honestly

One child process per conversation, started only when a send needs it. The
agent's own session handle is stored on the conversation row, so a later send
can bring the same memory back. After a server restart nothing is running and
the system says so — there is no pretending, and no machinery that quietly
reattaches. An agent idle for half an hour is stopped silently (its memory is
already saved); the next send brings it back without anyone noticing. A resume
that did not actually restore the agent's memory is refused out loud — never
silently accepted as a fresh brain behind an old transcript. A failed turn
writes one error-log line with the ids and the tail of the process's stderr.

Held messages live in memory only: a server restart loses whatever was still
waiting in line (the notebook keeps what was delivered or discarded). Kill is
the loud version of stopping: it ends the running turn and throws away the
waiting line, writing a discard row for each thrown-away message, because text
someone handed over must never vanish without a trace. Pressing New in the pane
kills the old conversation before starting fresh.

## Backend cards

The system can describe each agent CLI as a card: is the binary installed and
what version, who is logged in (where the CLI will say without a network call),
which models it offers, and whether a newer version exists — with a one-click
update whose command is inferred from how the CLI was installed, re-checked
afterwards, and reported as succeeded, unchanged, or failed. All of it is
advisory; nothing blocks on it. Logging in stays in the terminal, and the card
names the command.

## Code paths

- Contract and floor defaults: `src/planner/conversation2/contracts.py` (the
  docstrings are the documentation of record).
- Core, notebook, storage: `src/planner/conversation2/` (`system.py`,
  `events.py`, `storage.py`); tables land in
  `src/planner/core/migrations/versions/conversation_system_tables.py`.
- The three backends: `src/planner/conversation2/backends/`.
- Reading side, live tail, backend cards: `src/planner/conversation2/api.py`,
  `live_tail.py`, `snapshot.py`.
- The pane: `web/src/routes/DevConversationRoute.svelte`,
  `web/src/components/conversation2/`, `web/src/lib/conversation2/`.
- The contract's proof: `tests/support/conversation_contract_conformance.py`,
  run against the real system in
  `tests/unit/test_conversation2_conformance.py`.

## Handoffs

- The old conversation layer still serves production screens: `chat.md`.
- Ticket-side surfacing (which ticket needs you, row dots) is the worker
  orchestration's job, built against this contract: `employee-runtime.md`.

## Deferred

- **Swap**: production screens and the worker loop move onto this system when
  the program rules the swap; the old layer and this doc's "dev pane" framing
  are corrected in the same breath.
- **Held-line durability**: if losing the in-memory waiting line on a restart
  ever bites, the line can be made durable without changing the contract.
- **Error envelope**: the conversation routes speak plain HTTP errors, not the
  planner's error envelope; unify when the swap wires production screens.

_Last verified: 2026-07-25._
