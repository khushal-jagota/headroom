# VPS executor

`vps_transport.ts::VpsExecutor` owns one invocation's mailbox, presence, inbox, file transfers, retained Mac runs, and catalog relay. Its constructor receives the application's database, clock, change signal, and media cutoff. It does not create another database owner. `app_transport.ts::create_executor_routes` supplies the HTTP and obsolete-WebSocket boundary.

Production creates the endpoint only when an executor ID is configured. `LocaleBackendChildFactory` selects remote children for Mac Tickets and preserves local factories for VPS work. A canonical Mac Ticket with absent or conflicting Worker identity hints refuses before child creation. Persisted presence alone cannot enable remote execution. Without an endpoint, the existing local-only admission policy still refuses Mac work.

## Startup and retained state

Before record initialization, `core/start.ts` asks the endpoint for `open_runs()`. The single reconciliation therefore preserves retained turns before the core adopts them. Adoption restores asks, saved answers, provider identities, memory-notice maintenance state, and exact child references. A stale or stopping child remains quarantined until its corresponding retirement proof arrives.

`configure` binds the actual core to the inbox and run owner. `start` loads durable presence/mailbox state and resumes replacement drains, late-steer watches, and never-ran prompt delivery. A fresh child registered before a VPS crash receives its owed prompt on the original turn. Interruption closure and exact claim restoration follow the committed prompt. Production reads the endpoint's health and stall sources, and its `machine_catalog` uses the same catalog relay.

Durable items enter through `MacInbox`. Ordered receipts, replay, assignment authority, canonical writes, answer application, and file publication share the existing write door. A refused domain effect can roll back its savepoint while the outer refusal receipt commits. Runtime predictions are restored before the global publication finalizer, so rolled-back events do not escape through the live tail or Signal.

Live batches carry only message deltas, thinking notices, and tool progress. They use the current exact child's event sink and create no durable item receipt. Completed backend prose uses the item route for journal and receipt custody. The core does not turn it into an addressed notebook message. Lifecycle changes and other durable events also use that route. Live reads enter the existing application routes under the child's assigned principal; they do not grant arbitrary owner access.

## Routes and authority

All thirteen executor endpoints require trusted Tailscale login, the configured executor ID, no browser Origin, and no duplicate security headers. There is no executor bearer token. Work routes also require the supported protocol and current incarnation. Incarnation fences rows; it is not a credential. Writers repeat the relevant current-incarnation and assignment checks inside their transaction. Updates remain reachable before work-version admission.

| Method | Path | Boundary |
| --- | --- | --- |
| POST | `/api/executor/lease` | Admit identity and versions, update presence, settle replacement custody |
| GET | `/api/executor/commands` | Current executor's bounded mailbox poll |
| POST | `/api/executor/commands/outcomes` | Current executor's typed command outcomes; stop success still needs exact proof |
| POST | `/api/executor/conversations/:conversation_id/items` | Ordered durable intake and item receipts |
| PUT | `/api/executor/uploads/:sha256` | Authorized child's digest-named raw upload into private staging |
| GET | `/api/executor/commands/:command_id/files/:stored_file_id` | Authorized input of a waiting or delivered command |
| POST | `/api/executor/reads` | Allowlisted application read as the assigned principal |
| POST | `/api/executor/conversations/:conversation_id/live` | Transient events for the exact current child |
| GET | `/api/executor/update` | Real update service's current check |
| GET | `/api/executor/update/:request_id/archive` | Real update service's selected archive |
| GET | `/api/executor/update/prefetch/:archive_sha256` | Real update service's digest-selected prefetch |
| POST | `/api/executor/update` | Strict typed update report with current health |
| WebSocket | `/api/executor/connect` | Refuse the obsolete transport before upgrade |

`ComposedEnvironmentRuntime.mac_updates` exposes the environment's one Mac update service. Startup binds that same instance to the executor, presence checks, and update routes. Update reports pass strict generated decoding before the current health snapshot and synchronous service call. The default production backend/environment dependency table remains a supplier-owned gate.

## Stop and recovery ownership

An ordinary owner kill waits without a deadline for exact Mac stop proof, as Python does; only that conversation waits. A successful stop result alone does not prove termination. The matching accepted `child_stopped` item or proven process replacement closes exact custody. Rebind waits for that retirement outside the conversation lock before a fresh child takes the same reserved prompt. A safe initial-delivery withdrawal can finish the kill before remote retirement completes.

The invocation registers the endpoint through the existing producer lifecycle. Every stop observer receives the original absolute monotonic shutdown deadline. `settled()` retains raw completion after an observer expires. VPS shutdown detaches an active retained Mac child without starting a stop command; an already-running stop remains owned. Failed actual stop attempts remain retryable.

