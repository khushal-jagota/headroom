# Accepted differences from Python

This file records intentional differences that the migration Manager accepted. No new difference enters this file without that acceptance. Close an entry when Python adopts the same behavior.

## Native TypeScript presentation

The owner accepts native diagnostics, CLI human text and help layout, and HTTP JSON number spelling.
HTTP shapes, names, types, and statuses remain unchanged. CLI commands, options, JSON fields and types, and exit codes remain unchanged.
Executor wire behavior and exact integers required by the wire or database remain protected.
Comparisons check fields, values, and exit codes instead of Python presentation bytes.

## Staging merge 12: decision notes and recap freshness

The Manager accepts that decision routes admit revision `1.0` as `1`, because Fastify’s native JSON parser does not retain numeric token kinds.

## Unidentified callers from the TypeScript service

The migration Manager accepted the exact cgroup component `panels-typescript.service`
as an additional agent process boundary. The opt-in unidentified-caller rule retains
`panels-live.service` and `panels-workers.slice` and also refuses holders under that
new component. Similar service names do not match. Packaging and the predicate share
`lifecycle/service_identity.ts`. Packaging must import that same constant when its TS unit assets land.
The installed unit name remains an owner cutover decision.

The single direct source comparison records the old Python answer for the new name
separately from this accepted extension. If rollback keeps the new unit name, Python
must recognize that same name through the owner/Chief staging-change route before cutover.

The HTTP gate is wired after trusted-owner normalization. This base has no generic
application WebSocket transport. The pure close-1008 decision is source-compared;
Unit 22 must wire the shared admission boundary after trusted ingress when its raw
transport lands. Existing preview upgrades already require trusted-owner ingress.

## Unknown managed root in a verified backup

The migration Manager accepted this difference during Unit 21 Part 3 source review.
Python indexes its fixed destination-root map with each recorded root name. An unknown name raises an uncaught `KeyError`.
TypeScript reports an operational error instead. The plain environment restore handler returns exit code 1 and an error on stderr.
It does not reproduce the Python fault. Snapshot verification and the database/managed-root restore order retain their existing behavior.

The existing `test_unit21_restore_refusals_and_failed_swaps_preserve_owned_files` comparison pins this accepted refusal.

## Backend discovery in test mode

The migration Manager accepted this safer rule during Unit 19's backend review.
With `PLAN_TEST_MODE=1`, Python's default backend service still probes the machine.
TypeScript composes an empty recording environment and no production providers.
Without explicit recording fixtures, each backend card therefore reports not installed.
Production subprocess and HTTP doors also refuse test contexts before external I/O.
The focused backend composition check proves this refusal. Scripted comparison hosts
inject their declared catalogs and recording actions explicitly.

## Provider allowance scopes with an empty model display name

The migration Manager accepted this difference during review of Unit 19's backend package.
Python's `backend_state._catalogue_model_id` indexes the first word of each display name
after an exact match fails. If a display name is empty or absent, that lookup raises
`IndexError`. The explicit allowance refresh then returns HTTP 500 before it can replace
the cached observation.

TypeScript treats an empty display name as no display-family match. It can still resolve
a unique model-ID family. It drops an unresolved or ambiguous scoped window and retains
the account-wide windows, then commits a successful observation through the usual short
transaction. It does not reproduce Python's exception. This difference concerns only the
empty-display fallback. Nonempty model names and ordinary ambiguity retain the Python rules.

## Closed: accepted held prompts survive a secondary Ticket-model error

The migration Manager accepted this difference during review of PR 712 on November 12, 2026.
Python commit `27e893ad8` now supplies the same correction in the migration comparison baseline.

Both cores commit the accepted receipt, settle the running phase and held sender identities,
emit the change signal, then invoke the secondary Ticket-model writer. The reservation resolves
in `finally`, including when that writer raises. The same secondary error propagates without
another provider write or committed append. This is no longer an intentional difference.

`server/tests/conversation_core_delivery.test.ts` pins this order in
`publication failure after receipt commit does not append or deliver twice`. It also proves
that a repeated sender identity returns its receipt and later backend news still persists.

## Projection rebuild on the writer connection

Projection rebuild uses TEMP tables on the single writer connection in batches of 500 events. Python uses a second connection. Results match. The Manager accepted 373.7 seconds in TS versus 94.5 seconds in Python for 200,000 events (about 3.96 times as long). The follow-up must precede the first projection-version change after cutover ([measured profile](lifecycle/REBUILD_PROFILE.md)). Sustained-load latency is not measured.

## Fixed measure and Goal depth budgets

The migration Manager accepted this policy on November 12, 2026, for Unit 12.

TypeScript uses fixed named depth limits and weights in `server/measures/configuration.ts`:

- Evaluation budget: 991. Each reference costs four units. Each expression edge costs one. Goal actuals start with two units.
- Formula parser budget: 990. Reference leaves add zero, date checks add one, and number/period checks add two. Graph traversal contributes its depth.
- Graph and demand limits: 991.

These values were calibrated on Python's HTTP call path. They are TypeScript policy, not a model of an interpreter stack. TypeScript reads no interpreter setting and retains these limits when Python's boundary moves.

The investigation used Python 3.13.5 with recursion limit 1000. The live server runs Python 3.12. Comparison CI also uses Python 3.12, on both Node 22 and Node 24. Python has no fixed depth contract. Its boundary depends on the interpreter, recursion limit, and call path. In the measured plain-reference case, the shallow helper accepts 248 edges. HTTP reads accept 247 and limit at 248. Twenty extra helper wrappers reduce the shallow boundary to 243.

