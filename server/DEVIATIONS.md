# Accepted design decisions

This file records server decisions that the owner or Manager accepted, with their reasons. Each entry is a deliberate choice. Do not change one without that acceptance.

## Linux host Node major — accepted

The Manager accepts Node 22-only host admission for the native release.
The version 2 contract fixes `node_major: 22`, and native modules use that Node ABI.
A newer major can load those modules incorrectly. The builder and join recipe require Node 22 >=22.18.
TypeScript refuses a newer major. Its minimum-version diagnostic names that restriction.

## Native Linux executor archive

The Linux x86_64 executor archive holds inactive inventory entries for the ARM64 prebuilds that better-sqlite3 bundles.
The actual AArch64 header and matching package selector must agree. These entries retain their byte hashes and never enter the active dependency closure.
Other unknown ELF architectures refuse.

## Backend discovery in test mode

With `PLAN_TEST_MODE=1`, TypeScript composes an empty recording environment and no production providers.
Without explicit recording fixtures, each backend card reports not installed.
Production subprocess and HTTP doors also refuse test contexts before external I/O.
Tests therefore never probe the machine.

## Projection rebuild on the writer connection

Projection rebuild uses TEMP tables on the single writer connection in batches of 500 events. The Manager accepted 373.7 seconds for 200,000 events. Speed it up before the first projection-version change ([measured profile](lifecycle/REBUILD_PROFILE.md)). Sustained-load latency is not measured.

## Fixed measure and Goal depth budgets

The migration Manager accepted this policy on November 12, 2026, for Unit 12.

TypeScript uses fixed named depth limits and weights in `server/measures/configuration.ts`:

- Evaluation budget: 991. Each reference costs four units. Each expression edge costs one. Goal actuals start with two units.
- Formula parser budget: 990. Reference leaves add zero, date checks add one, and number/period checks add two. Graph traversal contributes its depth.
- Graph and demand limits: 991.

These values are fixed policy, not a model of an interpreter stack. TypeScript reads no interpreter setting.
The first applicable budget determines the boundary error. Each refusal returns validation status 400 without a new row.

See [the measure README](measures/README.md) for the fixed policy and proof commands.

## Managed Linux supervisor stop

The Manager accepted the following stop policy on November 12, 2026.

On managed Linux, TypeScript requires a prior authenticated stop intent for the current supervisor incarnation, then the exact supervisor signal.
A bare signal fails closed. Foreground launches keep ordinary signals.

The reason is the accidental-stop threat and simpler packaging for Electron and a Mac-only server.
The intent guard refuses accidental bare signals without a compiled wrapper, platform binary, signing step, or extra relocation target.
Prior intent does not authenticate the signal sender itself.
The design does not protect against a broad kill of the application child or same-user SIGKILL.

TypeScript keeps the configured control socket address. It requires an owned real parent directory at mode 0700 and creates the socket at mode 0600 without a wider-permission window. Restart remains a same-user operation without capability authentication.

APP uses a 30-second shared grace, a 45-second hard trigger, and an owned-force attempt bounded by original stop plus 55 seconds.
Packaged policy uses a separate supervisor APP fallback at 60 seconds, stop client at 80 seconds, and outer unit phase at 90 seconds.
An APP timer cannot preempt blocked JavaScript or native code. Independent Worker services are outside the APP unit cgroup.
External supervisor fallback alone cannot prove those services gone if APP blocks before its owned-unit force attempt.
A managed Mac-only server requires a later decision.

See [the accepted design](lifecycle/SUPERVISOR_DESIGN.md) for the protocol and failure limits.

## Application hard shutdown — accepted

The Manager accepted a shared 30-second grace and a hard APP trigger at 45 seconds.
APP attempts force only for still-owned, unreaped groups and retained unique Worker unit capabilities.
The manager-command batch has an original deadline at stop plus 55 seconds. Failure remains explicit in the log.
APP then exits 1 without destructive database finalizers while actual work remains.
When all work settles, normal finalizers run first and the actual entry exits 0 or 1 for the recorded result.
Embedded servers do not receive that process-completion capability.
Timers cannot interrupt a blocked APP loop or synchronous native call. Packaged supervisor and unit fallback remain separate proof gates.

