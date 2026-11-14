This slice supplies application identity, receipt-bound archives, database-frontier rules, static native inventory, and offline runtime admission. Python owns migrations. A TS database must already match the candidate frontier. Build, installation, and publication follow in separate reviewed slices.

Unpack opens the compressed input once and passes that descriptor to a required invocation-owned Node child. A first compressed pass checks the receipt hash before format parsing. Gunzip rereads that same descriptor from offset0 and inflates once. The existing `tar` package lists a bounded private TAR spool and checks every effective member. Its extractor then reads that same spool. Extraction never reopens the archive pathname. The parent creates the private output exclusively only after full hash and member validation. An appeared directory refuses. The API returns only after actual child close, final metadata/fsync, and spool cleanup. The installer owns the later rename.

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
| Stream chunks | 64 KiB |

Invalid input and I/O or child failures return `AppValidationError`.

Space admission has three checks:

- Before the spool, require twice the compressed size plus 1 GiB. A shortage returns `app archive has insufficient free space before spooling`.
- During spool writes, require the reserve plus the next batch, at most 1 MiB. A shortage returns `app archive exhausted free-space reserve while spooling`.
- After the spool, require its exact expanded size plus 1 GiB before output creation. A shortage returns `app archive has insufficient free space for output`.

The cited tree needs roughly 6.6 GiB at peak, including reserve, before filesystem overhead. Concurrent users can still exhaust a filesystem. The parent removes partial output after failure.

The shared admission policy rejects sparse metadata, path escapes, duplicate effective names, special modes, links outside the archive, and members behind links or files. Package defaults that sanitize or skip unsafe entries do not grant admission. Forward hard links wait for their regular targets. Ordinary file and directory metadata receives a final fsync.

The Manager accepted package-native modification times. `tar` decodes times into JavaScript Dates, with millisecond precision. Native Node APIs restore those values. The custom nanosecond conversion and native `futimens` binding are removed. TAR numeric fields, extension handling, text decoding, and end framing follow the package.

The existing comparison sequence covers synthetic ordinary archives and hostile input. Full real-producer tree equality, timestamp precision, child memory/disk cost, and cancellation evidence remain hosted gates. Each actual Python/TS producer must supply its real archive for Python/TS extraction comparison. Sealed-package evidence must retain every package's own license files and name packages that ship none. Development dependency scans do not prove that inventory. No root notice entry is required for the installed tar dependency.

Package license inventory reads the validated extracted sealed tree before admission tools run. It records metadata roots, package-slot aliases, retained file hashes, literal declared paths, and packages without their own license files. Nested package metadata defines ownership boundaries. Notices remain separate from license files. This evidence does not decide legal compatibility. Actual sealed Node package counts and missing names remain a hosted publication gate.

The fixed publication build entry uses one isolated native owner to build, seal, extract, inventory and admit the actual Linux archive. It checks the artifact again after admission. Its evidence records the exact archive and inventory bytes. Publication entries have one 30-minute deadline, including cleanup. Git calls have a 30-second bound and the unchanged Mac validator has a five-minute bound.

The final receipt binds the downloaded ZIP bytes to GitHub artifact IDs and the current run attempt. It reads the manifest and runtime inventory from the sealed archive itself. It invokes the unchanged Mac validator and reads the human iOS review from HEAD in a clean source tree. The audit records that review's commit and author. The main-only workflow gates the receipt on the same-run sealed Node 22 build, native admission, clean-root boot and stop, and the installer smoke script when present. Real installation and rollback belong to the trial instance.

The builder exports one exact Git commit through private bare metadata and read-only source objects. Only committed `.gitattributes` controls export. Host Git configuration, replacement references, and source `info/attributes` do not control it. The builder creates Node runtime metadata with the existing static native closure. Its required execution scope supplies the actual child owner, abort signal and original absolute deadline. Explicit null deadline preserves standalone build policy. Server calls require their existing finite deadline and invocation owner. Git, npm, readelf, patchelf, ldconfig and dpkg-query all use that same scope. Commands capture output in private files, refuse overflow before parsing and join actual close before cleanup. No per-command join waits for unrelated invocation children.