The gating proof compares Python and TypeScript well inside the budgets, for plain and mixed expressions and independent Goal roots. At the boundary and its neighbors, it asserts the TypeScript outcome only. It records Python's answer as non-gating information. A Python stack change does not invalidate the accepted TypeScript boundary. The direct proof regenerates the Python observations on every run.

The first applicable TypeScript budget determines the boundary error. At total graph depth 992, the measured Python path reports graph nesting while TypeScript reports expression nesting. Both return validation status 400 without a new row. This error-choice difference is recorded with the informational Python boundary observations.

Python also crashes during response serialization for a sufficiently deep accepted formula. In the measured JsonDict response, AST depth 253 succeeds. Depth 254 commits the definition, then returns HTTP 500. TypeScript preserves the committed row and returns the same status, content type, and text through a deliberate, handled response-depth error. It does not reproduce an unhandled Pydantic exception. Request-validation errors retain their separate response path.

See [the measure README](measures/README.md) for the fixed policy and proof commands. Unit 12 Part 2 supplies the merged Goal HTTP routes and populated Manager/Chief page checks. The independent-root seam proof remains separate from those route checks.

## Managed Linux supervisor stop

The Manager accepted the following stop policy on November 12, 2026.
The acceptance closes the design condition, not the implementation or hosted proofs.

Python on managed Linux checks the kernel signal sender, the unchanged launch parent, and the verified systemd manager identity.
TS will instead require a prior authenticated stop intent for the current supervisor incarnation, then the exact supervisor signal.
A bare signal fails closed. Foreground launches keep ordinary signals, as Python does.

The reason is the accidental-stop threat and simpler packaging for Electron and a Mac-only server.
The intent guard refuses accidental bare signals without a compiled wrapper, platform binary, signing step, or extra relocation target.
Prior intent replaces signal-sender identity. It does not authenticate the signal sender itself.
Neither design protects a broad kill of the application child or same-user SIGKILL.

TypeScript keeps the configured control socket address. It requires an owned real parent directory at mode 0700 and creates the socket at mode 0600 without a wider-permission window. Python's default directory is private, but an explicit socket parent and socket mode follow existing permissions and the process umask. The Manager accepted this stricter socket permission policy during the supervisor source review. Restart remains a same-user operation without capability authentication.

APP uses a 30-second shared grace, a 45-second hard trigger, and an owned-force attempt bounded by original stop plus 55 seconds.
Packaged policy uses a separate supervisor APP fallback at 60 seconds, stop client at 80 seconds, and outer unit phase at 90 seconds.
Supervisor/client changes remain in the unmerged release draft. The actual unit renderer and packaged hosted proofs remain open.
An APP timer cannot preempt blocked JavaScript or native code. Independent Worker services are outside the APP unit cgroup.
External supervisor fallback alone cannot prove those services gone if APP blocks before its owned-unit force attempt.
No existing unit, Mac executor, or LaunchAgent changes form part of this decision.
A managed Mac-only server requires a later decision.

Required proof, not yet completed:

- Isolated processes: bare-signal refusal, valid intent, wrong capability, expiry, old-incarnation replay, foreground stop, and child-plus-supervisor signal behavior.
- Hosted runner: actual new-unit `ExecStop`, installer stop, failed control socket through stop timeout, and startup failure before activation.
- Hosted runner: service restart with a new incarnation/capability, and stop during a concurrent application restart through the existing socket.
- Capability: file mode 0600, parent mode 0700, absence from application and every agent environment, and exclusion from all backup roots.
- Packaged supervisor: clean Node-only startup and relocation without a compiler or Python.

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
It preserves Python's canonical locale/presence checks for domain callers without that composition.
Persisted compatible Mac presence is not authority to launch a VPS-local adapter for a Mac Ticket.
Unit 22 replaces the single composed selection boundary when its actual executor router is bound.
This policy does not claim that a composed release has an executor ready.

## VPS metrics integer configuration

The migration Manager accepted this admission boundary for Unit 21 Part 2.
It applies to these ten integer environment overrides:

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

Python parses these overrides with `int()` and can preserve values beyond JavaScript's exact integer range, subject to existing configuration checks.
For example, Python accepts and retains `9007199254740993` for `history_points`.
TypeScript refuses values outside ±9,007,199,254,740,991 with a native validation error that names the field and its exact integer limit.
It does not silently round the example to `9007199254740992`.

The boundary exceeds realistic worker/page counts and memory/database budgets.
Explicit refusal keeps operational limits exact without spreading BigInt configuration through number-based runtime APIs.
Existing positive-value and consistency checks still apply.

The existing [configuration proof](../tests/compare/direct/test_unit21_metrics_config.py),
`test_unit21_metrics_configuration_and_sidecar_paths`, asserts the TS refusal as a gating result.
It records Python's different answer as informational evidence. The ten keys share the same admission predicate.
Unit 21 Part 2, including the metrics source, is merged into the migration branch.

## JavaScript source execution limits

The migration Manager accepted these limits for the new JavaScript script contract on November 12, 2026:

- 8 MiB combined control, stdout, and stderr output per run.
- 64 MiB aggregate decoded HTTP response bodies per run.
- 256 MiB V8 old-space through the child Node process option. This is not a total resident-memory guarantee.

Python enforces only the wall-time limit. Its configured default is 30 seconds (`src/planner/core/config.py` and `measure_sources/configuration.py`), equal to the TypeScript default. The configured wall-time value remains authoritative.

`measure_sources/runtime/runtime.test.ts` proves each limit with actual children:

