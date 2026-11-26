# Durable runtime records

`records.ts` writes sorted ASCII JSON bytes. It keeps nonfinite constants and exact numeric kinds.
The invocation's existing `FilesService` performs all record reads, writes, full flushes, replacement, and removal.

`record_operations.ts` retains parent descriptors through temporary-file creation, publication, and cleanup.
A substituted parent path cannot redirect the write or temporary-file removal.
Failed native closes retain their capabilities. The file worker seals admission, records the failure, and retries cleanup through its existing stop protocol.
`settled()` joins actual worker exit and retains the failure after successful cleanup.

These records live under the executor's `updates/` directory, beside `updates/releases/`.
The update controller and supervisor supply their deterministic local record paths.

`supervisor.ts` starts the selected runtime. On the first candidate start, the old supervisor passes the update to a separate native service. The update job records the previous definition, stops the old service, and starts the candidate supervisor through the new definition. The candidate checks the ready request and revision before promotion. If the trial fails, the update job restores the saved service definition and selection. A joined Mac also restores the previous signed app.
The selected runtime's `bin` leads the child PATH. Callers retain the invocation owners and close files after supervision settles.

`FilesService.read_runtime_revision` and `extract_runtime` accept an optional expected target through the owned files worker.
Undefined target preserves Darwin's marker and private Node checks. Explicit Linux executor admission instead binds its native Node manifest, payload contract, and inventory under the retained root descriptor.
Linux requires executable `bin/panels` and the shared executor entrypoint. It does not require `node/bin/node`.
The shared `admit_runtime` also accepts the target for its manifest recheck.

On Linux, the supervisor passes `LINUX_EXECUTOR` and uses the admitted host Node for baseline, selected, and restored runtimes. P5's `linux_executor_path` supplies the exact ordered PATH. The shared entrypoint constant supplies the CLI entry file. Executor data stays at the explicit `PLAN_EXECUTOR_ROOT`, while app root and SHA follow the selected runtime. Credentials remain in their private file, outside child arguments and environment values.

A failed Linux trial receives the shared Worker stop budget plus ten seconds before forceful termination. Ordinary supervisor shutdown retains its existing budget and process owner. Darwin retains private selected Node and its PATH behavior. Both platforms receive the selected app SHA explicitly.
