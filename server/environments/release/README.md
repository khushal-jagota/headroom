This slice supplies application identity, receipt-bound archives, and pure database-frontier rules. Python owns migrations. A TS database must already match the candidate frontier. Static native inventory, runtime admission, build, installation, and publication follow in separate reviewed slices.

Unpack opens the compressed input once and passes that descriptor to a required invocation-owned Node child. A first compressed pass checks the receipt hash before format parsing. Gunzip rereads that same descriptor from offset0 and inflates once. A bounded TAR spool retains validated offsets. Extraction never reopens the archive pathname or interprets the metadata again. The parent creates the private output exclusively only after full hash and member validation. An appeared directory refuses. The API returns only after actual child close, final metadata/fsync, and spool cleanup. The installer owns the later rename.

The spool is `0600` inside an exclusive `0700` directory. Output also starts and remains private at `0700`. On failure, the parent removes only its own output and spool, after actual child close. Archived directory modes cannot prevent that cleanup. The shared child owner supplies stop/force authority. No private owner, timer, or second process registry exists.

The reader uses these fixed budgets:

| Resource | Limit |
| --- | --- |
| Compressed input | 4 GiB |
| Expanded TAR | 8 GiB |
| One member | 4 GiB |
| Headers | 250,000 |
| One metadata body | 1 MiB |
| Cumulative metadata, path, and link bytes | 64 MiB |
| Path depth | 64 components |
| End padding | 2–40 whole zero blocks |
| Stream chunks | 64 KiB |

Invalid input and I/O or child failures return `AppValidationError`.

Space admission has three checks:

- Before the spool, require twice the compressed size plus 1 GiB. A shortage returns `app archive has insufficient free space before spooling`.
- During spool writes, require the reserve plus the next batch, at most 1 MiB. A shortage returns `app archive exhausted free-space reserve while spooling`.
- After the spool, require its exact expanded size plus 1 GiB before output creation. A shortage returns `app archive has insufficient free space for output`.

The cited tree needs roughly 6.6 GiB at peak, including reserve, before filesystem overhead. Concurrent users can still exhaust a filesystem. The parent removes partial output after failure.

The Manager accepted refusal of GNU sparse input, members behind links, unknown inert PAX keys, empty effective paths, non-UTF8 names, and directory/link bodies. [The difference registry](../../DEVIATIONS.md) records these policies. The release writer can lose fractional mtimes in ordinary headers. Fractional PAX times it writes stay intact. Readers preserve Python's nanoseconds exactly for either producer. Ordinary file/directory times and Unix modes are restored before their final fsync.

The child uses the existing Koffi dependency for descriptor-based `futimens` because Node's `utimes` truncates nanoseconds. Its conversion mirrors Python's split and floor operations. No timestamp tolerance or new binary is introduced. The parent protocol import does not load Koffi.

The existing comparison sequence covers synthetic ordinary archives and hostile input. Full real-producer tree equality, timestamp precision, child memory/disk cost, and cancellation evidence remain hosted gates. Each actual Python/TS producer must supply its real archive for Python/TS extraction comparison. Sealed-package evidence must retain every package's own license files and name packages that ship none. Development dependency scans do not prove that inventory. No root notice entry is required for the installed tar dependency.

The fixed stopped-database capture entry publishes unavailable evidence before it takes runtime ownership and reads one zero-wait readonly snapshot. It changes no application rows or schema. The fixed restore entry delegates to the existing stopped restore primitive. Both entries are release payload files. The external installer owns their deadlines and actual process cleanup. Real installer integration and copied-runtime independence remain open gates.


Receipt staging binds the receipt hash, complete manifest, and fresh runtime admission before the final sibling rename. A matching retained stage gets fresh admission without another download. Permission-restoring cleanup applies only to the exclusive workspace.

The schema adapter checks the complete v2 frontier through a zero-timeout readonly snapshot. V1 status uses the admitted candidate’s own isolated Python helper. UpdateService awaits that result and fences backup after shutdown. Actual v1 execution and sealed v2 admission remain hosted gates.

VPS Mac readiness constructs one real MacUpdateService with the runtime’s actual store, provider, lazy ready-release callback, and invocation child owner. A genuinely absent executor stays absent. Inspection uses one held descriptor, a bounded hash-first read, and one inflate pass. It creates no spool or output tree. The null-manifest sentinel and last Python marker retain source behavior. Extraction link and mode restrictions do not apply to inspection.

Additional Mac restrictions remain proposed: physical eight-GiB, header and metadata limits, sparse members, closed PAX keys, non-UTF8 paths, and NUL names or PAX values. Global PAX size, numeric forms, strict end padding, and UTF16/32/non-scalar manifest encoding restrictions also remain proposed. Local PAX size and UTF8 BOM remain supported. Node SyntaxError versus Python JSONDecodeError diagnostics remain proposed. Reports retain accepted:false for these observations.

No accepted deviation entry, Mac format change, default factory activation, executor transport, or real producer compatibility claim follows.