- `stdout and private control frames share the output byte budget` proves combined output enforcement, including buffered output before completion.
- `decoded HTTP bytes accumulate across responses and exhaust the run budget` proves aggregate response enforcement even if a script catches the failure.
- `V8 old-space exhaustion is a safe budget failure and the child is reaped` proves heap exhaustion with a smaller test cap.
- `good child sees frozen data and the exact environment allowlist; parent retains numeric candidates` checks the actual default child option `--max-old-space-size=256`.

The byte/heap failure cases use smaller configured limits to avoid unnecessary VPS load. Default constants retain the accepted values above.

### Accepted script-contract differences after source review

The Manager accepted these differences on November 12, 2026:

- Python inherits the full server environment. TypeScript constructs a filtered environment with private `HOME`/`TMPDIR` and fixed `LANG`. It retains only `TZ`, `PATH`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, their lower-case forms, `SSL_CERT_FILE`, `SSL_CERT_DIR`, and `NODE_EXTRA_CA_CERTS`. No `PLAN_*` variable or provider home passes through. The environment test pins this exact list.
- Non-wall limit notices say "The run exceeded an execution limit." Python's existing wall-time notice stays unchanged.
- JavaScript diagnostics retain only `SyntaxError`, `TypeError`, `ReferenceError`, and `RangeError`. Python retains fourteen exception names.
- A script's explicit `process.exit(0)` is a runtime failure because it omits the completion protocol. Python treated `SystemExit(0)` as success.

The child runs as the same operating-system user and can read what the server can read, as in Python. Environment filtering is not a sandbox. The author prohibitions are policy, not filesystem or process isolation.

## Claude SDK identity and argument serialization

The migration Manager accepted these two differences on November 12, 2026, for Unit 18:

| Variable | Python SDK | TypeScript SDK |
| --- | --- | --- |
| `CLAUDE_CODE_ENTRYPOINT` | `sdk-py` | `sdk-ts` |
| `CLAUDE_AGENT_SDK_VERSION` | `0.2.152` | `0.3.316` |

The child identifies the SDK that actually runs it. TypeScript does not claim to be the Python SDK.
These are the only accepted launch-environment differences. All other variables compare exactly;
there is no blanket SDK-variable normalization. The public spawn hook preserves the Python
inherited-environment policy, including removal of `CLAUDECODE`, rather than forwarding the TS
SDK's additional flags or filtered environment.

The Manager also accepted these SDK argument facts on November 12, 2026:

- TS adds `--allow-dangerously-skip-permissions`, required for its bypass-permissions mode.
- Python separates the values of `--model`, `--effort`, `--permission-prompt-tool`, and
  `--permission-mode`. TS uses the equals form for those four options.
- The SDKs use different fixed option orders. The paired check asserts each complete
  argument template, with only the correlated session UUID substituted. Both use `--resume=X`.
- TS prefixes a script executable with `node`. Python executes its shebang path directly.

Raw argument arrays remain in the evidence. No generic option parser, sorting, wildcard,
or unknown argument is accepted by the comparison.

`conversation/backends/claude_sdk_environment.test.ts` pins the two TS values through the real
pinned SDK and the catalog's public spawn hook. It compares the entire child environment,
including inherited values and the removal of `CLAUDECODE`. Every process API is blocked before
SDK import, and the synthetic launch stops at the hook without a process. The same check
asserts that import and query construction leave the server environment unchanged.

Source inspection found no Panels storage, view, usage, catalog, or session-path consumer of
either variable. Panels reads usage and cost from result fields, models from the handshake,
and the session cursor from `session_id`. Provider-private session-file effects remain unverified;
no provider session file or owner executable was inspected for this check. The adapter follows
this accepted policy.

## Credential request UUID text

Python's `UUID()` accepts additional integer-text forms, including a `0x` prefix,
a leading plus sign, and underscores when the resulting text has the required
length. Credential requests in TS accept canonical
hexadecimal UUID text and the ordinary UUID wrappers, but refuse those lenient
integer-text forms. The Manager accepts this narrower rule as Python leniency
that TS does not reproduce. The focused credential-request check pins refusal of
one `0x`-prefixed form. Canonical UUID identities and all normal request outcomes
still match Python.

## Codex process cleanup and blocked writes

The migration Manager accepted these three differences in the Part 1 review on
November 12, 2026:

- TypeScript cleans up the owned process group. Python's uncontained client stops
  only its direct child. The WorkerUnit path retains its existing containment.
- A write failure immediately fails all pending requests and suspended writes in
  TypeScript. Python marks the wire broken, while some pending requests wait for
  a later child exit or their own deadline.
- A TypeScript request deadline includes time in the write queue and a blocked
  stdin write. Python starts its request deadline after the write completes.

These differences do not authorize one request's cancellation to stop the whole
app-server, or a timeout to reject unrelated pending requests. Those operations
retain Python's scope. The process implementation never signals a group after
it observes the direct child's exit.

## Codex shell startup path

The migration Manager required the TypeScript-owned startup script in the Part 1 review
on November 12, 2026. On Linux, Python sets `BASH_ENV` to
`src/planner/conversation/tool_shell_startup.bash`. TypeScript sets it to
`server/conversation/tool_shell_startup.bash` through the shared TypeScript helper.
The paired startup check asserts each exact path and identical script bytes. Every other
child environment value and launch argument compares exactly.

## Recorded usage test composition and shutdown

The migration Manager accepted these usage differences in the Part 5 review on
November 12, 2026:

- Default TypeScript test composition selects synthetic provider homes beside the
  test database and supplies a rates fetcher that returns no rates. Python's
  default composition selects ambient provider homes and can fetch LiteLLM rates.
  Explicit source directories and recording fetchers remain injectable. Tests do
  not read owner transcripts or contact the public rates source.
