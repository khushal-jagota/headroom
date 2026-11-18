This package supplies application identity, receipt-bound archives, database-frontier rules, static native inventory, offline runtime admission, and the release build and publication entries. A TS database must be at the candidate's head or an earlier revision on its own schema-step chain; the installer runs the pending steps.

The hosted `mac_home_main.ts` producer composes a complete Darwin arm64 home from an exact Git export, prepared production server packages, provider packages, and built web assets. Prepared inputs must come from that same checked revision. Host Node 22.18+ and matching build headers remain outside the app. The producer retains package licenses and static seeds, writes real home entrypoints, and seals the Darwin v2 contract, native inventory, and frozen installer recipe before the archive hash. Mach-O dependencies must resolve inside the candidate or use the declared system library prefixes. Unknown external search paths refuse. The CLI retires the unpacked candidate, admits the sealed archive, and runs the shared foreground boot proof. Only then does it write `mac-home-build.json`, bound to `admission.json` and `boot-proof.json` by their hashes. Requests and logs stay under `proof-work/`. Direct `build_mac_home` callers receive an unproved archive and no publication metadata. The workflow supplies actual GitHub artifact identities after upload.

Release manifests, inventories, and publication receipts use native JSON values. Publication evidence uses safe integer numbers for GitHub identities, archive sizes, and counts. Hashes still bind the exact archive and inventory bytes.

Unpack opens the compressed input once and passes that descriptor to a required invocation-owned Node child. A first compressed pass checks the receipt hash before format parsing. Gunzip rereads that same descriptor from offset0 and inflates once. The existing `tar` package lists a bounded private TAR spool and checks every effective member. Its extractor then reads that same spool. Extraction never reopens the archive pathname. The parent creates the private output exclusively only after full hash and member validation. An appeared directory refuses. The API returns only after actual child close, final metadata/fsync, and spool cleanup. The installer owns the later rename.

The spool is `0600` inside an exclusive `0700` directory. Output also starts and remains private at `0700`. On failure, the parent removes only its own output and spool, after actual child close. Archived directory modes cannot prevent that cleanup. The shared child owner supplies stop/force authority. No private owner, timer, or second process registry exists.

The reader uses these fixed budgets:

| Resource | Limit |
| --- | --- |
| Compressed input | 4 GiB |
| Expanded TAR | 8 GiB |
| One member | 4 GiB |
| Entries and nonempty metadata records | 250,000 |
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

The Manager accepted package-native modification times. `tar` decodes times into JavaScript Dates, with millisecond precision. Native Node APIs restore those values. TAR numeric fields, extension handling, text decoding, and end framing follow the package.

Sealed-package evidence must retain every package's own license files and name packages that ship none. Development dependency scans do not prove that inventory. No root notice entry is required for the installed tar dependency.

Package license inventory reads the validated extracted sealed tree before admission tools run. It records metadata roots, package-slot aliases, retained file hashes, literal declared paths, and packages without their own license files. Nested package metadata defines ownership boundaries. Notices remain separate from license files. This evidence does not decide legal compatibility. Actual sealed Node package counts and missing names remain a hosted publication gate.

The fixed publication build entry uses one isolated native owner to build, seal, extract, inventory and admit the actual Linux archive. It checks the artifact again after admission. Its evidence records the exact archive and inventory bytes. Publication entries have one 30-minute deadline, including cleanup. Git calls have a 30-second bound and the unchanged Mac validator has a five-minute bound.

The final receipt binds the downloaded ZIP bytes to GitHub artifact IDs and the current run attempt. It reads the manifest and runtime inventory from the sealed archive itself. It invokes the unchanged Mac validator and reads the committed iOS review from HEAD in a clean source tree. The audit records that review's commit and author. The main-only workflow gates the receipt on the same-run sealed Node 22 build, native admission, clean-root boot and stop, and the installer smoke script when present. Real installation and rollback belong to the trial instance.

The builder exports one exact Git commit through private bare metadata and read-only source objects. Only committed `.gitattributes` controls export. Host Git configuration, replacement references, and source `info/attributes` do not control it. The builder creates Node runtime metadata with the existing static native closure. Its required execution scope supplies the actual child owner, abort signal and original absolute deadline. Explicit null deadline preserves standalone build policy. Server calls require their existing finite deadline and invocation owner. Git, npm, readelf, patchelf, ldconfig and dpkg-query all use that same scope. Commands capture output in private files, refuse overflow before parsing and join actual close before cleanup. No per-command join waits for unrelated invocation children.

Raw Git export uses the same owned unpack child and bounded TAR reader, with an explicit source-export request and completion. It preserves Git export attributes and the existing inert PAX comment. It creates no fake release manifest. Release extraction keeps its gzip, receipt hash and manifest requirements. Source cancellation requests stop for the exact unreaped group before input teardown, then joins actual close before it removes owned output or spool. The builder refuses a candidate symlink before path resolution. It validates the payload and removes build metadata before the final rename. No successful-path check or cleanup follows that rename. Cancellation cannot undo a rename already in progress. Cleanup failures retain the original failure as the cause and first aggregate member.

The complete builder requires a committed generated migration graph and schema seed whose seed revision is the graph's single head and whose graph is the frozen Python baseline plus one line of TypeScript schema steps. Regenerate both with `node server/core/database/tools/generate_seed.ts`. No migration runs at build time. The public CLI target is a required real app-relative executable. There is no default or success shim. Private launch uses POSIX exec to preserve the inherited listener, with the existing proxy/environment allowlist. Dependency builds write the installer file manifest after native completion and entry wrappers, before the artifact digest. Runtime validation and classification require that manifest and the installer entry. Source-only export has null runtime metadata.

