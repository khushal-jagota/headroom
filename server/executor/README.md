# Executor wire

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

The generated open JSON objects do not describe operation-specific bodies. Hand-written families in `protocol_payloads.ts` document known consumer shapes, but types do not validate runtime values. Generic envelope decoding keeps these dictionaries open until the original consumer invokes its decoder. The following ledger names each source family and the remaining consumer work. No executor route or native runner is supplied by this codec package.

| Open source family | Current runtime behavior | Remaining closure |
| --- | --- | --- |
| `QueuedEnvelope.payload`, selected by `kind` | `decode_envelope_payload` selects and decodes BackendEvent, CliWrite, CommandOutcome or AnswerApplication. Generic batches remain opaque. | Slice 5 must prove per-item receipt admission and refusal without whole-batch rejection. |
| `BackendEvent.arguments`, selected by `method` | `decode_event_arguments` decodes the selected root and constructs an existing event class. Types alone do not close the envelope/consumer path. | Slice 5 inbox and slice 6 remote runs must compare malformed selected arguments at their actual consumer boundary. |
| `CommandOutcome.result` | The generic codec retains the open dictionary. Known variants are documentary types. | Slice 6 remote child/run consumers must compare prompt, steer, answer, error and settlement results. The Mac-half runner owns their producer proof. |
| `AcceptanceReceipt.result` | The generic codec retains the open dictionary. Known variants are documentary types. | Slice 5 canonical agent writes/receipts must compare delivery, Ticket, blocker and error results. The Mac-half CLI owns result consumption. |
| `CliWrite.arguments` | The generic codec retains the open dictionary. Known operation bodies are documentary types. | Slice 5 must close operation-selected CLI bodies through canonical writers. |

The additional `CommandResult.model_list` dictionary also remains open. BackendSnapshot is an available root, and slice 6 catalog relay must apply its decoder and requested-backend check. The codec does not automatically validate that consumer-specific result.

## Executor JSON intake

`protocol_json.ts::parse_executor_json` is the executor's byte-level JSON parser. It supplies Pydantic/jiter lexical behavior before structural wire decoding. It does not apply body-size admission, schema defaults, scalar coercion, trusted identity, or handler-level payload validation.

The lexical parser retains numeric token kinds until structural interpretation. NaN, positive infinity, negative infinity, and float overflow remain explicit native nonfinite numbers. Later executor serialization writes those values as `null`, as Python does. The parser retains UTF-8 byte locations and rejects lone surrogate escapes and malformed UTF-8 without replacement. Its fixed compatibility limits match Python: 200 levels of nonempty container contents and a signed integer lexeme of at most 4,300 characters. An empty container at the next level is accepted when its parent is within the limit.

`ordered_json_entries` preserves first-key insertion order for parsed objects, including integer-looking keys. Duplicate keys update their value without moving that position. For objects constructed elsewhere, the accessor uses their JavaScript key order. Consumers that need source order must retain the parsed object rather than clone it through native enumeration. Syntax errors expose the exact lexical detail and byte position. The shared codec wraps them in the source ValidationError format.

## Shared runtime codec

Both halves import runtime operations from `protocol.ts`. The bounded and frame APIs take value/bytes first and the generated root name second. `decode_json` accepts bytes or text for journal reads without the bounded-body wrapper. HTTP decoding is lenient by default. Strict outer JSON and lenient Python-value paths remain distinct, including ChildCommand’s nested lenient arguments and the outer diagnostic origin. The value entry point accepts already-decoded JSON wire dictionaries and supported event classes. Required nullable fields stay required, defaults appear on decode, unknown fields follow Python’s policy, and the existing command/mailbox checks retain their order.

All structured integer fields use the generated `WireInteger = number | JsonNumber`. Safe integers decode to plain numbers. Integers outside the safe range retain the shared JsonNumber integer marker without rounding or a new unsafe-integer refusal. Structured float fields decode to plain numbers. Opaque JsonValue retains a marker only for an unsafe integer or an integral float whose token kind a plain number erases. The public `integer_value(value)` helper returns the exact bigint for arithmetic, comparisons and SQL bindings. Both peers use this same rule. Do not compare an unsafe integer marker through JavaScript object identity or cast it to number.

Backend event conversion returns the existing classes with exact markers retained where native projection loses precision. The reverse registry matches exact constructors. The VPS/core integration must retain the wider integer view at its own boundary.

Encoding takes the generated type for its mandatory root and validates before output. It requires nullable fields without defaults, writes source defaults and applies the command/mailbox semantic checks. This is the Manager's required peer-admission policy. It does not claim Python's unchecked serialization of arbitrary malformed dataclasses. Value conversion preserves lone-surrogate strings; byte serialization refuses them. Nonfinite floats become null in JSON-mode output. Typed byte output uses the shared `core/http/numeric_json.ts::stringify_pydantic_json` helper before size admission. The executor has no second numeric formatter. Its lone-surrogate check precedes that helper so that the source serialization error survives.

