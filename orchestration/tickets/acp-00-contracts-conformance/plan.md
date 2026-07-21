# ACP-00 implementation plan

## Boundary

This slice freezes types, pins dependencies, vendors the typed state donor, and proves the reusable
test harness. It does not add a route, spawn a production child, compose a broker, change a database,
or render UI. The implementation may touch only the files allowed by `contract.md`; the Hermes
checkout remains read-only.

## 1. Pin the two SDKs and the donor's runtime dependency

Edit `pyproject.toml` and `requirements.txt` together:

- add exact `agent-client-protocol==0.11.0` to project install metadata and the fully pinned runtime
  list;
- import generated request, response, notification, content, permission, tool, and plan models from
  `acp.schema`; use `acp.utils.serialize_params` for wire JSON;
- drive the scripted agent through the public `acp.stdio.spawn_agent_process` client-side helper and
  its returned SDK connection in the ACP-00 harness. ACP-01 still owns the production child wrapper
  and lifecycle composition; no JSON-RPC implementation enters production in ACP-00.

Edit `web/package.json` and regenerate `web/package-lock.json` with exact, non-range dependencies:

- `@agentclientprotocol/sdk@1.2.1`;
- `zustand@5.0.13`;
- `zod@4.4.3`, the SDK peer resolved at the pinned donor revision.

Append `node tests/acp-contracts.test.mjs` to the existing `test` script. Assert the direct and lock
resolutions, including the SDK integrity record, and assert that there is no installed/top-level
`@acp-components/core`, React, React DOM, or `acp-ui` package.

## 2. Vendor the exact framework-free state slice

Materialize commit `525a9d83c5ace577ac0417bf82bf983da4042663` in a temporary directory and copy only these files,
preserving paths below `web/src/vendor/acp-components-core/`:

| Donor path | Required SHA-256 |
| --- | --- |
| `src/types/index.ts` | `7e85ca21deb274ed4d28311e8249a34632f2a86d0cedace28cee379d27b8cfd4` |
| `src/store/sessionStore.ts` | `6d89afc95fbc2758d00293feb6da2c234781403e49abb3dc1d54a87aa34d902f` |
| `src/utils/id.ts` | `74deabced24a6e5342c2aa43c0df6b515d45a0496d144ce792aa74051315a939` |
| `src/transport/types.ts` | `c81be948d5dc9c458ad6fe06ae262154ee242e33f58cd6848e5bab79b9d22343` |

The four files remain byte-for-byte upstream in ACP-00, even where later reducer work must correct
behavior. Add `UPSTREAM.md` with repository, commit, package/path list, copy date `2026-07-19`, the
hash table, the package's MIT declaration, the absence of an upstream standalone license at this
pin, and an empty intentional-corrections section. Add the standard MIT text in `LICENSE` for the
upstream contributors. Do not copy provider/client code, React views, tests, CSS, examples, skills,
or transports.

`tests/unit/test_conversation_contracts.py` rehashes the committed files, parses `UPSTREAM.md`, and
checks that the vendored import graph closes over only these four files plus the exact SDK/Zustand
packages.

## 3. Implement the frozen Python contract package

Create `src/planner/conversation/`:

- `contracts.py` defines the named conversation values from the ticket as frozen Pydantic models
  with `extra="forbid"`. Shared private validators enforce trimmed non-empty identifiers and
  display-safe text. Integer sequences/generations/positions use the stated positive constraints.
  Workspace roots are absolute, ordered, non-empty, and unique after lexical normalization without
  requiring them to exist. Cross-field validators enforce queue position only for `queued`, reason
  required for `rejected` and optional only for `interrupted`, compaction summary only for
  `compacted`, compaction failure reason only for `failed`, and permission cancellation reason iff
  the exact SDK response outcome is `cancelled`. `QueuedPrompt`, permission request/outcome, and
  human prompt values embed the exact SDK models rather than copied unions.
- `configuration.py` contains the one typed constant
  `CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS = 300`.