VPS Mac readiness constructs one real MacUpdateService with the runtime’s actual store, provider, lazy ready-release callback, and invocation child owner. A genuinely absent executor stays absent. Inspection uses one held descriptor, a bounded hash-first read, and one inflate pass. It creates no spool or output tree. Inspection requires one manifest and uses the last executable marker for each runtime path. Extraction link and mode restrictions do not apply to inspection.

Mac inspection shares the package parser and resource limits. It keeps its separate manifest and runtime-marker rules. It does not apply extraction restrictions to unrelated links, duplicate names, or modes. Malformed manifests still refuse through the existing JSON boundary.

The TAR caps are 8 GiB expanded, 250,000 package entry events, 1 MiB per extension body, and 64 MiB cumulative metadata/path/link bytes. Empty extension headers emit no event and count only toward expanded bytes. Existing Mac limits stay at 2 GiB compressed, 4 GiB logical member bytes, and 16 KiB for the manifest.

The real Mac producer must pass the hosted compatibility gate. The gate runs on every Darwin job and requires its evidence files. [The difference registry](../../DEVIATIONS.md#mac-archive-inspection-policies) records the accepted inspection policies.

The fixed stopped-database capture entry publishes unavailable evidence before it takes runtime ownership and reads one zero-wait readonly snapshot. It changes no application rows or schema. The fixed restore entry delegates to the existing stopped restore primitive. Both entries are release payload files. The external installer owns their deadlines and actual process cleanup.

Installer runtime classification has its own bounded probe. The two-second capture budget starts immediately before the selected helper starts. Health follows HTTP redirects up to a fixed limit, with a five-second connection/read inactivity timeout. Its overall timeout is checked after each failed poll. Actual test contexts require loopback health URLs, including redirect targets. A closed stderr pipe does not stop installation.

Static native inventory is packaging-only. Its command helper and every native tool call require the caller's invocation owner, stop signal, and original deadline.

Install-time admission (`admit_runtime`) checks a release only against itself and the host: the receipt manifest, the release's own contract and inventory, its launch executables `bin/panels` and `bin/panels-launcher`, and the host requirements that its contract declares. It never compares the release with this version's file lists or contract constants, and it runs no release code. The build proof (`prove_built_runtime`) adds the probe and the driver tool commands below. Publication and the Mac home proof use it on a release that the same code built.

Runtime admission requires the caller's invocation child owner. One absolute 120-second budget covers host checks, the trusted runtime probe, and all tool probes. Each probe has a 45-second maximum and joins its own actual child close. It never waits on unrelated children through the shared owner. Shutdown seals and forces that same owner. Production update composition retains admission's raw continuation before finalization.

The v2 probe uses resolved host Node 22, at least 22.18.0. Trusted host-only operations precede the library baseline and every candidate import. The map includes every file-backed executable mapping except the resolved host Node executable. Every addon must resolve inside the candidate, including a baseline addon. Other new loads must resolve inside it or match canonical glibc names in real system library directories. The probe resolves every declared production package and exercises SQLite and Koffi. It imports every declared production package through the import resolver and starts the real usage worker. One refresh uses injected clock and rates replies, without a provider or network request. The worker remains alive through the executable-map snapshot, then termination joins its exit. The probe also checks the server Claude SDK binary among nine version/help tools. It never starts an application or a provider session.

`units.ts` asynchronously renders the new service and watchdog templates with their environment files. Managed callers supply explicit socket and lease paths outside both private temporary directories. Start classifies the held, checked application directory. Stop addresses the running supervisor through its private runtime record and pinned working directory. The real launchers retain full validation. Candidate admission rejects a null contract.

The templates preserve config/refusal inputs, private runtime placement, client80 and per-phase unit90 margins around supervisor60. The watchdog keeps its300-second command limit and60/30/5 timer schedule through a fixed private entry.

The probe executes candidate code with the host user's rights and is not a sandbox.
Descendants of a tool that exits normally are not swept.

Receipt staging binds the receipt hash, complete manifest, and fresh runtime admission before the final sibling rename. A matching retained stage gets fresh admission without another download. Permission-restoring cleanup applies only to the exclusive workspace. Staging and schema status retain their exclusive paths until an unsettled probe’s actual cleanup completes. Original probe and cleanup failures remain visible. Staging forwards the invocation stop signal to artifact downloads and the ZIP member pipeline. It checks that signal before candidate admission and rename. Its raw provider operation and temporary cleanup remain part of the stage promise.

The schema adapter checks the complete v2 frontier through a zero-timeout readonly snapshot. UpdateService awaits that result and fences backup after shutdown.

Dependency installation uses a private npm home and ignores host npm configuration and Node loader variables. Dependency lifecycle scripts remain enabled. The two agent CLI packages install `@latest`. Their bytes can differ across builds of the same source SHA. The artifact digest binds the exact installed bytes.

Mac inspection takes the invocation signal and one absolute 120-second deadline before it opens the input. Its bounded observer retains a separate full-cleanup promise when cancellation cannot settle immediately. Mac preparation joins that promise before temporary-file removal or the next queued flight. Both descriptor passes compute hashes, and the second must equal the first before acceptance.

Mac downloads use 30-second metadata and 60-second stream-read timeouts. The same invocation signal aborts metadata, download, ZIP reads, and queued preparation. These network timeouts do not bound disk writes or the complete download.
