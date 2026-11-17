# Mac outbox storage seam

`OutboxStore` preserves Python's SQLite outbox format. Items carry no files. The file sync in `transport/ticket_files.ts` sends Ticket files apart from them.
It does not validate complete wire shapes, construct delivery batches, or send HTTP.
The shared generated types own the wire contract. The later host still needs runtime codecs.

Open with `OutboxStore.open(root, clock, shared_cleanup_options, on_failure)`.
The required fault callback must initiate owned host shutdown. It runs once per root fault.
`monotonic()` returns seconds. `now_ns()` returns wall-clock nanoseconds.
The caller supplies the cleanup deadline. This seam chooses no new production clock policy.

Read-only format refusal occurs before directory or lock creation.
The file worker creates and flushes directories. The database's main thread then walks retained no-follow parents and acquires the nonblocking spool lock.
Only that database owner can release its lease. File-worker loss cannot release it.
The shared sidecar keeps all writable connections inside the database module.
Sequences, lengths, and counters use safe number values. Private filesystem timestamps retain bigint stat precision.
Startup loads pending sequence columns and retains local delivery diagnostics. Pending reads return at most 32 rows, optionally for one conversation.
The waiting-conversation read returns at most 65 ordered rows, so the host can expose 64 and detect truncation.

`append(columns, payload, validate)` writes the item in one transaction. It reads no Ticket file, so a large folder cannot delay it.
The event value and receipt bytes remain unchanged.
For byte admission, storage reads the valid local `{payload,files}` wrapper and projects an `ItemSubmission` through the shared generated types.
Native `JSON.stringify` measures the projected message size.
The required host validator owns complete schema and admission checks.
Serialization occurs outside the writer. The allocated sequence width completes the 1 MiB check inside the transaction, even with a no-op validator.
The required synchronous validator still performs the host's complete wire validation before commit.
A repeated submission ID returns the held row.

`note_ticket_activity(ticket)` tells the file sync that a Ticket's files can differ.
`ticket_files` records the file versions that the home confirmed. `home_files`, `record_home_file`, and `forget_home_file` read and change it for the file sync.
A recent modification time keeps the `-1` marker, so the next pass hashes that file again.
At startup, a file that still waited on an item from an older runtime leaves that record, so the file sync sends it. The old `blobs` copies are deleted.

`acknowledge` requires validated receipt facts and encoded bytes from the host.
The transaction retains CLI receipts, deletes other confirmed items, and clears confirmed rows for a stopped child.
Rows with local delivery failures remain for diagnosis, including after restart and child cleanup.
The confirmed callback runs after commit.

The local payload wrapper also stores delivery start time and terminal local failure reasons.
The first delivery attempt persists its wall-clock time so a restart cannot renew the delivery budget.
`fail_delivery` preserves failed content and the supplied local receipt, and removes the item from pending delivery.
`delivery_status` exposes retained failure counts and a reason independently of lease health.
These facts preserve the submitted event, item wire format, and SQLite spool schema.

Worker loss seals admission, and every write checks worker health synchronously before its transaction.
Stop drains admitted confirmations, closes SQLite, then releases the worker and main-owned lock.
If database close fails, resources stay owned and `settled()` stays pending. A later stop retries close.
After every resource closes, `settled()` rejects retained fatal faults and physical cleanup failures, including a close failure before a successful retry.
A rejected child actual join still permits the root to close its remaining database, worker, and lock.
Ordinary request refusals do not become fatal shutdown history.
The shared deadline observes actual cleanup without replacing it.
Its typed deadline failure does not enter actual failure history. Other child stop failures remain visible even if the child's actual join succeeds.
The root joins both results before final settlement. Clean late cleanup succeeds without a renewed budget.
If startup cleanup fails or exceeds that deadline, its outward error preserves the startup error as `cause`.
While resources remain, that error also exposes `cleanup.stop(options)` and `cleanup.settled()` for continuation.
Successful startup cleanup rethrows the original startup error unchanged.
Each failed tracked asynchronous operation logs once. Shutdown and the later host must not duplicate that diagnostic.
