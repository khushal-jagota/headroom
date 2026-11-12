# Video upload intake and admission

`VideoUploadService` completes the existing admission service. It reserves quota,
receives bounded chunks, validates the local container, and records ready or failed
outcomes. `preflight`, `adopt`, and `finish` retain the existing two-phase adoption
path and accepted-file pinning.

The constructor remains `new VideoUploadService(database, db_path)`. Its optional
third argument supplies an epoch-seconds clock, limits, free-space reading, probe
availability, probe function, and the host's shutdown signal. Production defaults
use the local filesystem and bounded ffprobe. The probe owns a detached process
group, stops it on abort or limit failure, joins its exit, and closes its streams.
Tests inject only the boundary that their case needs to control.

The public intake methods mirror Python: `reserve(request)`, `status(upload_id)`,
`cancel(upload_id)`, `transfer(upload_id, chunks)`, and `recover_and_cleanup()`.
Transfer accepts an async iterable of byte arrays. Each incoming array is divided
into the configured chunks. A fresh transfer requires `reserved`, starts at zero,
and creates its temporary file exclusively. It does not resume a partial transfer.
An interrupted or oversized transfer fails, removes the temporary file, and keeps
the received-byte count for the chunks already committed. A second transfer call
for that upload receives the same unavailable error as Python.

## File-write order

The Manager accepted one deviation from Python's chunk writer. Python checks
state and capacity, writes through a buffered file object, and commits progress
inside one write transaction. TS reads state and capacity, awaits the file write
outside the write door, then checks state again and commits progress in a short
transaction. It flushes the complete file before probing. Hashing, media probing,
and file copying never occur under the write door.

An ordinary sequence of chunks yields the same final file bytes, progress rows,
and route answer. Repeating an identical byte chunk appends those bytes again on
both sides. It is not a chunk identity or deduplication protocol. If that exceeds
the reservation, both sides fail the transfer.

During a TS write, another request can read the previous progress or cancel the
upload. Python's write transaction serializes that request until its chunk commit.
TS checks the state after the file write. A concurrent cancellation prevents the
progress commit. The transfer then follows Python's failure path, removes the
temporary file, and records `failed`. It does not resurrect a ready upload.

A crash after a TS file write and before its progress transaction can leave file
bytes ahead of `received_bytes`. Python can also leave bytes ahead when its file
write reaches the kernel before an uncommitted transaction rolls back. Python's
buffered writes can additionally leave bytes behind a committed count until its
buffer flushes. Neither implementation claims the temporary bytes are durable
until the final file flush. In both cases, startup finds a `transferring` row and
temporary file. Recovery does not resume or mark that row ready. A fresh transfer
is refused. At its expiry, cleanup removes the file and marks the row `cancelled`.
Before expiry, its outstanding reservation remains part of capacity accounting.

Recovery preserves uncertain admissions. If an admitted row's recorded target
contains its accepted prompt identity, recovery marks it linked. Expiry cleanup
never removes admitted or linked files. The parent Ticket records the direct
comparison and accepted deviation in its final evidence and `server/DEVIATIONS.md`.