Catalog cancellation withdraws its waiting command and completes that cleanup before the mailbox seals. A caller's cancelled observation does not cancel the shared catalog read. Endpoint shutdown seals new work and joins admitted saves, file cleanup, catalog withdrawal, and run/presence work before database closure. During shutdown, the catalog's short cleanup signal is capped by the original deadline.

## Shared wire

Both executor halves import their wire types from `wire.generated.ts`. Python remains the contract author until cutover. Protocol 22 uses HTTPS JSON. The spool format is 5 and the command journal format is 1.

From the repository root, in an isolated development environment:

```sh
scripts/dev mypy tests/compare/direct/export_unit22_wire.py
scripts/dev check-node server
.venv/bin/python -I -B tests/compare/direct/export_unit22_wire.py | node server/executor/tools/generate_wire.ts
.venv/bin/python -I -B tests/compare/direct/export_unit22_wire.py | node server/executor/tools/generate_wire.ts --check
```

Remove inherited `PLAN_*` and provider-home variables before repository execution. Supply private paths and an explicit non-live server URL. Ticket actions through `panels` retain the Worker's identity variables. The generator starts no server and opens no database, provider home, or network connection.

The Python exporter resolves this checkout's `src/` and imports the current protocol, backend snapshot, health and update dataclasses. It keeps Python's protocol manifest and event-codec mapping. The Node tool deduplicates schema definitions, emits one shared type per definition, and checks both generated files byte for byte. The direct comparison check re-exports Python on every run. A changed schema fails even if the protocol version stays unchanged.

The generated schema retains validation limits. TypeScript types express JSON structure only. Runtime codecs must preserve each call's validation mode, byte limits, identity checks and schema constraints. HTTP work decoding uses Python's lax mode. Local IPC uses strict mode, with nested argument decoding as Python defines it. A required nullable field remains required and must carry `null` when absent. A field with a Python default can be omitted. Python accepts unknown object properties on many dataclasses and ignores them. The types list declared fields, rather than promise that unknown properties survive decoding.

The generated open JSON objects do not describe operation-specific bodies. Hand-written families in `protocol_payloads.ts` document known consumer shapes, but types do not validate runtime values. Generic envelope decoding keeps dictionaries open until the selected consumer interprets them, with Python's field and unknown-property rules.

| Open source family | Runtime consumer | Boundary |
| --- | --- | --- |
| `QueuedEnvelope.payload`, selected by `kind` | `MacInbox` and `decode_envelope_payload` | Decode selected items individually, then join their writer and receipt |
| `BackendEvent.arguments`, selected by `method` | `decode_event_arguments` and the conversation core | Construct existing event classes before their selected core operation |
| `CommandOutcome.result` | `MacRemoteChild`, `MacRuns`, and mailbox settlement | Interpret prompt, steer, answer, error, and stop outcomes |
| `AcceptanceReceipt.result` | Canonical agent writes and inbox replay | Preserve the original result and delivery fate with its receipt |
| `CliWrite.arguments` | `vps_agent_writes.ts` | Select the existing canonical writer and its source argument contract |

`CommandResult.model_list` also remains an open dictionary in the generic codec. `VpsCatalogRelay` decodes its BackendSnapshot and checks the requested backend before it publishes the snapshot.

## Executor JSON intake

`protocol_json.ts::parse_executor_json` is the executor's byte-level JSON parser. It supplies Pydantic/jiter lexical behavior before structural wire decoding. It does not apply body-size admission, schema defaults, scalar coercion, trusted identity, or handler-level payload validation.

The lexical parser retains numeric token kinds until structural interpretation. NaN, positive infinity, negative infinity, and float overflow remain explicit native nonfinite numbers. Later executor serialization writes those values as `null`, as Python does. The parser retains UTF-8 byte locations and rejects lone surrogate escapes and malformed UTF-8 without replacement. Its fixed compatibility limits match Python: 200 levels of nonempty container contents and a signed integer lexeme of at most 4,300 characters. An empty container at the next level is accepted when its parent is within the limit.

`ordered_json_entries` preserves first-key insertion order for parsed objects, including integer-looking keys. Duplicate keys update their value without moving that position. For objects constructed elsewhere, the accessor uses their JavaScript key order. Consumers that need source order must retain the parsed object rather than clone it through native enumeration. Syntax errors expose the exact lexical detail and byte position. The shared codec wraps them in the source ValidationError format.

## Shared runtime codec

Both halves import runtime operations from `protocol.ts`. The bounded and frame APIs take value/bytes first and the generated root name second. `decode_json` accepts bytes or text for journal reads without the bounded-body wrapper. HTTP decoding is lenient by default. Strict outer JSON and lenient Python-value paths remain distinct, including ChildCommand’s nested lenient arguments and the outer diagnostic origin. The value entry point accepts already-decoded JSON wire dictionaries and supported event classes. Required nullable fields stay required, defaults appear on decode, unknown fields follow Python’s policy, and the existing command/mailbox checks retain their order.

