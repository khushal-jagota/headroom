# Attachment upload intake and admission

`VideoUploadService` completes the existing admission service. It reserves quota,
receives bounded chunks, and records ready or failed transfer outcomes. Every file type
uses this service with the same 250 MiB limit, including empty files. Media inspection
chooses a representation. A failed or unavailable video probe keeps the file as an
opaque attachment instead of refusing it. Supported small raster images retain native
image delivery within its existing budget; larger images remain file references. `preflight`, `adopt`, and `finish` retain the existing two-phase adoption
path and accepted-file pinning.

The constructor remains `new VideoUploadService(database, db_path)`. Its optional
third argument supplies an epoch-seconds clock, limits, free-space reading, probe
availability, probe function, and the host's shutdown signal. Production defaults
use the local filesystem and bounded ffprobe. The probe owns a detached process
group, stops it on abort or limit failure, joins its exit, and closes its streams.
Tests inject only the boundary that their case needs to control.

Storage uses the supplied database path's bare parent, without
resolving a DB-file symlink: `<supplied-parent>/files/video-uploads`.

The public intake methods are `reserve(request)`, `status(upload_id)`,
`cancel(upload_id)`, `transfer(upload_id, chunks)`, and `recover_and_cleanup()`.
Transfer accepts an async iterable of byte arrays. Each incoming array is divided
into the configured chunks. A fresh transfer requires `reserved`, starts at zero,
and creates its temporary file exclusively. It does not resume a partial transfer.
An interrupted or oversized transfer fails, removes the temporary file, and keeps
the received-byte count from its last committed progress checkpoint. A second transfer call
for that upload receives the unavailable error.

## Progress policy

Chunk writes happen outside the write transaction. Each chunk checks the physical disk floor, then writes with
no transaction open. Progress is recorded after each 8 MiB threshold and
unconditionally at EOF, before the final flush. Each progress transaction checks
state, updates the received count, then checks outstanding disk reservations.
Cancellation and reservation checks occur at those same checkpoints.
The capacity query reads filesystem metadata only. Transfer byte writes, hashing,
media probes, and copies stay outside the write door. The existing `adopt` and
`finish` paths still rename files and write the media-type sidecar inside their
transactions. Those short operations are not transfer writes.

Repeating identical chunk bytes appends them. There is no chunk identity or
deduplication protocol. An interrupted transfer cannot resume.

Temporary files are not served through a public route before readiness. Nothing
promises durability before the final flush. A crash before a checkpoint can leave
bytes ahead of the stored count. Startup retains a nonexpired transferring row,
refuses a new transfer for that row, retains its quota, then removes and cancels
it at expiry.

Recovery preserves uncertain admissions. If an admitted row's recorded target
contains its accepted prompt identity, recovery marks it linked. Expiry cleanup
never removes admitted or linked files.

## Shutdown and disconnect

Shutdown seals new intake and waits for tracked transfers before record or database closure.
The shared shutdown deadline bounds that wait. If it expires, teardown stops and
retains the database and runtime ownership beneath the active transfer. It does
not abort that transfer or invent a shutdown error in its row.
`drain({ deadline })` returns `Promise<void>` and resolves only after every tracked
transfer settles. An unfinished drain rejects with `ServerLifecycleError` and the
message `Video transfers remain unfinished at the shutdown deadline.` The caller
keeps runtime ownership when it receives that rejection.

A client disconnect removes the temporary file and records `Upload interrupted.`,
with the last committed progress checkpoint.

Accepted decisions pin their uploads in the decision transaction. A refused Worker start
returns the bytes to the upload store but retains that decision pin. A later Worker
claim can adopt them into its new conversation, even after the ordinary upload expiry.
Definite refusals of an unsent draft release its identity so an edited draft can reuse
the upload. Accepted or uncertain deliveries remain pinned.