- `backend_contracts.py` defines frozen capability/backend-definition values and Protocols only.
  Executable argv and environment names/overrides are immutable, ordered, and validated; a named
  cwd resolver returns an absolute path. `AcpEmployeeChild` accepts exact SDK request/notification
  models for initialize/new/load/prompt/cancel and exposes generation/alive/close. The factory takes
  typed update, permission, and death callbacks. `AcpSessionUpdateIngress` is enqueue-only.
  `BackendTurnStrategy` has typed steer and compaction observation/capture results; unsupported steer
  is a typed rejected delivery receipt, never backend-name inference.
- `wire_contracts.py` defines one `wire_version: Literal[1]` server-envelope discriminated union and
  one browser-action discriminated union. Each server variant carries the common employee/entity/
  session/generation/sequence identity plus exactly one of the ten payloads in `contract.md`.
  Validators match inner ACP session IDs to the outer session, nested activity/opened/settled
  sequence values to the envelope sequence, and reset generation to the outer generation. Required
  fields have no default so `serialize_params(..., by_alias/exclude defaults and None)` cannot drop
  `wire_version` or discriminators. Raw agent update validation converts an unknown/missing/partial
  `sessionUpdate` to `protocol_update_rejected` with the fixed public status and keeps raw detail out
  of the model. Browser action validation instead raises a typed Pydantic validation error; a valid
  prompt rejected by delivery policy uses `delivery_receipt: rejected`. Neither path creates text.
- `__init__.py` re-exports the frozen public contracts and adapters, but no implementation object.

Use `TypeAdapter` for both closed unions. Add construction, frozen-assignment, every-literal, every
cross-field, alias serialization, exact option ordering/ID, prompt content-block, inner/outer
session-match, unknown agent update, and invalid browser action cases to
`tests/unit/test_conversation_contracts.py`. Explicitly prove an invalid browser action is not
reported as an agent `protocol_update_rejected` event.

## 4. Mirror only the Panels envelope in TypeScript

Create `web/src/lib/acp/contracts.ts`. Import `SessionNotification`, `PromptRequest`, permission,
and related ACP types from `@agentclientprotocol/sdk`; import the donor state types from the vendored
paths. Define only the ten Panels server-envelope variants and five browser actions, matching Python
field aliases and discriminators. Do not redeclare an ACP content/session/tool/plan/permission union
and do not add runtime transport or reducer behavior.

Create deterministic committed fixtures:

- `tests/fixtures/acp/server-envelopes-v1.json` covers all ten server variants and representative
  text, thought, image, tool, plan, command, usage, and permission SDK payloads;
- `tests/fixtures/acp/browser-actions-v1.json` covers all five actions;
- the thought replay appears only as `agent_thought_chunk`, never as assistant text.

`tests/support/acp_fixture_writer.py` builds those fixtures from the Python models and
`serialize_params`; its normal test mode compares generated bytes to the committed files, while an
explicit maintainer command can rewrite them. `web/tests/acp-contracts.test.mjs` reads the committed
JSON, synthesizes in-memory TypeScript object literals using `as const satisfies` against the
exported Panels unions, and invokes the TypeScript compiler with normal `web/` module resolution.
It also asserts the ACP discriminators/content blocks survive byte-for-byte and the thought has no
assistant duplicate. A separate deterministic source-boundary assertion parses
`web/src/lib/acp/contracts.ts`, requires the authoritative ACP payload types to be imported from
`@agentclientprotocol/sdk` and donor state types from the pinned vendor paths, and rejects local
declarations of the SDK-owned session, prompt, permission, content, tool, plan, command, and usage
union/type names. Together these prove both wire compatibility and type ownership rather than a
hand-written JavaScript lookalike.

## 5. Build the scripted agent and reusable conformance harness

Add only purpose-named `tests/support/acp_*.py` modules:

