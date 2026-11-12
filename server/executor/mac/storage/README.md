# Mac outbox storage seam

`OutboxStore` preserves the Python outbox's local SQLite schema and immutable blobs.
It does not decode executor messages, construct envelopes, validate receipts, batch requests, or send HTTP.
The later host must use the shared generated wire types and codecs for those responsibilities.

Open the store with its isolated spool root and explicit clocks.
`OutboxClock.monotonic()` returns seconds. `now_ns()` returns wall-clock nanoseconds.
This incomplete storage seam does not choose a new production wall-clock precision policy.

The read-only format check occurs before directory or lock creation.
The store then opens its directories through the file worker, takes the retained-parent nonblocking process lock, and opens the shared durable sidecar.
The sidecar owns every writable SQLite connection.
All returned SQL integers use BigInt, including sequence numbers and nanosecond modification times.
Payload and receipt buffers remain exact opaque bytes.

`append(columns, payload, files, updates, validate)` assigns the global sequence and writes the file index in one transaction.
The required synchronous validator receives the inserted row before commit.
The host must validate and size-check its complete generated envelope there.
A failure rolls back the item and index changes. A repeated submission ID returns the held row without another insertion.
The validator must perform only synchronous encoding and validation, with no file work or asynchronous operation.

`with_capture(ticket, callback)` serializes that Ticket's captures and holds sweep protection through the callback's commit.
The callback receives local `ticket_files` rows, not wire manifests.
It must synchronously encode its payload and call `append` with the captured file references and index updates.
Only that narrow synchronous append remains admitted if shutdown begins during an already admitted capture.
An asynchronous callback is refused.
The host must check an existing submission before capture if it wants Python's duplicate-add fast path.

Source enumeration and metadata checks remain outside SQLite scopes.
The worker copies and hashes each changed source once, compares before/after metadata, and flushes the new blob before publication.
Recent modification times receive the existing `-1` cache marker.
Unchanged indexed files contribute dependencies only while an item still awaits their blob.
The worker skips unreadable or changing source bytes. Destination failures refuse the addition.
No directory or file hash runs on the main event loop or inside a database write.

`acknowledge` requires decoded identity and disposition facts plus the host's encoded receipt.
The host must validate the receipt and supply exact refused file keys before it calls this primitive.
The transaction retains CLI receipts, deletes other confirmed items, and clears confirmed rows for a stopped child.
The confirmed callback runs after commit and before blob cleanup, so cleanup failure cannot strand a host waiter.
Refused keys invalidate matching cache entries without removing a blob that another pending item references.

Startup deletes old confirmed rows and sweeps unreferenced spool blobs, including interrupted capture temporaries.
All blob deletion uses a retained parent. An active capture defers sweeps through publication or failure cleanup.
This sweep applies only to the spool's `blobs` directory.

`stop(shared_deadline)` seals new admissions, drains admitted captures and confirmations, closes SQLite, then releases the worker and process lock.
The deadline observes that cleanup without replacing its actual completion.
`settled()` retains actual worker completion. No store method starts a new file worker after stop.
Failed admitted operations do not prevent resource cleanup.
This storage layer logs each failed asynchronous operation once when its tracked promise rejects.
Shutdown does not log that failure again. The later host must not duplicate the same operation diagnostic.
Startup failures also retain worker ownership until cleanup actually settles.
