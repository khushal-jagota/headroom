# Durable runtime records

`records.ts` preserves Python's sorted ASCII JSON bytes, nonfinite constants, and exact numeric kinds.
The invocation's existing `FilesService` performs all record reads, writes, full flushes, replacement, and removal.

`record_operations.ts` retains parent descriptors through temporary-file creation, publication, and cleanup.
A substituted parent path cannot redirect the write or temporary-file removal.
Failed native closes retain their capabilities. The file worker seals admission, records the failure, and retries cleanup through its existing stop protocol.
`settled()` joins actual worker exit and retains the failure after successful cleanup.

Python places these records under the executor's `updates/` directory, beside `updates/releases/`.
The update controller and fixed supervisor will supply their deterministic local record paths.
Linux synthetic checks prove byte parity, staged-write failure, retained-parent publication, and close retry.
Darwin ABI and power-loss durability remain later native checks.
