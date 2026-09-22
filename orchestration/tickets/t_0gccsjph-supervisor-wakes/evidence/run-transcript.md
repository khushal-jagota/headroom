# Authoritative hybrid-backend dogfood transcript

Branch `ticket/t_0gccsjph-supervisor-wakes` was at
`6faf8b397f03117363e621691b34ce0fb0dd6571` when this run began. The implementation
under test was unchanged from `60181b826d35e188ad919e8684f5de3559f9d654`.

## Isolation

- Root: `/tmp/panels-wake-codex-30nrdwk7`
- Port: `32967`
- Test mode: enabled; fake time `2026-09-22T12:00:00+02:00`
- Tick: 60 seconds
- DB, dispatcher lock, logs, backups, control socket, and Hermes home: distinct paths
  below the isolated root
- Application root: ticket worktree

The launch command supplied only the values above as `PLAN_*` overrides, then ran:

```text
.venv/bin/python data/wake_dogfood_server.py
```

No credential values were printed or copied. Codex used the machine's ordinary
authenticated production child. Its optional app/plugin catalogue requests received
HTTP 403 responses, but the Worker turns and Panels CLI calls completed normally.

## Fixture

The isolated DB contained Sprint Item `si_th743adw` and four coding Tickets created at
`needs_success_condition`, held by the Item manager, with a saved brief and no pending
or kickoff proposal:

```text
idle       t_nzwycxjn
combined   t_4m5x08ta
error      t_zh967ydd
restart    t_dwk2jvzp
```

## Commands and observed results

```text
POST /api/test/run-step/t_nzwycxjn
=> {"dispatched":true,"ticket_id":"t_nzwycxjn"}
```

Production Codex conversation `conv_f5042ddb38c74a4189c04bc118e16201` received the
real Worker prompt. Its event 15 completed a shell tool call with detail
`proposed on t_nzwycxjn`; the Ticket acquired proposal revision 1 and wake 1.

```text
POST /dogfood/start-loops
=> {"started":true}
```

Batch 1 delivered wake 1 into scripted manager conversation
`conv_79c973e8c8684d1ca1944e5bd7bd0951`. The exact prompt event existed before closure.

```text
POST /dogfood/stop-loops
=> {"stopped":true}
POST /api/test/run-step/t_4m5x08ta
=> {"dispatched":true,"ticket_id":"t_4m5x08ta"}
```

Production Codex conversation `conv_8b1df2924bc84ab18627acea15b19cc7` received the
Worker prompt. Its event 14 completed with `proposed on t_4m5x08ta`, creating wake 2.

```text
POST /dogfood/arm-worker-error/t_zh967ydd
=> {"armed_ticket_id":"t_zh967ydd"}
POST /api/test/run-step/t_zh967ydd
=> {"dispatched":false,"ticket_id":"t_zh967ydd"}
```

The canonical readiness path recorded Ticket claim `errored` at revision 2 and created
worker-error wake 3 at source revision 2.

```text
POST /dogfood/start-loops
=> {"started":true}
GET /dogfood/backend/conv_79c973e8c8684d1ca1944e5bd7bd0951
=> running=true, writes=1, cancellations=0
```

Wakes 2 and 3 remained open while the manager incumbent was active. Completing that
incumbent returned `cancellations=0`; batch 4 then combined wakes 2 and 3 and its exact
prompt event 3 closed both.

```text
POST /dogfood/stop-loops
=> {"stopped":true}
POST /api/test/run-step/t_dwk2jvzp
=> {"dispatched":true,"ticket_id":"t_dwk2jvzp"}
```

Production Codex conversation `conv_68b8738e60344ea7b7bd9be3da7583f9` completed the
`panels worker propose` tool call at event 17, creating open wake 4. The server stopped
with loops stopped and no retained batch for wake 4.

The same DB restarted with `DOGFOOD_AUTO_LOOPS=1`. Batch 5 delivered wake 4, exact
manager prompt event 4 appeared, and wake 4 closed. The restarted scripted backend
reported one write and zero cancellations. The server then stopped cleanly.
