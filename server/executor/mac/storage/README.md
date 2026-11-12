# Mac outbox storage seam

`OutboxStore` preserves Python's SQLite outbox format and immutable blobs.
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
Local SQL sequences, lengths, and timestamps use BigInt.
Startup loads only sequence columns. Pending reads return at most 32 rows, optionally for one conversation.
The waiting-conversation read returns at most 65 ordered rows, so the host can expose 64 and detect truncation.

`append(columns, payload, files, updates, validate)` writes the item and index in one transaction.
Payload and receipt buffers retain their exact bytes.
For byte admission, storage reads the valid local `{payload,files}` wrapper and projects an `ItemSubmission` through the shared generated types.
The shared Pydantic JSON formatter retains numeric kinds, exact integers, UTF-8 text, and compact float spelling.
It refuses unpaired surrogates. This size-only projection does not validate complete wire shapes or reproduce all Pydantic input coercions.
Serialization occurs outside the writer. The allocated sequence width completes the 1 MiB check inside the transaction, even with a no-op validator.
The required synchronous validator still performs the host's complete wire validation before commit.
A repeated submission ID returns the held row. The host checks this before capture for Python's duplicate-add fast path.

`with_capture(ticket, callback)` serializes that Ticket's captures and protects blobs through the callback's commit.
The callback receives local file-index rows and must synchronously encode and append.
Only its admitted append remains available if shutdown begins during capture. Asynchronous callbacks are refused.
The worker copies and hashes changed sources once, checks before/after metadata, and flushes bytes before publication.
Unreadable or changing sources are skipped. Destination errors refuse publication.
Recent modification times retain Python's `-1` cache marker.
Neither file hashing nor copying runs on the main thread or inside the database writer.

`acknowledge` requires validated receipt facts and encoded bytes from the host.
The transaction retains CLI receipts, deletes other confirmed items, and clears confirmed rows for a stopped child.
The confirmed callback runs after commit and before blob cleanup. Cleanup failure cannot strand its waiter.
Refused file keys invalidate matching cache rows while other pending items retain their blob references.

An asynchronous empty-directory check precedes the reference join for sweeping.
After that check, storage rechecks capture activity before it queues deletion through a retained parent.
Startup sweeps unreferenced spool blobs and interrupted capture temporaries. This does not cover other artifact or update directories.
A capture-finally cleanup failure logs once and leaves blobs for a later sweep. It does not reject an append that already committed.
That last behavior deliberately fixes Python's post-commit cleanup failure. It is recorded in `server/DEVIATIONS.md`.

Worker loss seals admission, and every write checks worker health synchronously before its transaction.
Stop drains admitted captures and confirmations, closes SQLite, then releases the worker and main-owned lock.
If database close fails, resources stay owned and `settled()` stays pending. A later stop retries close.
After every resource closes, `settled()` rejects retained fatal faults and physical cleanup failures, including a close failure before a successful retry.
A rejected child actual join still permits the root to close its remaining database, worker, and lock.
Ordinary request refusals and the accepted deferred-capture cleanup policy do not become fatal shutdown history.
The shared deadline observes actual cleanup without replacing it.
Its typed deadline failure does not enter actual failure history. Other child stop failures remain visible even if the child's actual join succeeds.
The root joins both results before final settlement. Clean late cleanup succeeds without a renewed budget.
If startup cleanup fails or exceeds that deadline, its outward error preserves the startup error as `cause`.
While resources remain, that error also exposes `cleanup.stop(options)` and `cleanup.settled()` for continuation.
Successful startup cleanup rethrows the original startup error unchanged.
Each failed tracked asynchronous operation logs once. Shutdown and the later host must not duplicate that diagnostic.