## Composed local-only Ticket execution — accepted and temporary

The Manager requires a temporary refusal while the production composition has no executor router.
A Ticket whose locale differs from the current local execution locale keeps its existing conversation, claims, queue, and recovery state.
The composed policy refuses explicit work before effects and skips unavailable background work before claims or wake batches.
Persisted compatible Mac presence is not authority to launch a VPS-local adapter for a Mac Ticket.
This policy does not claim that a composed release has an executor ready.

## VPS metrics integer configuration

The migration Manager accepted this admission boundary for these ten integer environment overrides:

- `PLAN_VPS_METRICS_METRICS_PAYLOAD_BYTES`
- `PLAN_VPS_METRICS_PROCESS_PAYLOAD_BYTES`
- `PLAN_VPS_METRICS_PROCESS_SNAPSHOT_BYTES`
- `PLAN_VPS_METRICS_TRANSACTION_PAYLOAD_BYTES`
- `PLAN_VPS_METRICS_DATABASE_BYTES`
- `PLAN_VPS_METRICS_WAL_BYTES`
- `PLAN_VPS_METRICS_READ_WORKERS`
- `PLAN_VPS_METRICS_PROCESS_PAGE_ROWS`
- `PLAN_VPS_METRICS_HISTORY_POINTS`
- `PLAN_VPS_METRICS_SERVICE_OUTPUT_BYTES`

TypeScript refuses values outside ±9,007,199,254,740,991 with a native validation error that names the field and its exact integer limit.
It does not silently round a larger value.

The boundary exceeds realistic worker/page counts and memory/database budgets.
Explicit refusal keeps operational limits exact without spreading BigInt configuration through number-based runtime APIs.
Existing positive-value and consistency checks still apply. The ten keys share the same admission predicate.

## JavaScript source execution limits

The migration Manager accepted these limits for the JavaScript script contract on November 12, 2026:

- 8 MiB combined control, stdout, and stderr output per run.
- 64 MiB aggregate decoded HTTP response bodies per run.
- 256 MiB V8 old-space through the child Node process option. This is not a total resident-memory guarantee.
- The configured wall-time limit, 30 seconds by default.

`measure_sources/runtime/runtime.test.ts` proves each limit with actual children.

The Manager also accepted these script-contract rules:

- TypeScript constructs a filtered environment with private `HOME`/`TMPDIR` and fixed `LANG`. It retains only `TZ`, `PATH`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, their lower-case forms, `SSL_CERT_FILE`, `SSL_CERT_DIR`, and `NODE_EXTRA_CA_CERTS`. No `PLAN_*` variable or provider home passes through. The environment test pins this exact list.
- Non-wall limit notices say "The run exceeded an execution limit."
- JavaScript diagnostics retain only `SyntaxError`, `TypeError`, `ReferenceError`, and `RangeError`.
- A script's explicit `process.exit(0)` is a runtime failure because it omits the completion protocol.

The child runs as the same operating-system user and can read what the server can read. Environment filtering is not a sandbox. The author prohibitions are policy, not filesystem or process isolation.

## Codex process cleanup and blocked writes

The migration Manager accepted these rules on November 12, 2026:

- TypeScript cleans up the owned process group. Worker starts use the shared supervision runtime.
- A write failure immediately fails all pending requests and suspended writes.
- A request deadline includes time in the write queue and a blocked stdin write.

These rules do not let one request's cancellation stop the whole app-server, or a timeout reject unrelated pending requests.
The process implementation never signals a group after it observes the direct child's exit.

## Recorded usage test composition and shutdown

- Default TypeScript test composition selects synthetic provider homes beside the
  test database and supplies a rates fetcher that returns no rates.
  Explicit source directories and recording fetchers remain injectable. Tests do
  not read owner transcripts or contact the public rates source.
