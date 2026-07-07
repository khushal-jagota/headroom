# The command-line tool

`plan` is the command-line tool. It is for AI workers and for developer debugging;
the human plans through the web page. Every verb speaks to the server over HTTP and
answers in machine-readable JSON (`--json`). Long text always comes from a file or
standard input, never as an inline argument.

Its most important property is what it _cannot_ do: there is **no verb for accepting,
approving, granting, or unblocking anything**. Those decisions live only in the
human's web page. A worker can propose and record; it can never resolve.

## The verbs

- **`propose <field>`** — file or replace a proposal on a blank (`success`,
  `approach`, `plan`, or `result`). **`recap`** — overwrite the ticket's running
  summary. **`note <field>`** — leave durable guidance next to a blank without
  touching it.
- **`ticket create / show / list / set`** — manage tickets. `set` covers priority,
  deadline, day placement, and sprint placement only — scope (ceiling and at-cap) is
  never settable here, because that is a human grant.
- **`item create / show / list / set / propose-status`** — sprint items, including
  proposing a done-or-deferred status for the human to accept.
- **`sprint show`**, **`idea create / list`**, **`day show / add-ticket /
  remove-ticket`**, **`link add / rm`** — read and edit those objects.
- **`queue approvals / overdue`** — read-only views of what waits on the human and
  what's past its deadline.
- **`serve`** — run the server in the foreground.

_Code paths:_ `src/planner/cli/main.py` (the verbs), `src/planner/cli/http.py`
(the HTTP call, output, and exit codes).

## What used to be here and isn't

Earlier documentation listed verbs that belonged to the old dispatcher-and-claim
machinery, now removed. They no longer exist: **`run heartbeat` / `run close`** (a
dispatched worker keeping a lease alive and reporting its outcome), **`queue
pickup`** (what the dispatcher could take next), and **`plan seed`** (the markdown
importer). A worker no longer holds a claim or a lease; the employee runtime runs one
step at a time and writes status itself (see `employee-runtime.md`).

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the proposals, recaps, and notes
  this tool files, and the scope the server enforces on them.
- **The employee runtime** (`employee-runtime.md`) — the worker that drives this tool.

## Deferred

- **No importer verb.** The old `plan seed` markdown importer is gone; bringing data
  in from the old markdown planner has no command today. Trigger: a decision to
  support import again.

---

_Last verified: 2026-07-07._