- `acp_scripted_agent.py` is an executable in-memory agent implemented with the official SDK
  `Agent` interface and `acp.run_agent`/agent-side connection. It supports initialize, new, load,
  prompt, cancel, and permission reverse calls; replays stored typed notifications before returning
  load; sends all diagnostics to stderr; and uses deterministic test metadata switches for delayed
  and concurrent updates, IDs present/absent, automatic compaction provenance, cancellation, and
  death. The sole deliberate malformed-update helper emits an unknown/partial JSON-RPC notification
  in isolation because the SDK correctly refuses to construct it; it stays test-only and all valid
  traffic uses SDK models/transport.
- `acp_conformance.py` defines the manifest record, a narrow `AcpConformanceSubject` Protocol, typed
  per-probe evidence, and the ten assertion functions. The subject exposes one named observation
  method per mandatory probe rather than a generic dictionary/result bag.
- `acp_reference_subject.py` composes the scripted subprocess with the smallest test-only ordered
  reducer, binding holder, turn broker, permission settler, and compaction observer needed to return
  that evidence. It launches the executable through `acp.stdio.spawn_agent_process`, implements the
  official Client callbacks, drives the returned SDK connection through initialize/new/load/prompt/
  cancel, and uses the SDK observer seam for the protocol-only stdout assertion. It never imports the
  Panels database, Hermes, a route, or production runtime.

Add `tests/fixtures/acp/conformance-manifest.json` with exactly these production owners in the
contract's order: ACP-01, ACP-03, ACP-03, ACP-03, ACP-02, ACP-01, ACP-02, ACP-02, ACP-01, ACP-03.
Descriptions remain verbatim enough to map one-to-one to the ten acceptance probes.

`tests/unit/test_acp_conformance_harness.py` must prove:

1. the real subprocess negotiates initialize and performs new/load/prompt/cancel, protocol-only
   stdout, stderr-only diagnostics, typed replay before load response, a permission round trip, and
   deterministic non-zero death;
2. the manifest has exactly ten unique ordered probes and each assertion runs against the reference
   subject with no skip/xfail/network/real-agent dependency;
3. a parametrized mutation proof corrupts the one relevant evidence field for each probe (early
   load response, flattened thought, wrong grouping, appended plan/wrong tool ID, double settlement,
   binding drift, fake steer/broker ownership, silent compaction, callback reorder, text fallback)
   and only the corresponding probe fails.

The deterministic missing-ID fallback is scoped to one turn and role, resets at typed role/tool/plan
boundaries, and is identical for live and replay evidence. Plan snapshots replace; tool progress
reconciles by `toolCallId`; permission evidence covers cancel, death, last-browser disconnect, and
timeout exactly once.

## Acceptance-to-test map

| Named acceptance | Exact proof |
| --- | --- |
| 1. Contract validation/immutability | `tests/unit/test_conversation_contracts.py` contract and wire cases |
| 2. Pins/provenance/absence | same file's metadata, lock, hash, license, and import-graph cases |
| 3. Scripted ACP process | `tests/unit/test_acp_conformance_harness.py` subprocess lifecycle cases |
| 4. Ten probes and mutations | same file's manifest, reference-subject, and parametrized mutation cases |
| 5. Python-to-TypeScript fixtures | fixture-writer check plus `web/tests/acp-contracts.test.mjs` |
| 6. Focused gates | commands below, then one independent implementation review by another sub-agent |

## Implementation order and focused evidence

Implement in this order: dependency pins, exact vendor copy/provenance, Python contracts, fixtures
and TypeScript mirror, scripted agent, conformance subject/tests. Do not start a production route to
make a test pass.

Run once after the slice is settled:

```sh
.venv/bin/python -m pip install -r requirements.txt
npm --prefix web install
.venv/bin/ruff check src/planner/conversation tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py tests/support/acp_*.py
.venv/bin/mypy src/planner/conversation
.venv/bin/pytest tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py
node web/tests/acp-contracts.test.mjs
npm --prefix web run check
npm --prefix web test
```

Record exact output in this ticket's focused-test evidence. A different sub-agent reviews the
allowed-file diff once against `contract.md`, including SDK type ownership, vendor hashes, malformed
update isolation, mutation strength, and the absence of production composition. The orchestrator,
not the implementation agent, runs `./verify` after review and integration.