Raw Git export uses the same owned unpack child and bounded TAR reader, with an explicit source-export request and completion. It preserves Git export attributes and the existing inert PAX comment. It creates no fake release manifest. Release extraction keeps its gzip, receipt hash and manifest requirements. Source cancellation requests stop for the exact unreaped group before input teardown, then joins actual close before it removes owned output or spool. The builder refuses a candidate symlink before path resolution. It validates the payload and removes build metadata before the final rename. No successful-path check or cleanup follows that rename. Cancellation cannot undo a rename already in progress. Cleanup failures retain the original failure as the cause and first aggregate member.

The complete builder requires a committed generated migration graph and schema seed for the same Python frontier. Regenerate both through `tests/compare/direct/unit21_schema_seed.py`; no runtime Python parser or migration runs. The public CLI target is a required real app-relative executable. There is no default or success shim. Private launch uses POSIX exec to preserve the inherited listener, with the existing proxy/environment allowlist. Dependency builds write the installer file manifest after native completion and entry wrappers, before the artifact digest. Runtime validation and classification require that manifest and the installer entry. The actual supervisor payload, official operator binding and production environment remain separate prerequisites. Source-only export has null runtime metadata and does not prove those prerequisites.

VPS Mac readiness constructs one real MacUpdateService with the runtime’s actual store, provider, lazy ready-release callback, and invocation child owner. A genuinely absent executor stays absent. Inspection uses one held descriptor, a bounded hash-first read, and one inflate pass. It creates no spool or output tree. The null-manifest sentinel and last Python marker retain source behavior. Extraction link and mode restrictions do not apply to inspection.

Mac inspection shares the package parser and resource limits. It keeps its separate manifest and runtime-marker rules. It does not apply extraction restrictions to unrelated links, duplicate names, or modes. Malformed manifests still refuse through the existing JSON boundary.

The physical TAR caps are 8 GiB expanded, 250,000 headers, 1 MiB per extension body, and 64 MiB cumulative metadata/path/link bytes. Existing Mac limits stay at 2 GiB compressed, 4 GiB logical member bytes, and 16 KiB for the manifest.

