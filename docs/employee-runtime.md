# The employee runtime

Each Ticket has one employee conversation that can carry work across Stages. The
runtime decides when an Automatic Employee step may begin, claims exactly one step,
sends the real prompt through ACP, and settles the Ticket from the result.

Automatic discovery and execution are separate:

```
AutomaticEmployeeStepDiscoveryLoop        EmployeeStepRunner
read today's Ticket membership            claim under one SQLite write lock
apply the complete eligibility rule  ───► create one correctness-only run
hand eligible ids to the runner            send through AcpStepGateway
                                             │
commit → best-effort wake ◄──────────────────┘
SQLite and the periodic timer remain canonical
```

## Eligibility and the final claim

Discovery is read-only. It scans Tickets on the current planning day and passes only
eligible ids to the runner. A Ticket is eligible when all of these current facts hold:

1. It belongs to the supplied planning day.
2. No `employee_step_runs` row is currently running for it.
3. Its Stage is not terminal and has a next gated field.
4. Its effective Stage owner is `worker`, or it is a `paired` Stage that has not
   already received its opening step.
5. Its Ticket status matches that ownership state.
6. No proposal is already parked on the gated field.
7. Scope permits work at the current ceiling.
8. The Ticket has no active blocker.
9. If the Stage gates Closeout, no Ticket in the same effective-project and Worker-type
   lane is already at Closeout with a non-empty status. A parented Ticket uses its sprint
   item's project. Projectless Tickets share one projectless lane per Worker type.

Discovery orders candidates by oldest `updated_at`, then Ticket id. Ordinary Stages keep
their existing behavior. For Closeout, one poll submits at most one waiting Ticket from
each free lane.

The runner repeats the same decision under `BEGIN IMMEDIATE`. A stale discovery result
therefore cannot claim the Ticket, create a run, or contact an agent. The transaction
changes the Ticket to `agent_running_step`, creates the Employee-step record, and writes
the `employee_step_started` event before the worker prompt begins.

Eligibility-affecting actions commit first and then send a payload-free, best-effort
wake. The periodic discovery timer and SQLite state are still the backstop, so a lost
same-process wake cannot lose work.

_Code paths:_ `src/planner/runtime/automatic_employee_step_eligibility.py`,
`src/planner/runtime/automatic_employee_step_discovery_loop.py`, and
`src/planner/runtime/automatic_employee_step_eligibility_wake.py`.

## The correctness record

`SqliteEmployeeStepRepository` is the only SQL owner of `employee_step_runs`. A row has
exactly:

- `employee_step_id`
- `ticket_id`
- `status` (`running`, `complete`, `interrupted`, or `errored`)
- `employee_session_id`
- `error`
- `started_at`, `updated_at`, and `completed_at`

The table permits at most one running row per Ticket. Start, restart replacement,
session binding, first-wins settlement, stale cleanup, and running guards all go
through the repository. Ticket deletion cascades terminal run history and is rejected
while a run is active.

This row is product correctness state, not conversation state. It never stores a
prompt, reply, transcript, usage, activity, image, tool, clarification, or browser
projection.

_Code path:_ `src/planner/runtime/employee_step_repository.py`.

## Prompt delivery through ACP

`EmployeeStepRunner` builds the Stage-specific instruction and calls the ACP-only
`StepGateway` port. Production composes that port as `AcpStepGateway`.

The gateway opens or resumes the Ticket's durable binding through the same
`ConversationHub`, `AcpEmployeeRegistry`, and `ConversationTurnBroker` used by the
human pane. Its session callback binds the exact ACP session to both the Ticket mirror
and the running Employee-step record before prompt admission can complete. If another
owner won, the current run does not send through an unowned session.

On the first unbound demand, the registry creates a session and applies the Ticket's
explicit launch Model first, followed by its explicit Reasoning effort when supported.
Only then may it publish the first binding and admit the prompt. The binding write also
proves that the Ticket's complete launch setup has not changed while the session was
being prepared. An unavailable explicit choice retires the candidate session without a
binding or prompt.

Pending worker context is a separate durable service. The gateway prepares its exact
text into the model prompt, admits that prompt through ACP, and only then acknowledges
the included context revisions. A pre-admission failure keeps those revisions pending.
The worker sees the context because it is in the actual ACP prompt, not because Panels
wrote an event, database transcript, or UI row.

Human prompts and Automatic Employee prompts therefore share one durable backend
conversation. The broker serializes their delivery choices; the runtime does not use
a separate raw child, pool, relay, or transcript mirror.

_Code paths:_ `src/planner/runtime/acp_step_gateway.py`,
`src/planner/runtime/step_gateway.py`, `src/planner/conversation/`, and
`src/planner/worker_context/`.

