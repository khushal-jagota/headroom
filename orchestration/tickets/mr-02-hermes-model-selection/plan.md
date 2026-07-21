# MR-02 — Hermes model selection implementation plan

## Outcome, dependency, and boundary

MR-02 starts only after MR-01 is reviewed and integrated. It is a serial follow-up,
not work against MR-01's provisional interfaces, because it adds the one shared
newer-SDK-to-legacy-wire seam required by Hermes. Do not run another ticket against the
shared files named below until MR-02 lands; MR-03 and MR-04 can follow on the settled seam.

Hermes fills only two backend-specific parts of MR-01:

- Kickoff receives the live model catalog for Hermes's currently configured provider.
- An explicit saved model is applied through legacy `session/set_model` while creating
  the first unbound Hermes session, before the initial binding and first prompt.

The stored model is a historical first-session launch request, not live session state.
Once a binding exists, child load, attach/reload, replacement/recovery, winner adoption,
compaction, and explicit New Conversation paths do not reapply it. The post-Kickoff UI
shows no current Model or Reasoning setting. Hermes advertises and accepts no reasoning
value.

MR-01 continues to own Ticket defaults, the atomic Ticket writer, first-binding race,
catalog REST/cache, Kickoff controls, freeze behavior, and launch-only registry ordering.
MR-02 does not add a provider picker, reset-to-default behavior, header/post-Kickoff
controls, another persisted field, or a second cache. It does not revise MR-01's frozen
contracts.

The integration is pinned to Hermes revision
`047ba829844a557c25ef3e0d4062a862768faab2`. The checkout is read-only: do not edit,
fork, patch, install into, or commit anything under
`/Users/khushaljagota/.hermes/hermes-agent`.

## Grounded backend facts

- `acp_adapter/server.py::_build_model_state` reads Hermes's current provider, calls
  its curated/live provider catalog, inserts the active model when necessary, and uses
  `provider:model` as the selectable identifier.
- Legacy `models` state is returned by `session/new`/`session/load`, but Panels's newer
  ACP SDK discards that removed response field. Discovery therefore cannot parse a
  temporary ACP session and must not create or persist one.
- `set_session_model` handles `session/set_model` with exact `sessionId` and `modelId`
  parameters, resolves `provider:model`, rebuilds the session agent, and persists its
  model. The pinned adapter exposes no functional reasoning option; its generic config
  handler is not a substitute.

## Implementation

1. **Add one read-only Hermes catalog provider behind MR-01's catalog port.**

   Keep Hermes imports outside the Panels process. Run a small Python-3.11-compatible
   probe with the exact resolved Hermes interpreter/source root and the same
   `HERMES_HOME` used by production children. It reads the configured provider/native
   model and calls that provider's pinned curated/live catalog helper. Panels invokes no
   setup, save, session creation/load, or source writer; Hermes may retain only its own
   existing catalog read/cache behavior.

   Map the result to MR-01's frozen catalog shape: preserve ordered model names and
   descriptions; encode IDs exactly as `normalized-provider:model`; include the current
   configured model once at the front if the provider catalog omits it; and return no
   reasoning capability, options, or native reasoning value. Use MR-01's server-lifetime
   cache/deadline. Timeout, non-zero exit, malformed output, missing provider, or an empty
   unusable result is the existing calm catalog failure—never a Panels fallback and never
   a credential-bearing error.

2. **Add the exact legacy model wire bridge and Hermes launch adapter.**

   Add a narrowly named, backend-neutral child capability for legacy ACP model selection.
   The SDK implementation sends exactly:

   ```json
   {
     "method": "session/set_model",
     "params": {"sessionId": "<session>", "modelId": "<provider:model>"}
   }
   ```

   The role-skill child decorator forwards that capability unchanged. The Hermes launch
   adapter requires it, rejects non-null reasoning before any RPC, and sends no request for
   a null model. An explicit model is already validated against the current Hermes catalog.
   A request/protocol failure uses MR-01's first-launch failure path: close the candidate
   child, persist no binding, and send no prompt. Do not use `session/set_config_option`,
   substitute another model, or branch on `"hermes"` in the SDK child or registry.

3. **Wire Hermes through the landed MR-01 registration seam.**

   `_materialize_hermes` builds the catalog provider and launch adapter from the same
   resolved interpreter, executable, source root, and planner home as the durable child.
   Preserve production backend order, executable checks, skill provisioning, confinement,
   role decoration, and Chief behavior. Codex and Claude remain untouched.

   The launch adapter is invoked only in MR-01's `binding is None` first-session branch:

   `session/new -> optional session/set_model -> launch-config CAS -> binding CAS/publication -> first prompt`

   It is deliberately absent from bound `session/load`, attach/replay, replacement,
   adoption, compaction, requested-cancel recovery, and New Conversation branches. Those
   paths trust the ACP session's own current configuration even though the Ticket retains
   its historical Kickoff model.

