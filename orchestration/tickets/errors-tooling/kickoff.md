# Errors tooling — kickoff

Not dispatched. Ruled in by the owner on 2026-07-26, to run **after the conversation's
three states**. This file holds the decisions so that starting it later costs a read
rather than a conversation.

## Why

There is no logging configuration anywhere in Panels. `logs_dir` is always empty. On the
VPS the planner's output reaches journald and nothing else. So when something fails, the
durable record of it is whatever happens to be on the ticket, and the answer to "why did
that fail" is to ssh in and read the machine's own log.

That is survivable today because the owner watches most of what runs. It gets worse with
autonomous scale, not better.

## What it builds

**One JSON-lines file under `data/`**, written through structlog, size-rotated with a
fixed number of files retained so disk is bounded and old entries age out.

**Ids bound at the boundaries**, so a line can be traced to what it belonged to: a request
carries its ticket id; a runner carries its session and step ids. Binding happens once, at
the edge, rather than being passed down by hand.

**Stderr tails on every failure path.** The 64KB tail already exists but is built for
backend-provenance only. It flows into these records on all failure paths.

**The existing stdlib log sites feed the same file** — roughly thirty-four of them today —
rather than continuing to scatter.

## Decided

- **Nothing surfaces in the interface.** The ticket already shows it is not moving; the
  file is for tracing why, when someone goes looking. This follows the standing ruling that
  durable significance lives on the ticket. No new screen, no per-agent error panel. Owner,
  2026-07-26.
- **About 500MB retained.** Generous on purpose: disk is cheap and being told the answer
  aged out is the failure mode that makes a log worthless. Owner, 2026-07-26.
- **Everything written unredacted** — prompts, agent output, stderr tails, whatever they
  contain. This is a single-person tool on the owner's own machine. Redaction costs real
  complexity and reliably removes the one line that mattered. Owner, 2026-07-26. **Revisit
  the day Panels has a second user**, and treat that as the trigger rather than a
  someday-maybe.
- **The file is canonical; stdout is left alone.** Duplicating into journald as well means
  two records that can disagree about what happened.
- **It records failures, plus the boundary events a failure needs to be legible** — the
  request that came in, the step that started. Not general chatter. A log nobody can read
  through is the same as no log.
- **Backends' own diary logs stay out of scope entirely.** We do not own those and should
  not pretend to.

## The error model this serves — already ruled, restated so it is not re-derived

- A **ticket error** is a ticket that is not moving forward. Outcome-based, not
  cause-based.
- **Agent-level troubles display on the agent**, not on the ticket.
- **Cancel changes nothing.** The status stays; the mismatch between status and liveness is
  the honest signal.
- **Busy is not an error.** Readiness checks occupancy before sending, slipped collisions
  queue, and the queue stays dumb — a rare race is accepted rather than engineered around.

## Gate

The package is not done because a file appears. It is done when **a failure leaves a
durable record that names what it belonged to** — proved by a test that fails if the
record is not written, not by one that checks the logger was called.

Watch that writing a record never blocks a write path. At this scale a synchronous append
is fine; say so in the implementation rather than leaving the next reader to wonder whether
it was considered.

## Sequencing

After the three states. The honest trigger for pulling it forward: the owner going looking
for why something failed and not being able to find out. Once that has happened twice it
has stopped being insurance and started being the thing in the way.