- Summary, rate-refresh, backend, and voice requests receive HTTP 503
  after the shared conversation write admission seals during shutdown. This retains
  ownership of previously admitted work through its drain.

## Codex ask withdrawal at shutdown

TypeScript bounds ask withdrawal before shutdown to two seconds. When that bound expires,
client stop breaks the wire and stops and joins the owned child. The bound prevents a
parked ask or blocked write from holding process cleanup open.

## Source credentials across redirect origins

A credential reference, or any caller-supplied header except `Accept` and `Content-Type`,
restricts source redirects to the same origin (scheme, host, and port). Header names are case-insensitive.
The fetch refuses before a cross-origin hop with a transport failure that names the refused origin,
without the credential value. This keeps credentials away from other origins.
At these three call sites, TypeScript limits the wait for headers and each body read without a total deadline.
Node fetch sets Host from the URL and ignores a caller-supplied Host.

## Codex sign-in admission after shutdown

TypeScript closes sign-in admission when shutdown begins. Later or queued starts fail with `CodexSignInUnavailable`.

The production provider set also closes catalog and usage admission, aborts its owned probes, and joins them before shutdown completes. Snapshot environment cleanup and sign-in cleanup both run even if either fails. An aggregate error retains their failures.

## Hermes ACP ordering and bounded retirement

The Manager accepted strict read-order handling for Hermes. TS finishes each notification, request admission, and asynchronous reply effect before it admits the next frame. This includes managed-image persistence. Normal sink callbacks have no deadline, so a slow database does not break a healthy wire.

The Manager also accepted a 15-second drain bound after the direct child exits while a descendant retains stdout. TS fails the remaining wire operations after the bound. The reused process owner never signals a group after its leader was reaped.

Steer and configuration RPCs each have a 15-second reply deadline. A hung earlier persistence callback can therefore delay those callers only until their own deadline. The official SDK then sends `$/cancel_request` and stops waiting for that RPC only. Steer returns uncertain. Configuration raises `NeedsRebind`, because the applied value is unknown, and the core retires that child before another prompt. During startup, cleanup precedes `SessionLoadFailed`. Other pending requests and the wire stay intact. Regular turns and image persistence gain no deadline, and TS discards no valid content to release the queue.

Whole-update validation uses the official SDK JSON schema before its best-effort deserializer. TS rejects out-of-schema updates whole, including numeric fields sent as strings, instead of coercing them. Real Hermes serializes numeric model fields as numbers.

## Separate Mac executor state

TypeScript defaults to `~/Library/Application Support/Panels/executor-typescript`.
It refuses a resolved root equal to or inside the retired `executor` root, including aliases through symlinks.
It also refuses an empty or relative `HOME`, before it opens storage.
The installation keeps its own app, supervisor, and explicit state root.

## Mac canonical skill publication ownership and durability

The Manager accepted these Mac-only safety rules during PR 817 source review on November 13, 2026.
Account publication follows staging ownership checks and never replaces foreign entries.
The Mac uses an explicit owned-link policy. It never recursively removes provider entries.
Before any publication or derived-state write, the Mac requires an absolute existing provider home and an existing real `skills` directory.
The trusted adapter host supplies that home separately from the snapshot. Its real path must equal its configured normalized path.
The Mac refuses linked homes or skills directories, managed-name collisions with files/directories/foreign links, case-only collisions, and link loops.
It preserves unrelated entries and dangling user links. Only links that resolve inside its own executor state root belong to it.
The Mac retains the provider directory and uses descriptor-relative unlink, symlink, and rename operations.
New links first use recognizable private temporary names. Replacement is atomic, after an immediate ownership recheck.
A later publication removes only recognized temporary links that still point into that executor's own root.

The held directory and rechecks prevent a swapped folder from redirecting deletion into a replacement folder.
They do not coordinate same-user programs that write that folder at the same time. Rename is not an atomic ownership comparison.
The Mac also refuses linked ancestors of its state root, including native `/tmp` and `/var` aliases. Use reviewed canonical native paths.

