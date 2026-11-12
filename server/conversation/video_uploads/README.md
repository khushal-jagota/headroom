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
the received-byte count from its last committed progress checkpoint. A second transfer call
for that upload receives the same unavailable error as Python.

## Progress policy

Python staging PR 756 moves chunk writes outside its write transaction. TS now
matches that policy. Each chunk checks the physical disk floor, then writes with
no transaction open. Progress is recorded after each 8 MiB threshold and
unconditionally at EOF, before the final flush. Each progress transaction checks
state, updates the received count, then checks outstanding disk reservations.
Cancellation and reservation checks occur at those same checkpoints on both sides.
The capacity query reads filesystem metadata only. Transfer byte writes, hashing,
media probes, and copies stay outside the write door. The existing `adopt` and
`finish` paths still rename files and write the media-type sidecar inside their
transactions, as Python does. Those short operations are not transfer writes.

The direct sequence uses an eight-byte threshold and four-byte chunks to prove
threshold and EOF checkpoints without a large fixture. It compares the progress
calls, rows, answers, and final bytes against Python commit `2f2410343` or newer.
Repeating identical chunk bytes appends them on both sides. It is not a chunk
identity or deduplication protocol. An interrupted transfer cannot resume.

TS writes through a FileHandle, while Python writes through a buffered file
object. Temporary bytes can therefore become visible on disk at different times.
Those files are not served through a public route before readiness. Neither side
promises durability before the final flush. A crash before a checkpoint can leave
bytes ahead of the stored count. Python buffering can also leave bytes behind a
committed count until it flushes. Both retain a nonexpired transferring row at
startup, refuse a new transfer for that row, retain its quota, then remove and
cancel it at expiry. This is not a remaining progress-policy deviation.

Recovery preserves uncertain admissions. If an admitted row's recorded target
contains its accepted prompt identity, recovery marks it linked. Expiry cleanup
never removes admitted or linked files. The parent Ticket records the direct
comparison and exact Python baseline in its final evidence.

## Shutdown and disconnect

Python's ordinary graceful shutdown waits for an active PUT without a timeout.
The transfer stays active until it completes or the client disconnects. TS seals
new intake and waits for tracked transfers before record or database closure.
The shared shutdown deadline bounds that wait. If it expires, teardown stops and
retains the database and runtime ownership beneath the active transfer. It does
not abort that transfer or invent a shutdown error in its row.
`drain({ deadline })` returns `Promise<void>` and resolves only after every tracked
transfer settles. An unfinished drain rejects with `ServerLifecycleError` and the
message `Video transfers remain unfinished at the shutdown deadline.` The caller
keeps runtime ownership when it receives that rejection.

A client disconnect removes the temporary file and records `Upload interrupted.`,
with the last committed progress checkpoint, as Python does. The focused app test
proves the retained state at a drain deadline and the later disconnect cleanup.