Acceptance requires that the unchanged real Mac producer passes the hosted compatibility gate. The first run passed unchanged on the PR merge tree for head `37333bd6e`, with a 268.48-second build and 7.59-second inspection. The gate runs on every Darwin job and requires its evidence files. Every final head must pass it after source changes. The direct report measures four archive observations and parser diagnostics, with accepted:true. Other accepted limits and framing rules have source review, without a measured boundary claim. [The difference registry](../../DEVIATIONS.md#mac-archive-inspection-policies-and-json-diagnostics--accepted-conditionally) records the scope and proof. Default factory activation and executor transport remain later work.

The fixed stopped-database capture entry publishes unavailable evidence before it takes runtime ownership and reads one zero-wait readonly snapshot. It changes no application rows or schema. The fixed restore entry delegates to the existing stopped restore primitive. Both entries are release payload files. The external installer owns their deadlines and actual process cleanup. Real installer integration and copied-runtime independence remain open gates.

Installer runtime classification has its own bounded probe. The two-second capture budget starts immediately before the selected helper starts. Health follows HTTP redirects with Python's redirect limits and the same five-second connection/read inactivity timeout. Its overall timeout still uses Python's late check. Actual test contexts require loopback health URLs, including redirect targets. A closed stderr pipe does not stop installation.

Static native inventory is packaging-only. Its command helper and every native tool call require the caller's invocation owner, stop signal, and original deadline. A later build/update binding must supply that same execution scope inside APP. Synthetic loader lookup and outside-target checks remain in the existing release comparison. They do not prove a full native closure.

The separate archive proof workflow builds one real Python v1 payload and uses both real archive producers. It compares complete file streams, modes, links, hard-link relationships, and modification times after both extractors at the accepted package precision. Public streaming TAR measurements omit raw extension counts and padding. Compressed/expanded bytes, spool allocation, and actual free space are recorded. Source/output allocation and aggregate disk peaks remain unmeasured. OS memory receipts are not an aggregate process-tree peak. Full Node v2 construction, runtime admission/start, and the sealed Node license inventory remain open. This workflow uploads JSON only and cannot publish a release.

Runtime admission requires the caller's invocation child owner. One absolute 120-second budget covers host checks, the trusted runtime probe, and all tool probes. Each probe has a 45-second maximum and joins its own actual child close. It never waits on unrelated children through the shared owner. Shutdown seals and forces that same owner. Production update composition must retain admission's raw continuation before finalization. That caller remains open.

The v2 probe uses resolved host Node 22, at least 22.18.0. Trusted host-only operations precede the library baseline and every candidate import. The map includes every file-backed executable mapping except the resolved host Node executable. Every addon must resolve inside the candidate, including a baseline addon. Other new loads must resolve inside it or match canonical glibc names in real system library directories. The probe resolves every declared production package and exercises SQLite and Koffi. It imports every declared production package through the import resolver and starts the real usage worker. One refresh uses injected clock and rates replies, without a provider or network request. The worker remains alive through the executable-map snapshot, then termination joins its exit. The probe also checks the server Claude SDK binary among nine version/help tools. It never starts an application or a provider session.

Host/report comparisons use synthetic observations against Python. The actual process case proves the timer, private environment, complete JSON capture, and local child join. A hosted-only Linux branch separately pins and reaps both group members. These checks do not prove a sealed Node v2 payload. The Python bridge prerequisite is merged through staging merge 10, PR 788 at `990538d5e804b43d36ae8d3bbf0b408c6acc84f4`. No unmerged admission patch is used.

`units.ts` asynchronously renders the new service and watchdog templates with their environment files. Managed callers supply explicit socket and lease paths outside both private temporary directories. The host Node 22 >=22.18 prerequisite applies before the legacy Python transition too. Start classifies the held, checked application directory. Stop addresses the running supervisor through its private runtime record and pinned working directory. The real launchers retain full validation. Candidate admission rejects a null contract. The installed legacy-null branch requires its explicit SHA and real private Python.

The templates preserve config/refusal inputs, private runtime placement, client80 and per-phase unit90 margins around supervisor60. The watchdog keeps its300-second command limit and60/30/5 timer schedule through a fixed private entry. These are source producers, not an installed or complete production release. The copied installer, default environment, official public CLI acceptance and sealed-package license inventory remain open. The reserved publisher workflow is unchanged.

The probe executes candidate code with the host user's rights and is not a sandbox.
Descendants of a tool that exits normally are not swept, as in Python.

Receipt staging binds the receipt hash, complete manifest, and fresh runtime admission before the final sibling rename. A matching retained stage gets fresh admission without another download. Permission-restoring cleanup applies only to the exclusive workspace. Staging and schema status retain their exclusive paths until an unsettled probe’s actual cleanup completes. Original probe and cleanup failures remain visible. Staging forwards the invocation stop signal to artifact downloads and the ZIP member pipeline. It checks that signal before candidate admission and rename. Its raw provider operation and temporary cleanup remain part of the stage promise.

The schema adapter checks the complete v2 frontier through a zero-timeout readonly snapshot. V1 status uses the admitted candidate’s own isolated Python helper. UpdateService awaits that result and fences backup after shutdown. Actual v1 execution and sealed v2 admission remain hosted gates.

Dependency installation uses a private npm home and ignores host npm configuration and Node loader variables. Dependency lifecycle scripts remain enabled. The two agent CLI packages retain Python's `@latest` policy. Their bytes can differ across builds of the same source SHA. The artifact digest binds the exact installed bytes.

Mac inspection takes the invocation signal and one absolute 120-second deadline before it opens the input. Its bounded observer retains a separate full-cleanup promise when cancellation cannot settle immediately. Mac preparation joins that promise before temporary-file removal or the next queued flight. Both descriptor passes compute hashes, and the second must equal the first before acceptance.

Mac downloads preserve Python’s 30-second metadata and 60-second stream-read timeouts. The same invocation signal aborts metadata, download, ZIP reads, and queued preparation. These network timeouts do not bound disk writes or the complete download.
