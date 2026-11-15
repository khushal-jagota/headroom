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

`supervisor.ts` keeps the original installation fixed and launches the selected runtime with the same CLI arguments.
It checks the ready request and revision before promotion. An unconfirmed trial restores the previous selection and handoff.
The selected runtime's `bin` leads the child PATH. Callers retain the invocation owners and close files after supervision settles.

`FilesService.read_runtime_revision` and `extract_runtime` accept an optional expected target through the owned files worker.
Undefined target preserves Darwin's marker and private Node checks. Explicit Linux executor admission instead binds its native Node manifest, payload contract, and inventory under the retained root descriptor.
Linux requires executable `bin/panels` and the shared executor entrypoint. It does not require `node/bin/node`.
The shared `admit_runtime` also accepts the target for its manifest recheck.

On Linux, the supervisor passes `LINUX_EXECUTOR` and uses the admitted host Node for baseline, selected, and restored runtimes. P5's `linux_executor_path` supplies the exact ordered PATH. The shared entrypoint constant supplies the CLI entry file. Executor data stays at the explicit `PLAN_EXECUTOR_ROOT`, while app root and SHA follow the selected runtime. Credentials remain in their private file, outside child arguments and environment values.

A failed Linux trial receives the shared Worker stop budget plus ten seconds before forceful termination. Ordinary supervisor shutdown retains its existing budget and process owner. Darwin retains private selected Node and its PATH behavior. Both platforms receive the selected app SHA explicitly.