- TypeScript summary, rate-refresh, backend, and voice requests receive HTTP 503
  after the shared conversation write admission seals during shutdown. This retains
  ownership of previously admitted work through its drain. The Manager extended
  this seal to backend and voice requests in the whole-unit review. Python has no
  equivalent shared write-admission gate on these routes.

## Recorded usage timezone data and nonstandard JSON

The migration Manager accepted these remaining Part 5 differences on November 12,
2026:

- Day buckets use Node's bundled ICU timezone rules. Python uses the installed
  ZoneInfo data, with its package fallback. A temporary rule difference between
  these data releases can place an instant in a different day bucket. Validation
  still requires a Python-valid key. The Manager also accepted UTC fallback for
  an installed custom or legacy key that ICU cannot name, which differs from
  Python's installed TZif offset and can change its buckets. Invalid or
  uninstalled HTTP keys still receive 422.
- Transcript JSON in TypeScript rejects the nonstandard numeric tokens `NaN`,
  `Infinity`, and `-Infinity`. Python's `json.loads` accepts these tokens. The
  TypeScript parser rejects the whole line even when such a token occurs in an
  otherwise unused property. This narrower intake does not change ordinary JSON
  numeric signatures or finite token arithmetic.
## Codex ask withdrawal at shutdown

The migration Manager accepted this difference in the Part 2 review on November 12, 2026.
TypeScript bounds ask withdrawal before shutdown to two seconds. When that bound expires,
client stop breaks the wire and stops and joins the owned child. Python waits for the
withdrawal without this bound. The bound prevents a parked ask or blocked write from
holding process cleanup open.

## Source credentials across redirect origins

The migration Manager accepted this correction for the cross-area repair.
Python follows source redirects and retains custom credential headers across origins.
TypeScript does not reproduce this Python weakness. A credential reference, or any caller-supplied header except `Accept` and `Content-Type`,
restricts redirects to the same origin (scheme, host, and port). Header names are case-insensitive.
It refuses before a cross-origin hop with a transport failure that names the refused origin,
without the credential value. Requests without credential headers retain Python's redirect limit.
At these three call sites, TypeScript limits the wait for headers and each body read without a total deadline.
It does not time connect, write, and pool phases separately as HTTPX does.
Node fetch sets Host from the URL and ignores a caller-supplied Host, which Python HTTPX can retain.

`tests/compare/direct/test_cross_area_http.py` pins same-origin delivery, cross-origin refusal,
and Python's contrasting header behavior with synthetic loopback servers.

## Conversation history locale data

The migration Manager accepted Node's bundled locale data for history labels in
place of Python's system ICU on November 12, 2026. TypeScript uses
`Intl.DateTimeFormat` with medium dates and short times and loads no native ICU
library. Locale-data releases can differ in punctuation, spacing, or date patterns.
The recorded Python en-US labels use U+202F before AM/PM. Intl can use an ordinary
space there. For example, Python returns `Sep 22, 2026, 12:00 AM` while
Intl returns `Sep 22, 2026, 12:00 AM`. The direct comparison names this exact
separator difference and
retains both raw labels and code points. It does not normalize dates, times,
conversation IDs, ranks, revisions, or arbitrary label text. Prefix search remains
case-folded against the actual label that each runtime displays, so a prefix that
includes different locale-data text can select a different entry. Unknown locale
and zone inputs retain the documented fallback behavior.

## Codex sign-in admission after shutdown

The migration Manager required this safety difference in the Part 3 source review. TypeScript closes sign-in admission when shutdown begins. Later or queued starts fail with `CodexSignInUnavailable`. Python permits a later start on the same service instance.

The production provider set also closes catalog and usage admission, aborts its owned probes, and joins them before shutdown completes. Snapshot environment cleanup and sign-in cleanup both run even if either fails. An aggregate error retains their failures. These ownership requirements come from the Ticket Brief and Manager review.

## Hermes ACP ordering and bounded retirement

The Manager accepted strict read-order handling for Hermes in the Hermes Brief and Part2 review. Python's ACP dispatcher starts separate callback tasks and handles replies directly. TS finishes each notification, request admission, and asynchronous reply effect before it admits the next frame. This includes managed-image persistence. Normal sink callbacks have no deadline, so a slow database does not break a healthy wire.

The Manager also accepted a 15-second drain bound after the direct child exits while a descendant retains stdout. Python waits without that bound. TS fails the remaining wire operations after the bound. The reused process owner never signals a group after its leader was reaped.

The Manager accepted the following control-deadline rule on November 12, 2026: steer and configuration RPCs each have a 15-second reply deadline. A hung earlier persistence callback can therefore delay those callers only until their own deadline. The official SDK then sends `$/cancel_request` and stops waiting for that RPC only. Steer returns uncertain. Configuration raises `NeedsRebind`, because the applied value is unknown, and the core retires that child before another prompt. During startup, cleanup precedes `SessionLoadFailed`. Other pending requests and the wire stay intact. Python awaits these control replies without this deadline. Regular turns and image persistence gain no deadline, and TS discards no valid content to release the queue.

Whole-update validation uses the official SDK JSON schema before its best-effort deserializer. Python coerces some out-of-schema numeric strings, including usage occupancy and stated cost. TS rejects those updates whole instead of coercing them. Fresh parser observations show that the original SDK already rejects string occupancy or drops string cost. The Manager accepted this strict-wire policy on November 12, 2026. Real Hermes serializes numeric model fields as numbers. The side-by-side gate must confirm that actual usage and cost events arrive and are recorded. Fresh parser evidence stays with the Hermes ticket.