New generation files receive strict full flushes, and their directories receive flushes before atomic generation publication.
A damaged derived generation is preserved under a private `.invalid-*` name and rebuilt from the authoritative snapshot.
No source or provider user data is discarded by the rebuild.
The Mac host does not collect unreferenced generations or abandoned stages.

## CLI TLS trust

The migration Manager accepted Node's built-in default trust store for the CLI.
The CLI keeps no copied CA bundle. It honors `SSL_CERT_FILE`, `SSL_CERT_DIR`, and Node's startup `NODE_EXTRA_CA_CERTS` handling.
An explicit CA file or directory replaces the default roots. CA-directory lookup verifies lowercase OpenSSL subject-hash filenames and requires the `openssl` executable.
TLS failures use native Node wording at exit 2. A missing `SSL_CERT_FILE` and every invalid server URL syntax are clean validation errors at exit 1.

## Executor message and core admission

### Native JSON boundary

The Manager chose native JSON for the executor wire on November 14, 2026.
The shared schemas define message meaning and generate the TypeScript types.
The codec uses `JSON.parse`, cached Ajv validators, and `JSON.stringify`.
Integer fields and counters use safe integer numbers. Opaque payloads contain plain JSON values.
Nonfinite values refuse admission.
Private filesystem nanosecond timestamps and inode values retain Node bigint stat values and stay off the wire.
The command route forwards response disconnects into mailbox poll cancellation.

### Encoding validation

Each root-specific encoder validates against the shared schema before serialization.
Missing required fields and invalid command semantics refuse admission. Nullable fields without defaults require an explicit null.

### Canonical Mac Worker identity

The executor refuses before child creation when canonical Mac ownership disagrees with absent or different Worker identity hints.
A Mac Ticket therefore never selects a local child.

## Release archive reader policies — accepted

The release archive reader validates the complete member list before the destination exists. It returns `AppValidationError` for each refusal. Neither actual release producer emits the refused forms. It refuses:

- old GNU type `S` and every `GNU.sparse.*` PAX record
- unknown inert PAX keys, under the closed metadata-key policy
- an empty effective PAX file path
- bodies on directory or link members
- names that are not valid UTF-8
- any member whose parent is another archive link member, symbolic or hard, even when the link appears later.

The TS release writer can lose the fractional part of an mtime because node-tar writes whole seconds in an ordinary header. A fractional time that it writes in a PAX header stays intact. The reader keeps nanoseconds exactly.

## VPS mailbox shutdown admission

Shutdown seals new work immediately.
The mailbox rejects later owned work and returns an empty held poll before another SQL fetch.
Already admitted commits retain their announcements, and actual settlement retains pending failures and cleanup.
The mailbox also refuses nonfinite waits and waits above Node's maximum timer delay before SQL starts. Finite negative waits return at once.

## VPS presence shutdown and reply cursor

An admitted lease completes its commit, publication, and reply after shutdown seals the service.
Stop suppresses replacement, returned, and update callbacks that did not start before the seal.
It joins callbacks that already started. A skipped replacement stays owed, and a skipped update returns `no_update_check`.

Every accepted reply reads the canonical SQL mailbox maximum, including after the mailbox seal.
Mailbox rows never delete. The cursor therefore names existing rows that the next poll returns.

The presence seal rejects new leases, WebSocket refusals, and drain waits.
Cached reads, header fences, contact observations, and memory-only activity observations continue after stop.
A cold read returns the empty cached state without lazy SQL.
Contact observations update only memory after stop and start no timer.
A presence stop before lazy initialization performs no presence SQL read or write.
Final flush failures remain visible through actual settlement, as the lifecycle contract requires.
Normal-running contact flush failures log and remain retryable.

Presence commits are silent at the shared write door.
After commit succeeds, the service publishes its cache before it emits the one presence change signal.
Synchronous signal subscribers therefore read the committed presence state.

## Server event-loop task inspection — accepted, deferred

