# Backend cards and explicit provider limits

The eight backend routes depend on `BackendHttpRuntime` in `contracts.ts`.
`composition.ts` receives provider implementations as values. Routes never import
provider adapters. Card reads cache local catalog facts. Only an explicit usage
refresh acquires provider limits. Successful observations replace cached limits in
one short transaction. Failed reads retain the last successful observation.

Each HTTP operation supplies its cancellation signal to probes and update commands.
`commands.ts` owns each detached command group. Abort, timeout, and shutdown kill
that group and wait for its direct child's exit. Completion does not wait for a
descendant to close inherited pipes. Server shutdown stops commands before record
closure. HTTP transports use `redirect: 'manual'`, so a redirect cannot resend a
usage credential. The focused scripted-child and mocked-redirect checks prove
these boundaries without a provider executable or network request.

`provider_contracts.ts` defines the Python-shaped handoff:

| Interface | Real implementation owner | Current production binding |
| --- | --- | --- |
| `ClaudeModelCatalogSource` | Claude adapter, Unit 18 | `production.ts` binds `probe_claude_model_catalog` from PR 747 |
| `CodexModelCatalogSource` | Codex adapter, Unit 17 | `production.ts` binds `create_codex_providers` from merged PR 779 |
| `SavedCodexLoginSource` | Codex adapter, Unit 17 | Bound through `create_codex_providers` with durable rejection memory |
| `CodexSignInService` | Codex adapter, Unit 17 | Bound through `create_codex_providers` with database and clock |
| `HermesModelCatalogSource` | Hermes adapter, Unit 16 | Open |

`StoredCodexLogin`, `CodexLoginGeneration`, `CodexLoginRejectionMemory`, and
`CodexLoginStanding` preserve the login generation and card comparison operands.
The saved-login source receives the durable rejection memory. The sign-in service
owns its child and must stop it in `shutdown`. Catalog readers translate only the
declared unavailable errors. The Hermes source returns the retained Python
inventory script's command outcome, including its output and error text.

`../backend_usage/contracts.ts` defines `CodexUsageSource`. Unit 17 supplies the
bound short-lived `account/rateLimits/read` operation through `create_codex_providers`. It must initialize the app server,
honor the supplied 30-second deadline and cancellation signal, and stop and join
the child before it resolves or rejects. It reports timeout, rejected requests,
and duplicate login with the declared usage errors. Pass that source into
`CodexUsageAdapter`, then inject the adapter through `BackendProviders.usage`.
This real binding is composed in `production.ts`. Hermes intentionally has no usage adapter.

`CodexUsageSource` returns the raw JSON text of the `account/rateLimits/read`
result. The source must preserve numeric tokens instead of parsing and
reserializing them. `parse_codex_windows` owns the numeric JSON parse and rejects
floating-point tokens such as `25.0` where Python requires a strict integer.

`BackendProviders.machine_catalog` supplies `RemoteCatalogSource` for Tickets on
the Mac. The production binding currently returns `null`. Executor Unit 22 must
bind its live or last offline catalog through this callback. Until then, scoped
Mac reads answer `The selected machine is unavailable`. The local backend and
control proofs do not prove that remote binding.

`CodexSignIn.started_at` and `deadline_at` accept `Date` or a string in Python's
`datetime.isoformat()` form. A source with microseconds must supply the string.
The response preserves it exactly. A `Date` retains its millisecond precision.
The existing HTTP proof mixes a six-digit source timestamp with a whole-second
`Date` without a new host or case matrix.

Claude usage reads its local OAuth credential file and uses the guarded HTTP
transport. Tests supply synthetic files and a recording transport. The production
composition returns no providers in test mode. Production process and HTTP doors
also refuse test contexts before any external action.

Test-mode composition and the comparison host use
`RecordingBackendProbeEnvironment` from `recording_environment.ts` and scripted
catalogs. `testing/ticket_models.ts` owns the eleventh browser control, which swaps
the same source that the backend routes read. Direct checks compare full cards,
all eight HTTP routes, source rows, recording calls, and provider limit decisions
against Python. They do not prove real provider login, child lifetime, network
access, or the open Hermes binding. Browser checks run in hosted CI.

The comparison host in `server/test_host.ts` supplies unavailable providers and
a fixed `TicketModelSnapshots` source. Its HTTP comparisons prove route decisions
and projections, not real provider bindings. `test_unit17_provider_parity` in
`tests/compare/direct/test_unit17_providers.py` exercises the Codex implementations
against a scripted app server, including a real `create_codex_providers` composition
for sign-in shutdown. `test_unit18_model_catalog` in
`tests/compare/direct/test_unit18_startup.py` compares Claude catalog transformation
with a fake SDK handshake. `test_unit18_adapter_real_core_parity` in
`tests/compare/direct/test_unit18_adapter.py` exercises its adapter through a local
scripted CLI and the real core. None proves an external provider account or network.