All structured integer fields use the generated `WireInteger = number | JsonNumber`. Safe integers decode to plain numbers. Integers outside the safe range retain the shared JsonNumber integer marker without rounding in the wire or mailbox. The core adoption boundary explicitly refuses retained turn numbers outside the safe integer range. Structured float fields decode to plain numbers. Opaque JsonValue retains a marker only for an unsafe integer or an integral float whose token kind a plain number erases. The public `integer_value(value)` helper returns the exact bigint for arithmetic, comparisons and SQL bindings. Both peers use this same rule. Do not compare an unsafe integer marker through JavaScript object identity or cast it to number.

Backend event conversion returns the existing classes with exact markers retained where native projection loses precision. The reverse registry matches exact constructors. Retained turn contracts keep the wider integer view until the explicit core-adoption check.

Encoding takes the generated type for its mandatory root and validates before output. It requires nullable fields without defaults, writes source defaults and applies the command/mailbox semantic checks. This is the Manager's required peer-admission policy. It does not claim Python's unchecked serialization of arbitrary malformed dataclasses. Value conversion preserves lone-surrogate strings; byte serialization refuses them. Nonfinite floats become null in JSON-mode output. Typed byte output uses the shared `core/http/numeric_json.ts::stringify_pydantic_json` helper before size admission. The executor has no second numeric formatter. Its lone-surrogate check precedes that helper so that the source serialization error survives.

The JSON-mode result also passes peer validation before output. A nonfinite non-nullable float therefore refuses when its conversion produces null. Bounded messages use lenient peer admission. Frames use strict outer peer admission, including Python's nested command exception. Outgoing native containers must fit the peer's depth cap. Outgoing exact integers must fit the peer's 4,300-character signed decimal-token limit.

The facade exports the common ValueError base for ValidationError, ProtocolRefused and PydanticSerializationError. ProtocolRefused remains the single execution-identity class. Both peers share `parse_identity`, `validate_identity` and `refusal_reason_for_versions`; identity checks do not import the codec facade. UUID admission follows Python's source transformations and hexadecimal integer parser, including its accepted noncanonical forms. KeyError retains its separate source error family.

The same exported schema carries minimal Python annotations for field declaration order, actual enum members, and tuple/list identity. These distinctions support source diagnostics without another schema or generated type file. Runtime schema data is embedded as WIRE_SCHEMA in the existing wire.generated.ts module. wire.schema.json remains a freshness artifact and is not a runtime packaging dependency. JsonObject is an explicit root for plain-dictionary replies such as route 7. Path fields use the shared pure Posix spelling helper, with no home expansion, parent cancellation, symlink resolution or filesystem reads.

## VPS file intake

`files.ts` requires Linux and `/proc/self/fd`. Intake refuses unavailable procfs support before file creation or content consumption. There is no native library or path-based fallback. The Mac half retains its separate platform implementation. Only its existing FileVersion type and pure version comparison helpers are reused.

A directory capability is an owned Node FileHandle. The walk starts at `/`, opens each component with DIRECTORY and NOFOLLOW, and retains the acquired inode through each operation. Peer paths reject absolute paths, dot segments, backslashes, NUL and encoded escape forms. Symlink loops produce the clean 'artifact path contains symlink' refusal. Regular-file opens use NOFOLLOW, NONBLOCK and fstat. Exclusive mode-0600 creation, copy, rename, metadata, cleanup and flush use the same retained parents. A pathname never replaces a retained capability for byte I/O.

Hashing and copying use asynchronous file operations with 256 KiB blocks and exact bigint counts. `descriptor_hash` reads positionally from zero without advancing the descriptor's implicit offset. `file_digest`, `verified_version` and input-file admission share it. Short writes complete before a buffer is reused. Cleanup errors retain the original failure in AggregateError. `file_version_at` supplies synchronous metadata reads. Ordinary `rename_at` and `unlink_at` are asynchronous and finish before their retained parents close. Hashes, copies, parent creation, staging installation, cleanup and directory flushes stay outside the writer.

FileStaging is constructed before the inbox. An upload's held source parent and checked leaf pass through the inbox's second authorization into staging installation. The parent remains owned through cleanup, even if its former pathname changes. A later authorized inbox item publishes staged bytes through bounded synchronous rename groups inside the writer, after authority and version rechecks. Every source and destination parent opens before that writer. Exclusive hold links are created in retained destination parents before publication, then removed asynchronously with directory flushes after success or partial failure. Destination parents remain held through the receipt's unchanged-version check.