Notifications during a prompt write follow Python's existing current-turn/no-turn guard. A successful actual prompt write remains accepted if stop races its continuation. Stderr retains whole lines with Python's final character cap. Fresh direct traces compare initialization exactly: the Python serializer uses `exclude_defaults=True`, so its empty default `clientCapabilities` does not appear on the wire. These are parity fixes, not additional deviations.

The raw backend callback diagnostics compare exact payloads, turn attribution, counts, and order within each event type. Their cross-type order is not a parity claim. Canonical core events, lifecycle/view rows, live frames, and ACP requests keep exact ordered comparison.

## Separate Mac executor state

The Manager required these safety differences during the Mac slice 1 review on November 12, 2026.
TypeScript defaults to `~/Library/Application Support/Panels/executor-typescript`.
Python defaults to the sibling `executor` directory.
TypeScript refuses a resolved root equal to or inside that Python root, including aliases through symlinks.
It also refuses an empty or relative `HOME`, before it opens storage.
The installation keeps its own app, supervisor, and explicit state root.

TypeScript opens artifact sources with `O_NONBLOCK`, then requires a regular file through `fstat`.
A FIFO therefore refuses instead of blocking the event loop before that check, as Python can do.
Large copy, hash, and strict flush operations run in an owned worker with the shared shutdown deadline.
The file formats, hashes, and successful publication behavior retain the Python contract.

Mac workspace resolution refuses traversal through a non-directory with Python 3.13's ENOTDIR text; Python 3.12 accepts a file before `..`. The Manager approved this Mac-only difference. Shared resolver defaults remain unchanged.

### Command settlement and update stop proof identity

The Manager requires withdrawal before invocation to preserve the active turn.
Python changes that turn before its withdrawal check. TypeScript changes it only after invocation admission succeeds.

The Manager requires completed journal and output settlement before idle or a successful stop certificate.
Python checks only for derived output that remains unpublished, so a failed journal write for a command without output can escape that check.
TypeScript also blocks on the retained command's unsettled state.

The Manager requires one stop proof for each update request and exact child across retries.
TypeScript retains that proof's command identity until its required output settles.
Python allocates a fresh `update_stop_` identity on each preparation attempt.
The caller supplies the real update request ID. A retry cannot publish a second `child_stopped` entry for the same request and child.

The Manager also requires lossless native projection of `sender_message_count`.
TypeScript raises `ValueError` for counts outside JavaScript's safe integer range before native invocation. Python's integer count has no corresponding bound.

### Exact Mac stop after a failed command join

The migration Manager accepted this difference on November 13, 2026, for the final Mac executor slice.
Python joins only command tasks that remain pending. If one rejects during that join, Python returns an uncertain outcome before native cleanup.
TypeScript joins every selected actual command with `allSettled`, drains source work, attempts native cleanup, and drains final source work.
It still returns uncertain and issues no successful stop certificate for that failed join.
The exact child's joined-command errors and accompanying cleanup errors remain retained across retries, including after the failed tasks leave the active view.
Later stops retry retained native cleanup but cannot turn that known failed join into a success certificate.
A combined diagnostic preserves command and cleanup messages. Each wire error detail keeps the existing Unicode character bound.
Ordinary successful stops keep Python behavior.

### Lossy live notifications

The Manager requires sender failures to drop live notifications and log once, with no live delivery from retired children.
Python forwards its sender's exception and has no retired-child check on this live path.
TypeScript validates the live payload through the shared wire boundary before the best-effort send.

## Mac canonical skill publication ownership and durability

The Manager accepted these Mac-only safety differences during PR 817 source review on November 13, 2026.
The shared server publisher keeps Python behavior, including legacy `skills` symlink conversion and replacement of existing entries.
The Mac uses an explicit owned-link policy in that same shared module. It never recursively removes provider entries.
Before any publication or derived-state write, the Mac requires an absolute existing provider home and an existing real `skills` directory.
The trusted adapter host supplies that home separately from the snapshot. Its real path must equal its configured normalized path.
The Mac refuses linked homes or skills directories, managed-name collisions with files/directories/foreign links, case-only collisions, and link loops.
It preserves unrelated entries and dangling user links. Only links that resolve inside its own executor state root belong to it.
The Mac retains the provider directory and uses descriptor-relative unlink, symlink, and rename operations.
New links first use recognizable private temporary names. Replacement is atomic, after an immediate ownership recheck.
A later publication removes only recognized temporary links that still point into that executor's own root.

The held directory and rechecks prevent a swapped folder from redirecting deletion into a replacement folder.
They do not coordinate same-user programs that write that folder at the same time. Rename is not an atomic ownership comparison.
The root-local installation lock does not serialize another executor installation. The two-executor publication policy remains an owner decision.
The Mac also refuses linked ancestors of its state root, including native `/tmp` and `/var` aliases. Use reviewed canonical native paths.

New generation files receive strict full flushes, and their directories receive flushes before atomic generation publication.
A damaged derived generation is preserved under a private `.invalid-*` name and rebuilt from the authoritative snapshot.
Python instead refuses that existing generation. No source or provider user data is discarded by the rebuild.
The Mac host does not collect unreferenced generations or abandoned stages.
This slice adds no generation sweeper. Native durability, Darwin directory ABI, and filesystem failure behavior remain later Mac checks.

## Mac outbox cleanup after commit

The Manager required this correction during the Mac slice 2 review on November 12, 2026.
Python can reject a successful capture callback after its append commits, when the final blob sweep fails.
A caller without a fixed submission ID can then retry and queue the same output twice as new.
TypeScript logs that cleanup failure and retains the callback result. A later sweep retries blob cleanup.
The durable append and receipt behavior otherwise retain Python's contract.

## CLI workspace artifact field

