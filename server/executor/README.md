# Executor wire

Both executor halves import their wire types from `wire.generated.ts`. Python remains the contract author until cutover. Protocol 22 uses HTTPS JSON. The spool format is 5 and the command journal format is 1.

From the repository root, in an isolated development environment:

```sh
scripts/dev python tests/compare/direct/export_unit22_wire.py | node server/executor/tools/generate_wire.ts
scripts/dev python tests/compare/direct/export_unit22_wire.py | node server/executor/tools/generate_wire.ts --check
```

Remove inherited `PLAN_*` and provider-home variables before repository execution. Supply private paths and an explicit non-live server URL. Ticket actions through `panels` retain the Worker's identity variables. The generator starts no server and opens no database, provider home, or network connection.

The Python exporter resolves this checkout's `src/` and imports the current protocol, backend snapshot, health and update dataclasses. It keeps Python's protocol manifest and event-codec mapping. The Node tool deduplicates schema definitions, emits one shared type per definition, and checks both generated files byte for byte. The direct comparison check re-exports Python on every run. A changed schema fails even if the protocol version stays unchanged.

The generated schema retains validation limits. TypeScript types express JSON structure only. Runtime codecs must preserve each call's validation mode, byte limits, identity checks and schema constraints. HTTP work decoding uses Python's lax mode. Local IPC uses strict mode, with nested argument decoding as Python defines it. A required nullable field remains required and must carry `null` when absent. A field with a Python default can be omitted. Python accepts unknown object properties on many dataclasses and ignores them. The types list declared fields, rather than promise that unknown properties survive decoding.

The generated open JSON objects do not describe operation-specific bodies. The hand-written families in `protocol_payloads.ts` supply their consumer types. Generic envelope decoding keeps these dictionaries open until the original consumer invokes its decoder. No executor route or native runner is supplied by this codec package.

## Executor JSON intake

`protocol_json.ts::parse_executor_json` is the executor's byte-level JSON parser. It supplies Pydantic/jiter lexical behavior before structural wire decoding. It does not apply body-size admission, schema defaults, scalar coercion, trusted identity, or handler-level payload validation.

Finite numbers use the shared `JsonNumber` representation through `NumericJSON`. NaN, positive infinity, negative infinity, and float overflow remain explicit native nonfinite numbers. Later executor serialization writes those values as `null`, as Python does. The parser retains UTF-8 byte locations and rejects lone surrogate escapes and malformed UTF-8 without replacement. Its fixed compatibility limits match Python: 200 levels of nonempty container contents and a signed integer lexeme of at most 4,300 characters. An empty container at the next level is accepted when its parent is within the limit.

`ordered_json_entries` preserves first-key insertion order for parsed objects, including integer-looking keys. Duplicate keys update their value without moving that position. For objects constructed elsewhere, the accessor uses their JavaScript key order. Consumers that need source order must retain the parsed object rather than clone it through native enumeration. Syntax errors expose the exact lexical detail and byte position. The shared codec wraps them in the source ValidationError format.

## Shared runtime codec

Both halves import runtime operations from `protocol.ts`. The bounded and frame APIs take value/bytes first and the generated root name second. `decode_json` accepts bytes or text for journal reads without the bounded-body wrapper. HTTP decoding is lenient by default. Strict outer JSON and lenient Python-value paths remain distinct, including ChildCommand’s nested lenient arguments and the outer diagnostic origin. The value entry point accepts already-decoded JSON wire dictionaries and supported event classes. Required nullable fields stay required, defaults appear on decode, unknown fields follow Python’s policy, and the existing command/mailbox checks retain their order.

Decoded numbers use `NumericJSON` and the shared `JsonNumber` custody. Do not cast away an unsafe integer marker. Backend event conversion returns the existing classes with exact markers retained where native projection loses precision. The reverse registry matches exact constructors. The VPS/core integration must decide its numeric projection at its own boundary.

Encoding does not revalidate or coerce malformed values through the decoder. It serializes normalized DTOs and supported existing classes. Generic value conversion and typed byte serialization retain their separate Python behavior. Value conversion preserves lone-surrogate strings; byte serialization refuses them. Nonfinite floats become null in JSON-mode output. Typed byte output uses the shared `core/http/numeric_json.ts::stringify_pydantic_json` helper before size admission. The executor has no second numeric formatter. Its lone-surrogate check precedes that helper so that the source serialization error survives. ProtocolRefused is the shared execution-identity class. ValidationError and KeyError preserve source names and diagnostics.

The same exported schema carries minimal Python annotations for field declaration order, actual enum members, and tuple/list identity. These distinctions support source diagnostics without another schema or generated type file. Path fields use the shared pure Posix spelling helper, with no home expansion, parent cancellation, symlink resolution or filesystem reads.