The Manager accepted explicit refusal because the TypeScript runtime cannot enumerate Promise await chains.
The typed `server-event-loop-tasks` callback returns exit 1, empty stdout, and an `unsupported` error.
Await-chain inspection remains deferred. Resource counts do not replace this diagnostic.
Node's built-in diagnostic report is the operator alternative.
Unexpected operator failures, including cleanup AggregateErrors, print the error text at exit 1.

## VPS file descriptor custody

The VPS file module uses Linux procfs descriptors.
It refuses unsupported systems before intake and supplies no path fallback.
It retains the original source and destination parents through copy and cleanup, and it keeps the checked input descriptor through its bounded input stream.
Its input owner anchors its byte root independently of the managed-file lookup, so a root swap cannot redirect input bytes.
Regular-file admission adds NONBLOCK before fstat, so a substituted FIFO cannot hang its open.
Cleanup failures carry explicit provenance and retain the original failure with the cleanup errors.
An upload timeout destroys the cancellable upload source, then closes and removes the incoming file before it joins the source's actual cleanup.

## Catalog cancellation withdraws waiting commands

TypeScript withdraws a waiting model-list command after an internal read cancellation, reconnection, or stop. A caller that cancels its own observation does not cancel or withdraw the shared read. TypeScript uses its own short cleanup signal, capped by the original shared shutdown deadline, and joins withdrawal before catalog settlement. The invocation keeps the mailbox open until that cleanup settles.

## Mac archive inspection policies

The Manager accepted these policies on November 13, 2026:

- Refuse global PAX size overrides and NUL text in names or PAX values.
- Refuse UTF16/32 and non-scalar UTF8 manifest encodings.
- Refuse malformed JSON, with Node `SyntaxError` diagnostics.
- Apply the bounded TAR reader and its closed numeric, PAX, and end-framing rules to Mac inspection.

The fourth group includes GNU sparse entries and metadata, unknown PAX keys, unsupported header layouts or path encodings, and invalid UTF8 text.
Header numbers must fit safe integers and use the supported octal or base-256 forms. PAX lengths, sizes, and terminators retain their closed grammar.
These physical limits match the release archive reader:

| Resource | Limit |
| --- | --- |
| Physical expanded TAR | 8 GiB |
| Headers | 250,000 |
| One extension metadata body | 1 MiB |
| Cumulative metadata, path, and link bytes | 64 MiB |
| Trailing zero padding | 1–20 KiB, in whole 512-byte blocks |

The Mac limits stay at 2 GiB compressed, 4 GiB logical member bytes, and 16 KiB for the manifest.
Inspection still permits the source's unrelated duplicate names, link targets, and special modes. Extraction-only graph and mode restrictions do not apply.
Archive refusals return `UpdateConflict` with `The Mac runtime archive is invalid.`

The unchanged real Mac producer must pass the hosted gate in `.github/workflows/typescript-lifecycle-native.yml`.
Every final head repeats this gate after source changes. Evidence stays under `mac-producer-compatibility` in the native ABI artifact.
If that producer fails any accepted policy, stop and ask the Manager before further work.

## Mac Unix CLI trust and transport failures

The client refuses linked run/socket entries and sockets owned by another user before capability emission.
The ordinary CLI socket read/write/retry seam returns exit 2 for transport failures.

## Ticket ownership temporary files

TypeScript treats `.panels-workspace.json.<32 lowercase hex characters>.tmp` as a private
Ticket ownership marker in public reads, links, and listings, including aliases. The temporary
file contains the same private ownership data as the final marker, so the Ticket workspace port hides it too.

## Workspace Stop publication retry

The Manager accepted this rule on November 15, 2026. After a successful native
Stop and failed durable publication, TypeScript retains the successful Stop and retries
only publication through `MacRunner.stop_and_publish`. It does not call native Stop again.

## Native first-home installation

The standalone first-home installer accepts only sealed Node version-2 home releases.
A new Mac home writes an explicit version-2 identity with canonical external `installer_node`.
Both hosts use the selected canonical host Node directory before provider directories. Darwin puts that directory before Homebrew, and Homebrew stays ahead of discovered provider directories.