TypeScript reads workspace artifact `opens` values. Python reads the absent `path` field and fails for populated workspaces, including JSON output.
The Manager accepted this correction. Human output prints `null` for a group without an openable path. JSON retains the complete tree and children.

## CLI nonfinite body-file input

TypeScript rejects nonfinite body-file values before transport, with validation exit 1 and the normal JSON error envelope.
Native JSON parsing rejects `NaN` and `Infinity`. Numeric validation rejects overflow such as `1e999`, including nested values.
Exact large integers remain valid. Python accepts these nonfinite inputs, then fails during HTTPX serialization without its normal error envelope.

## CLI TLS trust and configuration diagnostics

The migration Manager accepted Node's built-in default trust store for the CLI.
The measured Node 22 store contains 145 roots, while pinned Python certifi contains 118.
All 118 certifi roots occur in Node's store, which adds 27 roots in this observation.
The CLI keeps no copied CA bundle. It honors `SSL_CERT_FILE`, `SSL_CERT_DIR`, and Node's startup `NODE_EXTRA_CA_CERTS` handling.
An explicit CA file or directory replaces the default roots. CA-directory lookup verifies lowercase OpenSSL subject-hash filenames and requires the `openssl` executable.
The observed trust sets and raw diagnostic examples are in [the transport evidence](/files/tickets/t_jt78h9xr/artifacts/pr1-transport-review.json).

The Manager accepted native Node TLS failure wording at exit 2 instead of Python's OpenSSL wording and source line.
A missing `SSL_CERT_FILE` produces a clean validation error at exit 1, where Python prints a traceback.
Every invalid server URL syntax is a clean configuration-validation error at exit 1.
Python can instead print a traceback or attempt DNS. In the measured `http://[` bracket case, Python returns DNS connection exit 2 and TypeScript returns validation exit 1.
Both raw bracket-case outcomes remain in the linked transport evidence. The Manager accepted the same rule for all malformed-address forms.
A missing URL scheme retains Python's connection failure at exit 2.
One comparison per failure class retains both raw outcomes.

## CLI invalid UTF-8 body files

The migration Manager accepted a clean validation error at exit 1 for invalid UTF-8 in a body file.
Python's file decoder instead prints an uncaught traceback. Both commands fail before an HTTP request.

## Integer-like JSON map keys

The Manager accepts native JavaScript order for cosmetic JSON maps. Integer-like keys use numeric order instead of Python insertion order.
Comparisons check parsed JSON fields, types, values, and CLI exit codes. They do not compare object-member order or Python number spelling.
Arrays remain ordered. Declaration arrays preserve dataset fields and chart dimensions, and Ticket search follows workflow field order.
Persisted dataset row identities and exact numeric values remain unchanged. Special names such as `__proto__` remain ordinary own properties.

## Executor message and core admission

### Native JSON boundary

The Manager replaced Python byte compatibility with native JSON on November 14, 2026.
Both TypeScript executor peers ship together at cutover.
The shared schemas define message meaning and generate the TypeScript types.
The codec uses `JSON.parse`, cached Ajv validators, and `JSON.stringify`.
Integer fields and counters use safe integer numbers. Opaque payloads contain plain JSON values.
Nonfinite values refuse admission.

The Python lexical parser, exact-number markers, scalar coercions, numeric formatting, and special depth and integer-token limits are removed.
Command, answer, mailbox, identity, and version admission rules remain, with the existing error codes.
Private filesystem nanosecond timestamps and inode values retain Node bigint stat values and stay off the wire.

The Manager requires the TypeScript command route to forward response disconnects into mailbox poll cancellation; Python's route has no explicit disconnect observer.

### Encoding validation

The Manager required this admission rule in the shared codec review of PR 806.
TypeScript validates each root-specific encoder input through the wire decoder before serialization.
It rejects missing required fields and invalid command semantics, and it supplies Python's declared defaults.
Python's serializer can write a dataclass whose state did not pass that validation.
The TypeScript sender must produce a message that the Python peer can read.
Root-specific encoder inputs use the generated contracts. Nullable fields without defaults require an explicit null.

### Retained turn numbers

Python accepts unbounded integers. TypeScript refuses remote turn numbers, sender message counts and late-steer sequences outside the safe integer range at their conversation-core boundary. Wire and mailbox integers remain exact.

### Replaced-process retry logging

Python logs every failed retirement or close attempt. The Manager accepted TypeScript logging only the first failure of each retry loop, including its cause. Both retry indefinitely with delays rising from one to sixty seconds; TypeScript shutdown cancels the retry and retains its actual settlement.

### Canonical Mac Worker identity

The Manager accepts refusal before child creation when canonical Mac ownership disagrees with absent or different Worker identity hints.
Python selects a local child in that case. TypeScript refuses so a Mac Ticket never selects a local child.

## Release archive sparse members — accepted

The Manager accepted explicit refusal of old GNU type `S` and every `GNU.sparse.*` PAX record.
Python supports these sparse formats. Neither actual release producer emits them.
The TypeScript reader returns `AppValidationError` before the destination exists.
The existing release comparison records this accepted difference instead of Python equality.

## Release archive metadata, names and writer timestamps — accepted

The Manager accepted these five differences on November 13, 2026:

- Unknown inert PAX keys are refused under the closed metadata-key policy.
- An empty effective PAX file path is a typed refusal. This Python fault is not reproduced.
- Bodies on directory or link members are refused under the closed member policy.
- Release names must be valid UTF-8. A non-UTF-8 name is a typed refusal.
- The TS release writer can lose the fractional part of an mtime because node-tar writes whole seconds in an ordinary header. A fractional time that it writes in a PAX header stays intact. The reader keeps Python's nanoseconds exactly for either archive. This applies to release application archives only.

