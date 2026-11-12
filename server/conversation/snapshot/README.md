# Backend cards and explicit provider limits

The eight backend routes depend on `BackendHttpRuntime` in `contracts.ts`.
`composition.ts` receives provider implementations as values. Routes never import
provider adapters. Card reads cache local catalog facts. Only an explicit usage
refresh acquires provider limits. Successful observations replace cached limits in
one short transaction. Failed reads retain the last successful observation.

`provider_contracts.ts` defines the Python-shaped handoff:

| Interface | Real implementation owner | Current production binding |
| --- | --- | --- |
| `ClaudeModelCatalogSource` | Claude adapter, Unit 18 | `production.ts` binds `probe_claude_model_catalog` from PR 747 |
| `CodexModelCatalogSource` | Codex adapter, Unit 17 | Open |
| `SavedCodexLoginSource` | Codex adapter, Unit 17 | Open |
| `CodexSignInService` | Codex adapter, Unit 17 | Open |
| `HermesModelCatalogSource` | Hermes adapter, Unit 16 | Open |

`StoredCodexLogin`, `CodexLoginGeneration`, `CodexLoginRejectionMemory`, and
`CodexLoginStanding` preserve the login generation and card comparison operands.
The saved-login source receives the durable rejection memory. The sign-in service
owns its child and must stop it in `shutdown`. Catalog readers translate only the
declared unavailable errors. The Hermes source returns the retained Python
inventory script's command outcome, including its output and error text.

`../backend_usage/contracts.ts` defines `CodexUsageSource`. Unit 17 must supply a
short-lived `account/rateLimits/read` operation. It must initialize the app server,
honor the supplied 30-second deadline and cancellation signal, and stop and join
the child before it resolves or rejects. It reports timeout, rejected requests,
and duplicate login with the declared usage errors. Pass that source into
`CodexUsageAdapter`, then inject the adapter through `BackendProviders.usage`.
This real binding remains open. Hermes intentionally has no usage adapter.

Claude usage reads its local OAuth credential file and uses the guarded HTTP
transport. Tests supply synthetic files and a recording transport. The production
composition returns no providers in test mode. Production process and HTTP doors
also refuse test contexts before any external action.

The comparison host uses `RecordingBackendProbeEnvironment` and explicit scripted
catalogs. `testing/ticket_models.ts` owns the eleventh browser control, which swaps
the same source that the backend routes read. Direct checks compare full cards,
all eight HTTP routes, source rows, recording calls, and provider limit decisions
against Python. They do not prove real provider login, child lifetime, network
access, or the open Codex and Hermes bindings. Browser checks run in hosted CI.
