This slice supplies application identity, receipt-bound archives, database-frontier rules, static native inventory, and offline runtime admission. Python owns migrations. A TS database must already match the candidate frontier. Build, installation, and publication follow in separate reviewed slices.

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

The builder exports one exact Git commit and creates Node runtime metadata with the existing static native closure. Its required execution scope supplies the actual child owner, abort signal and original absolute deadline. Explicit null deadline preserves standalone build policy. Server calls require their existing finite deadline and invocation owner. Git, npm, readelf, patchelf, ldconfig and dpkg-query all use that same scope. Commands capture output in private files, refuse overflow before parsing and join actual close before cleanup. No per-command join waits for unrelated invocation children.

Raw Git export uses the same owned unpack child and bounded TAR reader, with an explicit source-export request and completion. It preserves Git export attributes and the existing inert PAX comment. It creates no fake release manifest. Release extraction keeps its gzip, receipt hash and manifest requirements. Source cancellation closes the input channel, stops the exact unreaped group and joins actual close before it removes owned output or spool. Cancellation cannot undo a filesystem operation already in progress.

The complete builder requires a committed generated migration graph and schema seed for the same Python frontier. Regenerate both through `tests/compare/direct/unit21_schema_seed.py`; no runtime Python parser or migration runs. The public CLI target is a required real app-relative executable. There is no default or success shim. Private launch uses POSIX exec to preserve the inherited listener, with the existing proxy/environment allowlist. The actual supervisor payload, official operator binding and production environment remain separate prerequisites. Source-only export has null runtime metadata and does not prove those prerequisites.

The separate archive proof workflow builds one real Python v1 payload and uses both real archive producers. It compares complete file streams, modes, links, hard-link relationships, and exact modification times after both extractors. Public streaming TAR measurements omit raw extension counts and padding. Compressed/expanded bytes, spool allocation, and actual free space are recorded. Source/output allocation and aggregate disk peaks remain unmeasured. OS memory receipts are not an aggregate process-tree peak. Full Node v2 construction, runtime admission/start, and the sealed Node license inventory remain open. This workflow uploads JSON only and cannot publish a release.

Runtime admission requires the caller's invocation child owner. One absolute 120-second budget covers host checks, the trusted runtime probe, and all tool probes. Each probe has a 45-second maximum and joins its own actual child close. It never waits on unrelated children through the shared owner. Shutdown seals and forces that same owner. Production update composition must retain admission's raw continuation before finalization. That caller remains open.

The v2 probe uses resolved host Node 22, at least 22.18.0. Trusted host-only operations precede the library baseline and every candidate import. The map includes `.so` libraries and `.node` addons. Every addon must resolve inside the candidate, including a baseline addon. Other new loads must resolve inside it or match declared glibc libraries. The probe resolves every declared production package and exercises SQLite and Koffi. It requires the retained usage worker module and probes the server Claude SDK binary among nine offline tools. It never starts an application or a provider session.

Host/report comparisons use synthetic observations against Python. The actual process case proves the timer, private environment, complete JSON capture, and local child join. A hosted-only Linux branch separately pins and reaps both group members. These checks do not prove a sealed Node v2 payload. The Python bridge prerequisite is merged through staging merge 10, PR 788 at `990538d5e804b43d36ae8d3bbf0b408c6acc84f4`. No unmerged admission patch is used.
