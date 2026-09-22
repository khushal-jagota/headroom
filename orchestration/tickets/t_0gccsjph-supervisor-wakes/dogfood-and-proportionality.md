# Supervisor wake dogfood and proportionality

## Authoritative runtime boundary

The authoritative run exercised branch `ticket/t_0gccsjph-supervisor-wakes` at report
head `6faf8b397f03117363e621691b34ce0fb0dd6571`; product implementation head remained
`60181b826d35e188ad919e8684f5de3559f9d654`.

- Isolated root: `/tmp/panels-wake-codex-30nrdwk7`
- Server: `127.0.0.1:32967`
- `PLAN_TEST_MODE=1`, fixed clock `2026-09-22T12:00:00+02:00`
- `PLAN_TICK_SECONDS=60`, the repository default
- DB, dispatcher lock, logs, backups, control socket, and Hermes home were separate
  paths below the isolated root; `PLAN_APP_ROOT` named this worktree.

The harness ran the real FastAPI app, SQLite conversation system/store,
`BackgroundLoops`, Worker API/readiness flow, manager-wake loop, and wake store. Role
identity selected the backend boundary: Ticket Workers (`PLAN_TICKET_ID`) used the
production Codex app-server child, while the Sprint Item manager used the scripted child
so its incumbent could be held and completed deterministically. Only the explicitly
armed error Ticket's opener returned `PromptDeliveryUncertain`; all manager deliveries
used the real conversation system. See [evidence](evidence/run-transcript.md), including
the [sanitized harness](evidence/hybrid_dogfood_server.py),
[server log](evidence/server-log-sanitized.txt), [backend snapshots](evidence/backend-state.json),
and [final SQL state](evidence/final-state.json).

No live deployment database, state path, port, or process was touched, and nothing was
integrated to staging. The isolated server stopped cleanly.

## Results

Three production Codex Workers received real durable Worker prompts and independently
called `panels worker propose`:

- `t_nzwycxjn`, tool-result event 15, created idle proposal wake 1;
- `t_4m5x08ta`, tool-result event 14, created active-manager proposal wake 2;
- `t_dwk2jvzp`, tool-result event 17, created restart-recovery wake 4.

All were minimal coding Tickets created directly at `needs_success_condition`, Item-held,
with no kickoff or pending proposal.

Idle delivery created batch 1 and exact manager prompt event 1 before wake 1 closed. With
that manager turn active, the second Codex proposal remained open. The canonical
`/api/test/run-step/t_zh967ydd` uncertain path atomically wrote an errored claim at
revision 2 and worker-error wake 3 at source revision 2. The active manager had one
write and zero cancellations. After its exact completion, batch 4 combined wakes 2 and
3; exact prompt event 3 then closed both. The manager had two writes and still zero
cancellations.

For restart recovery, loops were stopped while the manager remained active, the third
Codex Worker created open wake 4, and the server stopped with no retained batch for that
wake. Restarting the same isolated DB with real background loops created batch 5 and
exact prompt event 4, then closed wake 4. The restarted scripted backend observed one
write and zero cancellations.

Final durable state was wakes 1–4 closed; batches 1, 4, and 5 delivered; memberships
`1 -> [1]`, `4 -> [2, 3]`, `5 -> [4]`; and manager events prompt 1, completed turn 2,
combined prompt 3, recovered prompt 4. Every prompt carried its batch's exact sender ID.

## Proportionality finding

The active-manager preflight claims a durable batch before checking activity, then
quietly deletes it on deferral. Periodic polls and unrelated shared change signals can
therefore repeat a claim/delete while an open wake and active manager coexist. In this
preserved default-60-second run, `sqlite_sequence` reached 5 while only batches 1, 4,
and 5 survived: two transient active-preflight batch IDs were minted and deleted. An
earlier 1-second exploratory run suggested larger proportional churn, but its raw
evidence was not retained and is not treated as an authoritative count.

This caused no interruption, replay, retained-row growth, or failure to combine. It is
still avoidable write and sequence-ID churn. The preflight is removable in a follow-up
because queue mode already avoids interruption and combines delivery into a later turn;
any removal must preserve exact-prompt closure, restart-safe `accepted`/`dispatching`,
and intended wake coalescing.

Safe separate cleanup candidates are moving the scripted adapter into `tests/support`,
turning this harness into a maintained isolated-runtime helper, documenting queue-mode
coalescing beside the wake runtime, and keeping formatter-only cleanup in dedicated
changes rather than broad product/test diffs.