The four reader refusals use `AppValidationError` before output creation. Python accepts three forms and fails after destination creation for an empty path.
The existing release comparison pins these intentional differences and retains Python's observations without claiming equality.

## Release archive members behind links — accepted

The Manager accepted refusal of any member whose parent is another archive link member.
This includes symbolic links and hard links, even when the link appears later.
Python can extract through safe internal links. Neither actual release producer requires that behavior.
TypeScript validates the complete member graph before it creates the output directory.
The existing release comparison records this accepted difference instead of Python equality.

## TypeScript iOS compatibility inventory — accepted

The Manager accepted the TypeScript source inventory on November 13, 2026.
It replaces Python source paths with production TypeScript, eight fixed JSON inputs, and two retained Hermes Python helpers.
The inventory includes goal guidance, holder-page guidance, and the executor schema now that these JSON files are runtime inputs.
The Manager requires renewed human review before publication at cutover.
The digest retains raw bytes, NUL framing, and Python path ordering. Publication still requires a source-bound human review.

## VPS mailbox shutdown admission

The Manager's VPS executor Brief requires that shutdown seal new work immediately.
The TypeScript mailbox rejects later owned work and returns an empty held poll before another SQL fetch.
Python's mailbox can accept later work and fetches once more before its closed check.
The direct mailbox comparison records one source fetch and zero candidate SQL calls after the seal.
Already admitted commits retain their announcements, and actual settlement retains pending failures and cleanup.
The stateless replacement participant and the single stall source retain Python's post-stop behavior.
Their caller owns the SQL capability, admission and actual settlement.

The Manager's mailbox review also requires timer-range admission.
TypeScript refuses nonfinite waits and waits above Node's maximum timer delay before SQL starts.
Python's event loop can accept longer durations. Finite negative waits retain immediate behavior.

## VPS presence shutdown and reply cursor

The Manager accepts these two presence differences for Unit 22a.
An admitted lease completes its commit, publication, and reply after shutdown seals the service.
Stop suppresses replacement, returned, and update callbacks that did not start before the seal.
It joins callbacks that already started. A skipped replacement stays owed, and a skipped update returns `no_update_check`.
Python does not suppress all callback starts after close.

Every accepted reply reads the canonical SQL mailbox maximum, including after the mailbox seal.
During a command commit before its announcement, this cursor can lead Python's cached mailbox cursor.
Mailbox rows never delete. The leading cursor therefore names existing rows that the next poll returns.
This uses no new flag on the mailbox.

The presence seal rejects new leases, WebSocket refusals, and drain waits.
Cached reads, header fences, contact observations, and memory-only activity observations continue after stop.
A cold read returns the empty cached state without lazy SQL.
Contact observations update only memory after stop and start no timer.

A presence stop before lazy initialization performs no presence SQL read or write.
Python close can initialize its record before its final flush and logs and swallows OS or SQLite failures.
TypeScript final flush failures remain visible through actual settlement, as the lifecycle contract requires.
Normal-running contact flush failures log and remain retryable, as in Python.
They do not become shutdown failures when a later flush succeeds.
The bounded stop observer uses the shared absolute deadline. Actual settlement remains separate.

Presence commits are silent at the shared write door.
After commit succeeds, the service publishes its cache before it emits the one presence change signal.
Synchronous signal subscribers therefore read the committed presence state, with no cache update before a possible rollback.

## Operator composition unavailable leaves — resolved

`environment app-build`, `environment run`, `serve`, and `restart` now use the native
release builder, prepared-instance launch, supervisor, and control protocol.
The temporary unavailable-command refusal is removed. The installed wrapper remains
unchanged until the separate cutover.

## Operator manifest path consistency

The Manager requires one admitted path for backup-current's manifest read and validation.
Python reads the literal Click path, but its validator independently expands a leading `~`.
If the current directory contains a directory named `~`, Python can validate a different file.
TypeScript uses one absolute literal path for both operations and retains the literal error filename.
App-identity retains Python's explicit validator expansion of `~`.

## Server event-loop task inspection — accepted, deferred

The Manager accepted explicit refusal because the TypeScript runtime cannot enumerate Promise await chains.
The typed `server-event-loop-tasks` callback returns exit 1 and empty stdout.
Human stderr is exactly `error: The TypeScript runtime cannot enumerate Promise await chains.\nThis diagnostic is unavailable in the TypeScript server.\n`.
JSON stderr is the existing error envelope with code `unsupported`, the first sentence as its message, empty detail, and a newline.
Await-chain inspection remains deferred. Resource counts do not replace this diagnostic.
The public CLI prints that callback's literal result. Node's built-in diagnostic report is the operator alternative.

Unexpected operator failures, including cleanup AggregateErrors, print the error text at exit 1 instead of Python's traceback, as accepted by the Manager.

## VPS file descriptor custody

The accepted executor plan uses Linux procfs descriptors for the VPS file module.
The module refuses unsupported systems before intake and supplies no path fallback.
Python's install helper rewalks a target pathname during copy and cleanup.
TypeScript retains the original source and destination parents through those operations.
Python hashes an input descriptor, closes it, and later reopens its path for the response.
TypeScript retains the checked descriptor through its bounded input stream instead.
The later transport slice must consume that stream and prove its disconnect ownership.
The input owner also anchors its byte root independently of the existing managed-file lookup.
A root swap between that lookup's symlink check and resolution cannot redirect input bytes.
Its standard message root follows the logical DB-path parent, even when the DB filename is a symlink.

Regular-file admission adds NONBLOCK before fstat, so a substituted FIFO cannot hang its open.
Python's open can block on that FIFO. The same source hazard appears in the Mac file evidence.
Cleanup failures carry explicit provenance and retain the original failure with the cleanup errors.
Python can replace the original exception when cleanup also fails.

