---
name: panels-worker
description: Working a Panels ticket, one step at a time.
---

# Working a Ticket

You work one Ticket, one Stage at a time. You're given the current Stage only; do
that work, then propose what you've done. Your job is only ever the
single Stage in front of you.

## Your ticket and your specialist
Which Stages a Ticket has, and what each needs, depend on its Worker type. Run
`panels worker my-ticket` — it names your worker skill and reports the current
Stage, effective ownership, and scope. Invoke that skill.

### Who owns the current Stage

Every non-terminal Stage has a default ownership mode. A Ticket can override that
default for one Stage; the current Stage's override wins, otherwise its default applies.
Ownership says who drives the current Stage. It is separate from Ticket scope.

- **Worker** — the Employee may be discovered and run automatically when every other
  eligibility condition also allows it. Scope still decides whether a proposal is
  accepted below the ceiling, parked for the user's approval through **Propose** at the
  ceiling, or prevented by **Stop**.
- **User** — the Ticket rests at `user` and is never dispatched automatically.
- **Paired** — the Ticket gets one automatic opening turn for the current Stage, then
  rests at `paired` and is never started automatically again. Ordinary Ticket Chat is
  the continuation path: the user's
  message reaches this Ticket's durable Employee session and worker context. When clarity is reached, the worker can propose.

**Take over** sets an explicit `user` override on the current Stage, including while a
run is active. **Release** clears that current-Stage override; it does not reveal an
older override, and the Stage default applies again. Respect the Ticket's effective
ownership. Do not treat Take over, Release, or an owner change as a scope change.

### The CLI

Everything runs through the `panels` command — `panels --help` for full usage. The tools you use:

- **`panels worker my-ticket [part,part]`** — read your Ticket header and part
  manifest, or expand named saved fields, `proposal`, `recap`, `guidance`, and `archive`. The header says who you are, the current
  Stage, effective ownership, and scope.
- **`panels ticket show <id> [part,part]`** — read another Ticket's header and part
  manifest, or expand named saved fields, `proposal`, `recap`, `guidance`, and `archive`.
- **`panels ticket ownership <id> --stage <stage> --mode worker|user|paired|default`** —
  set or clear a Stage ownership override when the user directly instructs that change.
- **`panels worker propose <id> --recap "…"`**, piping the proposal text on stdin — propose your own Ticket's current gated field. No supervisor, holder Ticket, or other Worker can file it for you. The body arrives on stdin only, and every proposal must also set a recap with `--recap TEXT`.
- **`panels worker recap <id>`**, piping the recap text on stdin — update the running recap outside a proposal.
- **`panels worker trouble`**, piping the note on stdin — record one short trouble note on your
  current Ticket during the active claimed worker step. Use it for a harness, tool, or
  Ticket problem that did not go well. Record only trouble that you encountered. Do not
  grade yourself or record what went well.
- **`panels worker request-help [ticket-id]`**, piping the help message on stdin — send one canonical addressed message when you cannot responsibly continue without important input. The current ceiling holder is the default recipient. Use exactly one of `--owner`, `--chief`, `--ticket <id>`, or `--sprint-item <id>` only when another principal must answer. The message drives the shared unread-reply attention fact. Do not use this for ordinary discussion, proposals, approvals, permission prompts, Stop, or confirmed Worker errors.
- **`panels worker note <id>`**, piping the guidance text on stdin — replace the Ticket’s durable guidance document. Add `--append` to preserve the existing guidance and add new text.
- **`panels send-message --owner --message "…"`** — send one addressed chat message to
  the owner through this Ticket's current conversation. Use the same command with exactly
  one of `--chief`, `--ticket <id>`, or `--sprint-item <id>` to message another employee.
  This is conversation, not a substitute for a canonical Ticket, Day, or Sprint action.
  Your ordinary turn-end prose is runtime-only; use this command when another principal
  must receive a message.

All four write commands take their text on stdin only; there is no file-path option, since it once let two Workers sharing one `/tmp` overwrite each other's text before it reached the ticket. Pipe or redirect text in, for example `echo "…" | panels worker propose <id> --recap "…"` or a heredoc into stdin.
- **`panels ticket create --worker-type <id> --title "…"`** — create a Ticket when the
  current approved step spins off a new one. Before creating it, load and follow
  `panels-ticket-creation`; this Worker skill still owns the current Stage's authority
  and approved scope.


## How to complete this effectively

### Cross-cutting disciplines

- **Treat deployed apps as immutable.** Repository work must happen in a Git checkout
  under the assigned workspace, normally in an isolated worktree. Never edit or run
  tests from `~/Deployments/Panels/current/app` or another deployed app artifact. If
  the source checkout cannot be found, request user help instead of changing the
  running installation.
- **Do not over-specify fields.** 
- **Explain your proposal judgment in chat.** After you propose a gated field, your chat reply should very briefly explain why you shaped the proposal that way. Do not merely announce that the field is ready, repeat which field you proposed, or restate approval/status details, the UI already shows this. 
- **Use recap as cold-user orientation.** The recap is not a work log. Keep it short and scannable, so a cold user can read it alongside the title and understand what the ticket is and what was done before this proposal to refresh their mind before reviewing this proposal.
- **Preserve direct user guidance.** When the user gives direction during a worker step that should survive the turn, add it to the Ticket guidance with `panels worker note <id> --append`. Keep this document for user direction, not a work log. Automatic step prompts include current guidance; saving it does not send a chat message. Read it with `panels worker my-ticket guidance` when continuing another conversation turn.

### Ticket-owned planning artifacts

Ticket-owned artifacts are durable work products that make the work easier to understand; they are not a reason to bloat a gated field. Store them in the live managed tree at `/home/vps/Deployments/Panels/current/data/files/tickets/<ticket-id>/<relative-path>` and link them from the relevant proposal, note, implementation, or closeout as `/files/tickets/<ticket-id>/<relative-path>`. For example, write `/home/vps/Deployments/Panels/current/data/files/tickets/<ticket-id>/artifacts/ui-plan.html` and link it as `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`. Never use a source checkout's `data/...`, a ticket worktree's `data/...`, or another path inferred from the current directory.

### Preview servers you leave running

When a Ticket's work is a running preview server, record it as an ordinary Markdown link to `/dev/tickets/<ticket-id>/<port>/`, for example `[take one](/dev/tickets/t_t8bu42wk/8791/)`. Panels serves that address and the user can open it. Never record `http://127.0.0.1:<port>/` or `http://localhost:<port>/`: that address is true only on this machine, and the user browses from another device. A recorded link goes out of date when the port dies, like any other stored value.

For frontend changes, normally include a small HTML planning artifact that shows the intended layout, important states, and interactions before implementation starts. When the ticket's purpose is to experiment in HTML to discover the design, that HTML is the exploration and work product; do not require a second planning artifact first. Apply this as judgment-based guidance, not a mechanical gate: create an artifact when seeing the thing will materially improve planning, approval, or execution.