## Settlement and restart

The gateway returns terminal status, employee session id, error, and whether a failure
was confirmed by the backend Worker or arose in Panels' conversation machinery. The
runner does not receive transcript text. It settles the exact running record once and
then applies the Ticket outcome:

- a completed worker turn lets proposals and the resolution engine determine the
  next resting status;
- an interrupted turn leaves an interrupted correctness record and releases the Ticket;
- every errored turn records its error in the correctness row, but only a failure
  explicitly confirmed by the backend Worker makes the Ticket `errored` and stores its
  exact text as `backend_error`;
- cancellation uncertainty or timeout, restart recovery, session busy, permissions,
  browser publication, replay, projection, and unclassified gateway failures keep their
  existing non-error Ticket behavior;
- takeover or a lost claim cannot be undone by a late worker completion.

Every non-error Ticket status transition clears `backend_error` in the same write. The
v32 migration also clears old errored Ticket states back to each current Stage's existing
resting control status. It uses the current-Stage ownership override when present and
otherwise the ownership default captured by v30, because the older correctness rows did
not record provenance and cannot confirm that their failures came from the backend.

On startup, a stranded running row is not treated as a new prompt. Recovery requires
the same Ticket session binding, interrupts the old correctness row, creates one
replacement carrying the same employee session, and resumes through ACP. Stale rows
that cannot be recovered are interrupted. First-wins settlement prevents shutdown,
late completion, and restart cleanup from finishing the same run twice.

Shutdown closes discovery admission, interrupts exact bound runs, drains accepted
work to the configured deadline, and then closes the ACP composition in owner order.

## Permissions

An Automatic Employee permission may be answered only while the exact Ticket,
session, binding generation, active turn, Ticket status, and running Employee-step row
still match. The check and transition into settling happen under one immediate SQLite
transaction. A stale permission cannot act on a newer run.

## Worker roles and Employee backends

The base role is `panels-worker`. It reads the Ticket's Worker type, loads that type's
specialist skill, and uses the `panels` CLI to inspect the Ticket and file proposals.
Panels exposes the repository's role skills through each backend's native skill
location: project links for Codex and Claude Code, and startup links in the configured
planner Hermes home. A new Ticket session receives the `panels-worker` instruction in
its first real prompt, whether that first prompt comes from the human pane or Automatic
Employee work. Panels does not alter Claude Code's system prompt.

Application composition builds one ordered Employee-backend catalog and validates every
Worker type's default against it. Ticket creation copies that type's starting Worker,
Model, and Reasoning values once. Both the human pane and `EmployeeStepRunner` resolve
the Ticket's stored launch setup before first binding, and the durable binding must agree
with its Worker. There is no runtime fallback to a different backend or explicit value.

The ordered production catalog is exactly `hermes`, `codex`, and `claude`. The server
uses the official ACP client library to run each adapter over standard input and
output. All three use the Ticket workspace root, support observed compaction and ACP
permissions, and can carry both human and Automatic Employee work. Hermes supports
native Steer. Codex and Claude Code do not; Queue and Send Now remain available.

Hermes exposes Model selection and no Reasoning selection. Codex and Claude Code expose
both through ACP, with Reasoning choices discovered again for the selected Model. After
the first binding, the stored launch model and reasoning are historical only. Bound
loads, child replacement, compaction recovery, and New Conversation do not reapply them;
the ACP session owns its live configuration.

Permission is a runtime invariant, not Ticket or binding state. Each actual new Worker or
Chief session receives the backend's full-access mode. A bound reload preserves the durable
session instead of creating or reconfiguring one. Hermes combines YOLO mode with `dont_ask`,
but its adapter may still expose residual permission behavior.

Claude Code receives an initialize-only preflight at server startup, and the temporary
child closes without creating a session. Codex starts lazily on first demand. Neither
startup behavior changes the Ticket's durable backend choice or session identity.

## Handoffs

- **Worker types** (`worker-types.md`) declares Stages, default ownership, and the
  specialist skill.
- **Tickets & the gates** (`tickets-and-gates.md`) owns proposals, scope, approval,
  and Ticket status.
- **Conversation** (`chat.md`) owns typed browser replay and shared human/worker
  session behavior.
- **The command-line tool** (`cli.md`) is the surface the employee acts through.

## Deferred

- **Retry after a settled error.** Startup recovers a stranded running step, but an
  already errored Ticket still needs a deliberate retry policy. This is intentionally
  outside the current failure-classification behavior.
- **In-server rollover scheduling.** Panels provisions the rollover role skill; thin
  scheduled prompts remain outside the deterministic runtime.

---

_Last verified: 2026-07-21 (three-backend ACP Employee runtime and correctness-only run records)._