The JSON-mode result also passes peer validation before output. A nonfinite non-nullable float therefore refuses when its conversion produces null. Bounded messages use lenient peer admission. Frames use strict outer peer admission, including Python's nested command exception. Outgoing native containers must fit the peer's depth cap. Outgoing exact integers must fit the peer's 4,300-character signed decimal-token limit.

The facade exports the common ValueError base for ValidationError, ProtocolRefused and PydanticSerializationError. ProtocolRefused remains the single execution-identity class. Both peers share `parse_identity`, `validate_identity` and `refusal_reason_for_versions`; identity checks do not import the codec facade. UUID admission follows Python's source transformations and hexadecimal integer parser, including its accepted noncanonical forms. KeyError retains its separate source error family.

The same exported schema carries minimal Python annotations for field declaration order, actual enum members, and tuple/list identity. These distinctions support source diagnostics without another schema or generated type file. Runtime schema data is embedded as WIRE_SCHEMA in the existing wire.generated.ts module. wire.schema.json remains a freshness artifact and is not a runtime packaging dependency. JsonObject is an explicit root for plain-dictionary replies such as route 7. Path fields use the shared pure Posix spelling helper, with no home expansion, parent cancellation, symlink resolution or filesystem reads.

## VPS command mailbox

`vps_mailbox.ts::SqliteCommandMailbox` takes the application's Database and an optional millisecond clock. The existing synchronous write door owns command admission, run custody and result persistence. Polls capture raw rows, then decode commands and prepare settlement text outside the writer. The final writer checks every selected row against that preparation before any effect, reads current expiry facts and applies changes atomically. A changed selection retries preparation. Stored decoding errors surface only at Python's corresponding row visit and roll the writer back. In-memory waits observe committed rows. A canceled put observer leaves the admitted commit and its wake intact.

With 50,000 old rows and a poll at the 4 MiB byte bound, the measured largest event-loop gap is 72.6 ms. The Manager accepts that cost for a poll every few seconds. Gate 3 must compare this baseline with the real table size on a copy of live data.

The fixture reactivates 3,000 of its 50,000 history rows as waiting commands (504,801 encoded bytes), leaving 47,000 closed history rows. Their replacement takes 338.5 ms inside the actual transaction and 365.3 ms through reply encoding. The largest measured event-loop gap is 377.2 ms. Python completes the corresponding lease in 110.2 ms. This lease cost remains synchronous; Gate 3 must compare it with the real replacement backlog beside the poll baseline.

Replacement withdraws waiting commands in the lease transaction. Delivered commands settle from the successor's journal cursor. A successful stop result stays delivered until `finish_by_proof` supplies actual child-stop evidence. `publish_settlements` wakes one command's waiters before its listener, then advances to the next command. A listener failure leaves the whole committed batch durable.

Sequence, turn and recovery counters retain exact SQL text reads and bigint bindings. Public values use the shared codec's safe-number or exact-marker rule. Command rows use Pydantic bytes. Outcome rows use validated object conversion followed by sorted standard-library JSON.

`stop({ deadline })` requires the shared absolute monotonic deadline in seconds. It seals new owned work, wakes polls and cancels result waits immediately. Its bounded observer uses `observe_shutdown`. `settled()` retains the actual work and cleanup join, including unexpected failures pending at the first stop. The stateless replacement transaction participant and the single stall source remain usable through their caller's SQL capability after stop. Their caller owns admission and settlement. The mailbox leaves database closure to its caller. Later executor composition registers this owner through the application's producer mechanism.

Callers establish authority before they enter this internal API. Transport callers authenticate executor ingress and admit the configured executor ID, protocol and current incarnation before polls or outcome reports. Incarnation and command arguments are row fences, not credentials. The routes slice owns that admission. Data helpers require a caller-owned SQL capability, and `finish_by_proof` requires an already-authorized matching `child_stopped` inbox item.

Each held poll owns a removable wake registration. Timeout, abort, listener failure and shutdown release it. Abort reasons use a shared typed cancellation or an AbortError with the original reason as its cause. Waits require finite durations within Node's timer range. Finite negative durations return immediately, and a null result-wait timeout remains unbounded.

### Known Python hazards

These behaviors remain for the named follow-up owners.

| Hazard | Current effect and follow-up owner |
| --- | --- |
| A window contains 32 expired rows with live work behind it | A held poll can sleep until another put or its timeout before it fetches the live row. A VPS mailbox follow-up owns the wake/selection policy. |
| A stored command cannot decode | That poll raises ValidationError and rolls back immediately. Repeated polls at the same cursor fail again, with no automatic skip or repair. A VPS mailbox data-repair follow-up owns recovery policy. The routes slice must preserve the failure rather than silently skip work. |
| The process crashes after commit but before settlement publication | The row remains durable, while the in-memory settlement notice is lost. The runs/core recovery slices (6 and 7) can reread durable results after restart. Durable notification replay requires a separate VPS mailbox follow-up. |
