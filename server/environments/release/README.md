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

VPS Mac readiness constructs one real MacUpdateService with the runtime’s actual store, provider, lazy ready-release callback, and invocation child owner. A genuinely absent executor stays absent. Inspection uses one held descriptor, a bounded hash-first read, and one inflate pass. It creates no spool or output tree. The null-manifest sentinel and last Python marker retain source behavior. Extraction link and mode restrictions do not apply to inspection.

The Manager accepted four groups. They cover global PAX size and NUL refusal, UTF16/32/non-scalar UTF8 manifest refusal, parser diagnostics, and the bounded, closed TAR policy. The last group includes sparse input, unknown PAX keys, unsupported layouts or path encodings, invalid UTF8, and closed numeric/PAX/end framing. Local PAX size and UTF8 BOM remain supported. Malformed JSON still refuses, with Node SyntaxError text instead of Python JSONDecodeError text.

The physical TAR caps are 8 GiB expanded, 250,000 headers, 1 MiB per extension body, and 64 MiB cumulative metadata/path/link bytes. Trailing zero padding must occupy 1–20 KiB in whole 512-byte blocks. Existing Mac limits stay at 2 GiB compressed, 4 GiB logical member bytes, and 16 KiB for the manifest.

Acceptance requires that the unchanged real Mac producer passes the hosted compatibility gate. The first run passed unchanged on the PR merge tree for head `37333bd6e`, with a 268.48-second build and 7.59-second inspection. The gate runs on every Darwin job and requires its evidence files. Every final head must pass it after source changes. The direct report measures four archive observations and parser diagnostics, with accepted:true. Other accepted limits and framing rules have source review, without a measured boundary claim. [The difference registry](../../DEVIATIONS.md#mac-archive-inspection-policies-and-json-diagnostics--accepted-conditionally) records the scope and proof. Default factory activation and executor transport remain later work.

The fixed stopped-database capture entry publishes unavailable evidence before it takes runtime ownership and reads one zero-wait readonly snapshot. It changes no application rows or schema. The fixed restore entry delegates to the existing stopped restore primitive. Both entries are release payload files. The external installer owns their deadlines and actual process cleanup. Real installer integration and copied-runtime independence remain open gates.

Installer runtime classification has its own bounded probe. The two-second capture budget starts immediately before the selected helper starts. Health follows HTTP redirects with Python's redirect limits and the same five-second connection/read inactivity timeout. Its overall timeout still uses Python's late check. Actual test contexts require loopback health URLs, including redirect targets. A closed stderr pipe does not stop installation.

Static native inventory is packaging-only. Its command helper and every native tool call require the caller's invocation owner, stop signal, and original deadline. A later build/update binding must supply that same execution scope inside APP. Synthetic loader lookup and outside-target checks remain in the existing release comparison. They do not prove a full native closure.

The separate archive proof workflow builds one real Python v1 payload and uses both real archive producers. It compares complete file streams, modes, links, hard-link relationships, and exact modification times after both extractors. Public streaming TAR measurements omit raw extension counts and padding. Compressed/expanded bytes, spool allocation, and actual free space are recorded. Source/output allocation and aggregate disk peaks remain unmeasured. OS memory receipts are not an aggregate process-tree peak. Full Node v2 construction, runtime admission/start, and the sealed Node license inventory remain open. This workflow uploads JSON only and cannot publish a release.

Runtime admission requires the caller's invocation child owner. One absolute 120-second budget covers host checks, the trusted runtime probe, and all tool probes. Each probe has a 45-second maximum and joins its own actual child close. It never waits on unrelated children through the shared owner. Shutdown seals and forces that same owner. Production update composition must retain admission's raw continuation before finalization. That caller remains open.

The v2 probe uses resolved host Node 22, at least 22.18.0. Trusted host-only operations precede the library baseline and every candidate import. The map includes every file-backed executable mapping except the resolved host Node executable. Every addon must resolve inside the candidate, including a baseline addon. Other new loads must resolve inside it or match canonical glibc names in real system library directories. The probe resolves every declared production package and exercises SQLite and Koffi. It imports every declared production package through the import resolver and starts the real usage worker. One refresh uses injected clock and rates replies, without a provider or network request. The worker remains alive through the executable-map snapshot, then termination joins its exit. The probe also checks the server Claude SDK binary among nine version/help tools. It never starts an application or a provider session.

Host/report comparisons use synthetic observations against Python. The actual process case proves the timer, private environment, complete JSON capture, and local child join. A hosted-only Linux branch separately pins and reaps both group members. These checks do not prove a sealed Node v2 payload. The Python bridge prerequisite is merged through staging merge 10, PR 788 at `990538d5e804b43d36ae8d3bbf0b408c6acc84f4`. No unmerged admission patch is used.

The probe executes candidate code with the host user's rights and is not a sandbox.
Descendants of a tool that exits normally are not swept, as in Python.
Mac inspection takes the invocation signal and one absolute 120-second deadline before it opens the input. Its bounded observer retains a separate full-cleanup promise when cancellation cannot settle immediately. Mac preparation joins that promise before temporary-file removal or the next queued flight. Both descriptor passes compute hashes, and the second must equal the first before acceptance.

Mac downloads preserve Python’s 30-second metadata and 60-second stream-read timeouts. The same invocation signal aborts metadata, download, ZIP reads, and queued preparation. These network timeouts do not bound disk writes or the complete download.
