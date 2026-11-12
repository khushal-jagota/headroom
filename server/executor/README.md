# Executor wire

Both executor halves import their wire types from `wire.generated.ts`. Python remains the contract author until cutover. Protocol 22 uses HTTPS JSON. The spool format is 5 and the command journal format is 1.

From the repository root, in an isolated development environment:

```sh
scripts/dev python server/executor/tools/export_wire.py | node server/executor/tools/generate_wire.ts
scripts/dev python server/executor/tools/export_wire.py | node server/executor/tools/generate_wire.ts --check
```

Remove inherited `PLAN_*` and provider-home variables before repository execution. Supply private paths and an explicit non-live server URL. Ticket actions through `panels` retain the Worker's identity variables. The generator starts no server and opens no database, provider home, or network connection.

The Python exporter resolves this checkout's `src/` and imports the current protocol, backend snapshot, health and update dataclasses. It keeps Python's protocol manifest and event-codec mapping. The Node tool deduplicates schema definitions, emits one shared type per definition, and checks both generated files byte for byte. The direct comparison check re-exports Python on every run. A changed schema fails even if the protocol version stays unchanged.

The generated schema retains validation limits. TypeScript types express JSON structure only. Runtime codecs must preserve each call's validation mode, byte limits, identity checks and schema constraints. HTTP work decoding uses Python's lax mode. Local IPC uses strict mode, with nested argument decoding as Python defines it. A required nullable field remains required and must carry `null` when absent. A field with a Python default can be omitted. Python accepts unknown object properties on many dataclasses and ignores them. The types list declared fields, rather than promise that unknown properties survive decoding.

The generated open JSON objects do not describe operation-specific bodies. `QueuedEnvelope.payload`, backend event arguments, command outcomes, acceptance receipt results, and CLI-write/model-list dictionaries need hand-written contracts and runtime codecs. This shared-type delivery does not claim their runtime implementation or any executor route.