Cleanup uses retained directory descriptors and exclusive creation, rather than recursive deletion through a replaceable pathname.
Changed root or service file identities retain installation files. A timed-out cleanup observer retains descriptors until actual native work settles.
This deliberate uncertainty can retain extra files. It never broadens service control or deletion authority.
After authenticated Darwin bootout, first-install cleanup waits for job removal with the shared updater limits: one 120-second budget and 500-millisecond polls.
Changed ownership, unreadable jobs, or deadline expiry still refuse cleanup.
Every Darwin update takes a verified backup before launch. Native job and descendant-birth evidence must prove retirement before replacement.
An accepted healthy release remains installed when an auxiliary job fails. The terminal record includes that failure.

## Ticket Worker launch folder

At the owner's request, TypeScript starts managed Ticket Workers in the Ticket folder
on the home and Mac executor, not in its `worktree/` checkout.
Workspace preparation, ownership, and checkout location stay the same. The Worker
prompt names the checkout separately from the launch folder.

## Claude SDK single read and live effort

TypeScript gives the Claude child's stdout straight to the SDK. Panels does not read each line first.
The SDK skips a malformed JSON stdout line. Stderr keeps its line bound.
The SDK reads stdout with Node `readline` and sets no line limit. Memory for a very long line depends only on that reader.
Panels drains the SDK messages into its own queue as they arrive. Steer admission and settlement read that queue's receipts, so a slow save never holds them.
Stop reads the receipt that the SDK returns for the interrupt. The SDK drops non-string members from that receipt. A receipt without both lists refuses.
A permission ask waits until Panels has taken in the assistant message holding its own tool use.
The wait has a five-second bound and ends on abort, cleanup, stream close, and a turn result.
An effort change to a named level on a live session applies with `applyFlagSettings`, without a restart.
Clearing effort restarts the child, because a live reset returns to the model default rather than the user's settings.
A model change restarts the child.

## Messages are not held for automatic compaction

At the owner's request, TypeScript delivers a send-now or queued message at once
50 to 60 minutes after the last agent activity. The prompt cache is still warm then.
Only the idle sweep compacts. It compacts an idle conversation
with no held messages in that window. A message that arrives while that compaction
runs waits for it. The focused maintenance test
in `server/tests/conversation_core_maintenance.test.ts` proves this behavior.

## Remembered CLI version verdicts

TypeScript does not run `claude --version` or `codex --version` before every Claude or
Codex start. The server keeps each definite feature-floor verdict
for the exact executable file: backend, resolved path, device, inode, size, and modification
and change times. The backend snapshot's version read fills it, and a miss runs one check and keeps its
result. A failed or unreadable check is not kept. A conversation turn checks before its child
launch record is written, so a too-old CLI is refused without launch custody.
The connection still enforces each floor. Claude's initialize reply and Codex's
`initialize` userAgent report the running version. A version below the floor fails that
start with the same update message and replaces the kept verdict. A missing or unreadable
reported version does not fail the start.

## Finished agent replies are recorded

At the owner's request, TypeScript records each finished agent reply as an
`agent_message` event in its turn. This covers home children and Mac children. The Mac sends
`agent_message_completed` as a durable envelope.
A reply shows in the full conversation only. It raises no unread mark, notification
or Manager wake. The focused test
in `server/tests/conversation_core_delivery.test.ts` proves the record, the order and
the unread mark.

## Permanent save errors give up

TypeScript retries transient conversation save errors until they succeed, with backoff to 30 s:
contention, I/O, a full disk, filesystem errors, and a statement that expired after a schema change (`SQLITE_SCHEMA`).
A permanent error, such as a constraint failure, schema drift (`SQLITE_ERROR`), or a
JavaScript error inside the write, gives up after three retries.
Its conversation stops its child, ends the running turn as failed, and refuses new work
until a restart. A prompt or steer receipt that gives up reports and records the delivery
as uncertain. `SQLITE_CORRUPT` and `SQLITE_NOTADB` give up at once and refuse every later
conversation save and admission in the process. The `vps_save_failed` stall kind
alarms at the next check. The failed state lives in memory only.
