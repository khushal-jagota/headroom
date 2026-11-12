# Command journal worker

Open through `open_command_journal(outbox, incarnation, dependencies, shared_cleanup_options)`.
The outbox reserves the complete opening and registers its unstarted journal before module loading or Worker creation.
The journal creates only `commands.sqlite3` beneath that owner's root. It takes no independent lock.

The required dependencies are trusted file-module URLs for the shared protocol and pure output policy, `now_ms(): bigint`, and `on_failure(error): void`.
The protocol exports `decode_json` and `encode_value` with the shared root names.
The pure module exports `command_owes_outputs`. It must import no provider or account composition.
The host must schedule owned shutdown from `on_failure`. Each journal fault invokes it once.
The spool also requires a fourth `OutboxStore.open` callback for file-worker faults. A shared host shutdown action must be idempotent.
The final production codec binding awaits the shared protocol module. The current required port does not prove that binding.
The final binding must use shared `integer_value` for generated `WireInteger` values. The current private conversion awaits that handoff.
Trusted module injection serves finite comparisons. No environment lookup, decoder fallback, optional codec, or production test import exists.
Main imports the protocol only for shared error prototypes. Codec and policy calls run inside the journal worker.

All APIs are asynchronous. Command/result inputs use canonical bytes or text, without a wire body cap for journal results.
Journal reads remain lax. Canonical writes use shared `encode_value` and the shared Pydantic formatter.
Mailbox intake, unfinished-command recovery, and tombstone compaction keep Python's atomic transactions and SQL sort clauses.
The worker completes full snapshot normalization before it exposes the first row, preserving failure order.

The journal returns cursor rows with `CanonicalReader` objects. Their `byte_count` is a safe number from the worker's Buffer length.
A reader transfers owned chunks of at most 256 KiB. No unrelated pooled backing bytes cross the thread boundary.
Natural cursor EOF leaves already issued readers usable. Explicit cursor close releases its issued readers and unissued rows.
Reader EOF releases its worker reference. Early iterator return closes that reader or cursor.
Live readers enforce their exact byte count and chunk limit. Explicit snapshot cursor close marks its issued readers closed locally.
Fresh cursors expose `command_count` for the original poll reply, including duplicates.
The caller must consume every fresh command and its bytes, or explicitly close the batch in `finally`.
Incomplete fresh delivery faults the service and host. Restart recovery then settles its recorded commands instead of silently losing delivery.
A failed successful-response transfer exits the worker. Cursor capacity is checked before the intake transaction commits.
Stop releases all abandoned reader, cursor, and partial-input references.
No full inventory or cloned `JsonNumber` array crosses to main.
The later reporter must use byte counts and worker-local selection instead of rebuilding the complete inventory on main.

Private diagnostics use a tagged control representation for scalars, objects, arrays, bytes, and explicit numeric markers.
Every value receives a tag. This preserves `JsonNumber` identities without guessing from user-object field names.
Main restores errors through the trusted shared prototypes. It does not decode bad data again to manufacture an error.

The worker loads modules before it requests opening permission from the main owner.
It retains the database, or core's failed-opening close capability, before the startup directory flush.
Main continues admitted clock and flush callbacks during shutdown and joins their actual promises.
A stopped owner never returns a live journal.

Stop seals new calls immediately. It drains whole admitted operations before it sends the worker stop frame.
An admitted multi-chunk finish retains its reservation through the final transaction during graceful stop.
An active transaction finishes atomically. No forced worker termination bypasses a live database-close failure.
A close failure keeps its retry port and resource ownership. Later stop calls retry it.
Observed deadlines do not cancel actual cleanup. After the journal settles, the root cleanup chain continues through outbox close, file-worker exit, and lock release.
Fatal transport-write failures seal pending/later calls and terminate the broken worker, then join its actual exit.
Stop after an owned worker fault returns `ServerLifecycleError`. An ordinary live database-close failure retains its original retryable error.

Fixed admission limits retain at most 32 complete operations or queued requests, one partial upload, 32 snapshot cursors, and 64 issued readers.
Excess requests fail before admission. Each input has a signed 32-bit byte ceiling. SQLite can refuse its own lower native limit.
Mailbox poll input also keeps the existing 4 MiB body limit. No 1 MiB cap applies to catalog results in the journal.
Snapshot owners must close cursors in `finally`. Natural EOF preserves outstanding issued readers. Stop releases every remaining snapshot.

Unexpected Worker exit seals spool mutations. The pinned better-sqlite3 environment cleanup hook closes its native database handles before actual exit.
An error event alone is not completion. Actual `settled()` joins the worker exit and all admitted callbacks and operations.
The addon proof lives in `src/better_sqlite3.cpp:56`, `src/addon.cpp:7`, and `src/objects/database.cpp:36` in pinned better-sqlite3 13.1.1.
Node-API environment cleanup applies even when `worker.terminate()` disallows JavaScript cleanup.

The root owns one internal shared fault latch. Observed file-worker or journal failure sets it.
Ordinary shutdown does not set it, so already admitted work can finish.
The journal checks this latch before writes and at the end of each transaction callback.
This narrows an observed cross-thread failure window. It does not atomically fence physical thread death through SQLite COMMIT.
The main-owned lock still excludes replacement writers until all registered databases and workers actually settle.

Queue, runner, HTTP, provider, and catalog composition remain later slices.
The finite fixture modules prove focused behavior, not arbitrary runtime wire validation or production provider exchange.