Hard-link holds defer overwritten-inode cleanup until after the publication writer.
A same-user destination swap after the check inside the writer and before rename is undetected. It can release large data on the event loop.
Hard crashes can leave `.hold-*`, `.incoming-*`, and `.tmp-*` files. No startup sweeper removes them.

InputFile retains its checked descriptor through a single-consumer stream. It starts at offset zero, emits exactly the hashed count, refuses premature EOF, and never emits an appended suffix. Yielded chunks remain stable across later reads. Python instead hashes a descriptor, returns a path, and later reopens that path in its response. The retained-descriptor contract deliberately closes that substitution race. The input response consumes held chunks and closes the capability on completion or disconnect.

Input bytes stay below the standard message store beside the logical database-path parent. The existing message-file lookup admits the ID and resolves contained symlinks. Its result must stay inside the independently anchored conversation root before a NOFOLLOW descriptor walk opens it. A lookup-root swap therefore cannot redefine byte authority. Contained same-conversation leaf links keep Python behavior. Escapes, loops and later symlink substitutions return the existing not-found answer.

The transfer owner seals new intake at stop and joins admitted transfers through the shared grace period. The supplied request/media signal controls physical stream cancellation at the existing cutoff. Stop adds no grace timer and does not close an unconsumed input immediately.

Uploads require an `UploadSource` with a real `destroy` capability. The HTTP route supplies its owned request Readable. On stall or cancellation, the owner destroys that source before it calls iterator return. It closes and unlinks the incoming file before it joins the real source cleanup. Actual settlement retains every admitted syscall, stream and unexpected cleanup failure.

## VPS command mailbox

`vps_mailbox.ts::SqliteCommandMailbox` takes the application's Database and an optional millisecond clock. The existing synchronous write door owns command admission, run custody and result persistence. Polls capture raw rows, then decode commands and prepare settlement text outside the writer. The final writer checks every selected row against that preparation before any effect, reads current expiry facts and applies changes atomically. A changed selection retries preparation. Stored decoding errors surface only at Python's corresponding row visit and roll the writer back. In-memory waits observe committed rows. A canceled put observer leaves the admitted commit and its wake intact.

Accepted poll and large-replacement latency measurements remain in DEVIATIONS. Gate 3 must compare them with the real table and backlog sizes on a copy of live data before further performance machinery is considered.

Replacement withdraws waiting commands in the lease transaction. Delivered commands settle from the successor's journal cursor. A successful stop result stays delivered until `finish_by_proof` supplies actual child-stop evidence. `publish_settlements` wakes one command's waiters before its listener, then advances to the next command. A listener failure leaves the whole committed batch durable.

Sequence, turn and recovery counters retain exact SQL text reads and bigint bindings. Public values use the shared codec's safe-number or exact-marker rule. Command rows use Pydantic bytes. Outcome rows use validated object conversion followed by sorted standard-library JSON.

`stop({ deadline })` requires the shared absolute monotonic deadline in seconds. It seals new owned work, wakes polls and cancels result waits immediately. Its bounded observer uses `observe_shutdown`. `settled()` retains the actual work and cleanup join, including unexpected failures pending at the first stop. The stateless replacement transaction participant and the single stall source remain usable through their caller's SQL capability after stop. Their caller owns admission and settlement. The mailbox leaves database closure to the invocation owner.

Callers establish authority before they enter this internal API. The transport admits executor ingress, configured ID, protocol, and current incarnation before polls or outcome reports. Incarnation and command arguments are row fences, not credentials. Data helpers require a caller-owned SQL capability, and `finish_by_proof` requires an already-authorized matching `child_stopped` inbox item.

Each held poll owns a removable wake registration. Timeout, abort, listener failure and shutdown release it. Abort reasons use a shared typed cancellation or an AbortError with the original reason as its cause. Waits require finite durations within Node's timer range. Finite negative durations return immediately, and a null result-wait timeout remains unbounded.

### Known Python hazards

These behaviors remain for the named follow-up owners.

| Hazard | Current effect and follow-up owner |
| --- | --- |
| A window contains 32 expired rows with live work behind it | A held poll can sleep until another put or its timeout before it fetches the live row. A VPS mailbox follow-up owns the wake/selection policy. |
| A stored command cannot decode | That poll raises ValidationError and rolls back immediately. Repeated polls at the same cursor fail again, with no automatic skip or repair. A VPS mailbox data-repair follow-up owns recovery policy. The routes slice must preserve the failure rather than silently skip work. |
| The process crashes after commit but before settlement publication | The row remains durable, while the in-memory settlement notice is lost. The runs/core recovery slices (6 and 7) can reread durable results after restart. Durable notification replay requires a separate VPS mailbox follow-up. |