The upload timeout observer can return retry_later while a JS iterator read remains pending.
Python's wait_for cancels and awaits that read before it returns its timeout.
TypeScript requires a cancellable upload source and destroys it before iterator return.
It closes and removes the incoming file before it joins the source's actual cleanup.
The routes slice must pass its owned request Readable with that destruction capability.
Stop seals intake and observes actual work. The supplied media signal supplies the shared grace cutoff.
The input lookup result must remain inside an independently anchored conversation root.
The resolved target then passes through the same NOFOLLOW directory walk and regular-file open.
Contained same-conversation leaf links keep Python behavior. Later substitutions refuse as not found.
NUL artifact paths retain Python's exact `lstat: embedded null character in path` refusal.
Ancestor symlink loops produce the clean `artifact path contains symlink` validation refusal, without raw Node errors.
Python raises a raw `RuntimeError` for the same ancestor loop. The Manager accepts this clean TypeScript refusal.
The Manager reviews these implementation differences before this slice merges.

## Catalog cancellation withdraws waiting commands

The Manager requires that TypeScript withdraw a waiting model-list command after an internal read cancellation, reconnection, or stop. Python cancellation leaves that row waiting. A caller that cancels its own observation does not cancel or withdraw the shared read. TypeScript uses its own short cleanup signal, capped by the original shared shutdown deadline, and joins withdrawal before catalog settlement. The invocation keeps the mailbox open until that cleanup settles.

## Mac archive inspection policies and JSON diagnostics — accepted conditionally

The Manager accepted these four groups on November 13, 2026:

- Refuse global PAX size overrides and NUL text in names or PAX values.
- Refuse UTF16/32 and non-scalar UTF8 manifest encodings.
- Preserve malformed JSON refusal, with Node `SyntaxError` diagnostics instead of Python `JSONDecodeError` diagnostics.
- Apply the bounded TAR reader and its closed numeric, PAX, and end-framing rules to Mac inspection.

The fourth group includes GNU sparse entries and metadata, unknown PAX keys, unsupported header layouts or path encodings, and invalid UTF8 text.
Header numbers must fit safe integers and use the supported octal or base-256 forms. PAX lengths, sizes, and terminators retain their closed grammar.
These physical limits match the accepted application reader:

| Resource | Limit |
| --- | --- |
| Physical expanded TAR | 8 GiB |
| Headers | 250,000 |
| One extension metadata body | 1 MiB |
| Cumulative metadata, path, and link bytes | 64 MiB |
| Trailing zero padding | 1–20 KiB, in whole 512-byte blocks |

The existing Mac limits stay at 2 GiB compressed, 4 GiB logical member bytes, and 16 KiB for the manifest.
Inspection still permits the source's unrelated duplicate names, link targets, and special modes. Extraction-only graph and mode restrictions do not apply.

The direct case measures global PAX size, a NUL PAX comment, a UTF16 manifest, one oversized metadata declaration, and malformed JSON diagnostics.
Python accepts the first three archives. The metadata fixture is also truncated, so both implementations refuse it.
The four archive refusals return `UpdateConflict` with `The Mac runtime archive is invalid.`
Other accepted limits and framing rules have source review here, without a measured boundary claim.
Both implementations refuse malformed JSON, with different parser error types and text. This adds no accepted malformed input or claim of equal error bytes.

The direct proof is `tests/compare/direct/test_unit21_mac_updates.py::test_unit21_mac_updates_retained_archive_and_leased_report_sequence`.
Its `unit21-mac-updates.json` report retains both outcomes and marks the accepted observations explicitly.
Acceptance requires that the unchanged real Mac producer passes the hosted gate in `.github/workflows/typescript-lifecycle-native.yml`.
The [first real producer proof](https://github.com/khushal-jagota/headroom/actions/runs/43466927540/job/132028428389) passed on the PR merge tree for head `37333bd6e`.
Its 597,087,278-byte archive passed unchanged, with both direct children closed. Build plus inspection took 276.07 seconds.
Every final head must repeat this gate after source changes. Evidence stays under `mac-producer-compatibility` in the existing native ABI artifact.
If that producer fails any accepted policy, stop and ask the Manager before further work.

## Installer database and attempt paths — accepted

The Manager accepted this correction to Python's copied installer on November 12, 2026.
Python accepts configured paths at update admission but its copied installer hardcodes
`current_root/data/planner.db` and `current_root/data/update-attempt.json`.
TypeScript passes the admitted database to capture and rollback restore. It uses the
admitted data directory for the attempt record and its lock. Neither path derives from
the application root.

`tests/compare/direct/test_unit21_installer.py::test_installer_algorithm_and_configured_paths`
compares four canonical-path scenarios against fresh Python. Its configured-path lifetime
proves actual capture/restore command arguments, record and lock placement, no canonical
data directory, and cancellation only after the owned restore witness closes.
The configured helpers are disclosed Node witnesses, not a real installer rehearsal.
`test_unit21_interruption_capture.py` separately exercises the actual stopped capture and
restore entries. Hosted service, copy, health and rollback proofs remain later gates.

## Mac Unix CLI trust and transport failures — Manager directed

The TypeScript client refuses linked run/socket entries and sockets owned by another user before capability emission; Python follows those links.
The ordinary CLI socket read/write/retry seam returns exit 2 for non-ValueError transport failures that Python can expose as a traceback.

- Mac Node archives: the TypeScript VPS also admits a regular executable `node/bin/node` and regular `server/executor/mac/entrypoint.ts`; Python VPS acceptance of Node-only archives remains unsupported.