## Exact write scope

No file outside this list may be edited during implementation.

Shared legacy-wire seam:

- `src/planner/conversation/backend_contracts.py` — add only the separate legacy model
  capability protocol; do not change the generic launch/catalog contracts.
- `src/planner/conversation/sdk_child.py` — implement only the exact raw
  `session/set_model` request on that capability.
- `src/planner/conversation/role_skill_kickoff.py` — forward that method unchanged.
- `src/planner/conversation/__init__.py` — export only the new public capability/adapter.

Hermes provider:

- `src/planner/conversation/hermes_backend.py`
- `src/planner/conversation/backend_catalog.py` — Hermes registration wiring only.
- `src/planner/conversation/hermes_employee_configuration.py` — new catalog and launch
  adapters.
- `src/planner/conversation/hermes_model_catalog_probe.py` — new standalone read-only
  probe compatible with the pinned Hermes interpreter.

Focused test/support files:

- `tests/support/acp_scripted_agent.py` — recognize/audit the exact legacy method only.
- `tests/unit/test_acp_employee_child.py`
- `tests/unit/test_role_skill_kickoff.py`
- `tests/unit/test_hermes_acp_backend.py`
- `tests/unit/test_hermes_employee_configuration.py` — new provider-boundary tests.

Evidence only:

- `orchestration/tickets/mr-02-hermes-model-selection/live-dogfood-evidence.md`

In particular, do not edit `employee_registry.py`, `employee_configuration.py`,
Ticket/Worker-type code, schema, API, `web/`, docs, generic MR-01 tests, Codex/Claude
files, dependencies/locks, or the Hermes checkout. If the landed MR-01 launch-only seam
cannot accept the Hermes adapter without one of those edits, stop and return the concrete
contract mismatch to the orchestrator rather than expanding scope.

## Focused proof

1. **Catalog provider:** injected probe tests prove exact ordered `provider:model` mapping,
   labels/descriptions, active-model insertion/deduplication, exact interpreter/home/source
   selection, no reasoning, cache reuse through MR-01's service, calm malformed/timeout/
   empty failures, and no ACP session or Panels binding creation.
2. **Legacy wire:** the scripted peer records exact camel-case parameters and proves the
   first-session order `new -> set_model -> prompt`. Null model emits no model request;
   non-null reasoning fails before any request; a model-request failure produces no binding
   or prompt.
3. **Historical launch boundary:** use MR-01's landed registry fixtures without editing
   them to exercise a Ticket that still stores an explicit Hermes model. Its first unbound
   session applies the model once. Subsequent bound load, child replacement/recovery, and
   explicit New Conversation record zero `session/set_model` calls. This is a required
   focused assertion, not an inference from the first-launch test.
4. **Decorator and registration:** prove role-skill forwarding is exactly once, production
   Hermes exposes the catalog/launch adapters over the same resolved installation, and the
   generic SDK/registry contain no Hermes-name conditional.
5. Re-run MR-01's unchanged browser assertion that frozen Kickoff controls disappear and
   no post-Kickoff current Model/Reasoning setting is rendered. Add no Hermes frontend test
   component or read-only provenance UI.

Run the affected Pytest files, Ruff on every changed Python file, and strict Mypy on
`src/planner/conversation`. Do not run `./verify`; the final settled multi-backend tree
owns the one canonical verifier run.

## Real Hermes dogfood

Use the production-served app and one disposable fresh Ticket:

1. Record the pinned revision and hashes of the tracked Hermes catalog/ACP source files.
2. In Kickoff, confirm models come only from the configured provider and Reasoning is
   absent; select a non-default advertised model and approve.
3. Let the first Employee prompt complete. Resolve the durable ACP binding and inspect that
   same planner-Hermes session: its persisted model matches the selection and its history
   contains the first prompt/response, proving model application preceded work.
4. Reload the Ticket and confirm Kickoff shows no current Model/Reasoning settings. Do not
   interpret the stored Ticket value as live state or force a later session back to it.
5. Re-hash the tracked Hermes files and record that the checkout source is unchanged.

Record only model/provider, Ticket/session identifiers, focused gates, first-prompt
evidence, and before/after hashes in the allowed evidence file. Never record credentials
or secret-bearing configuration.

## Explicit non-scope

- Reapplying historical Kickoff values to any bound or later conversation.
- Hermes reasoning, `/reasoning`, fast mode, or a Panels workaround.
- Provider selection or catalogs outside the current configured provider.
- Changes to defaults, Ticket persistence, Kickoff controls, freeze rules, headers, or
  post-Kickoff presentation.
- Hermes source/install changes, Codex/Claude work, silent fallback, retries, or background
  refresh.
