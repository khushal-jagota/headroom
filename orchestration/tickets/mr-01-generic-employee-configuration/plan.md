# MR-01 implementation plan: generic Ticket employee configuration

## Outcome and boundaries

Implement the frozen `orchestration/acp-model-selection/contract.md` as one generic
vertical slice. A Worker type declares a default Employee backend, model, and reasoning
effort; ordinary Ticket creation copies that trio once. The stored Ticket trio is then
the launch authority only until first binding; afterward model and reasoning remain a
historical record of the Kickoff request, while the ACP session owns its live state. The
trio is editable only inside the existing Kickoff section before Kickoff starts and
before any Employee session or binding exists. There is no reset to the Worker-type
defaults, no per-backend remembered value, no header control, and no control or
current-setting claim after Kickoff. Chief of Staff remains unchanged.

MR-01 proves the contracts with the scripted ACP backend. It creates the provider hooks
that MR-02 through MR-04 will fill, but does not implement real Hermes, Codex, or Claude
catalog discovery and does not change their current native-default behavior.

## Frozen implementation contracts

- Rename `WorkerProfile.model` and `reasoning_effort` to
  `default_employee_model` and `default_employee_reasoning_effort`; retain
  `default_employee_backend`. Production Worker types remain Hermes / null / null.
  Serve all three defaults in the Worker-type manifest for truthful configuration, but
  never use the manifest to reset or normalize an existing Ticket.
- Add flat Ticket fields `employee_launch_model: str | None` and
  `employee_launch_reasoning_effort: str | None` beside the required
  `employee_backend`. Null means the first session's backend-native default. After
  binding, the two stored values are historical launch inputs rather than a live mirror.
  Do not add either value to `conversation_session_bindings`.
- Replace the backend-only update with one exact-body
  `PUT /api/tickets/{ticket_id}/employee-configuration` accepting exactly
  `employee_backend`, `employee_launch_model`, and
  `employee_launch_reasoning_effort` (the latter two are nullable). One
  `write_employee_configuration` transaction validates and writes the normalized
  complete trio and emits one `ticket_updated` event whose field is
  `employee_configuration` and whose `from`/`to` values are complete trio objects.
- Put dependency normalization in a framework-free Ticket logic function. Changing
  backend produces `(new backend, null, null)`. Changing model retains an explicit
  reasoning value only when the candidate-model catalog still advertises it; otherwise
  it becomes null. An unchanged explicit value must still be advertised. A true
  complete-trio no-op remains a no-op even after freeze.
- Use one exact pristine predicate everywhere: `needs_kickoff`, status `empty` or
  `awaiting_approval`, null Ticket session mirror, and no binding row. Serve its result
  as `employee_configuration_editable` in Ticket detail so the browser and writer use
  the same server-owned boundary.
- Add immutable conversation contracts for a catalog option, a backend/model catalog,
  and an Employee session configuration adapter. Extend
  `MaterializedEmployeeBackendRegistration` with its adapter. The default adapter
  exposes native defaults only and rejects explicit values; the scripted registration
  uses the generic ACP config-option adapter. This keeps MR-01 green while leaving the
  three production adapters to their follow-up MRs.
- Extend `AcpEmployeeChild` and both implementations/decorators
  (`SdkAcpEmployeeChild`, role-skill wrapper) with the stable
  `set_config_option(session_id, config_id, value)` and temporary
  `close_session(session_id)` operations. The generic ACP adapter selects options by
  semantic category (`model`, then `thought_level`), never by a provider-specific ID.
- Add an `EmployeeConfigurationCatalogService` over the materialized registrations.
  It owns server-lifetime caching keyed by `(backend, candidate_model)`, opens an
  unpublished temporary child/session for live discovery, applies an optional candidate
  model before reading dependent reasoning choices, and closes both the temporary
  session and child in `finally`. Discovery has no Ticket employee identity and never
  calls the binding repository. Expose it through
  `GET /api/employee-configuration-catalog?employee_backend=...&candidate_model=...`;
  omitted `candidate_model` means the backend-native model and makes no claim about a
  live Employee session.
- Add nullable `employee_launch_model` and `employee_launch_reasoning_effort` fields to
  `ConversationEmployee`, populated from the Ticket only while no binding exists. The
  registry applies them only to the
  first `new_session` response, model before reasoning, before attempting the first
  binding. The first-binding CAS receives that prepared launch configuration and, while
  holding `BEGIN IMMEDIATE`, compares its complete trio with the current Ticket row
  before persisting the unchanged binding shape. Once a binding exists, employee
  resolution carries no requested model/reasoning and every load, child replacement,
  compaction recovery, and explicit New Conversation trusts the ACP session/native state
  without reapplying historical Kickoff values. Stale or invalid first-session candidates
  close their child and cannot publish or prompt.
- If first-binding CAS returns another candidate as the winner, retire the losing child,
  re-resolve the Employee through the binding repository, and require the resolved bound
  Employee to have null launch inputs before loading and publishing the winner. Winner
  adoption never carries the loser's requested launch values and never invokes the
  configuration adapter against the already-bound winning session.

## Sequenced implementation

1. **Land schema and domain contracts.** In `src/planner/core/db.py`, advance v26 to
   v27, add the two nullable Ticket columns to canonical DDL, and add an atomic v27
   migration that preserves every v26 value and binding while seeding both columns to
   null. Update `src/planner/tickets/contracts.py`, `src/planner/worker_types/contracts.py`,
   the four production definitions under `src/planner/worker_types/`,
   `src/planner/worker_types/registry.py`, and `tests/support/probe.py`. Update Ticket
   row loading/serialization and all explicit Ticket INSERT/SELECT fixtures for the new
   fields. Do not alter the binding-table DDL.

2. **Seed once and add the atomic writer.** In `src/planner/tickets/data.py`, seed all
   ordinary and external-work Ticket creation paths from the Worker profile once;
   preserve the existing optional create-time backend override by normalizing a changed
   backend to null model/reasoning. Add
   `src/planner/tickets/logic/employee_configuration.py` for catalog validation and
   backend/model dependency normalization. Replace `write_employee_backend` with the
   complete-trio transaction and centralize the exact freeze check (including the
   binding query). In `src/planner/tickets/api.py`, add strict nullable-string body
   parsing, the catalog read route/dependency, and the exact configuration PUT route.
   Update `src/planner/tickets/views.py` to serialize both values and the authoritative
   editable flag. Update the existing CLI backend setter in `src/planner/cli/main.py` to
   call the new complete endpoint with null dependent values; do not add model/reasoning
   CLI selectors in this MR.

3. **Build the generic catalog/application seam.** Define the option, catalog,
   discovery/apply protocol, native-default adapter, and semantic ACP adapter in a new
   `src/planner/conversation/employee_configuration.py`, with only the provider-neutral
   types in `backend_contracts.py`. Extend registrations in `backend_catalog.py` and
   composition in `conversation/composition.py` so one set of materialized backends is
   shared by the runtime and the new cached catalog service. Expose that service through
   `ConversationComposition` and `core/server.py`; return the normal calm 503 envelope
   when conversation/catalog composition is unavailable. Export the public contracts
   from `conversation/__init__.py`.

4. **Configure only the first unbound session before publication.** Add the two stable child
   calls in `conversation/sdk_child.py` and forward them in
   `conversation/role_skill_kickoff.py`. In `conversation/employee_registry.py`, invoke
   one `_configure_initial_session_before_first_binding` helper only in the `binding is
   None` branch after `new_session`: pass its initial `config_options`, apply model first,
   consume the returned refreshed options, then apply reasoning before first CAS. Leave
   existing-binding load, browser attach reload, explicit New Conversation,
   requested-cancel replacement, compaction candidate load, and external-winner adoption
   on their current unconfigured lifecycle paths so ACP state is not overwritten. In
   `conversation/sqlite_binding_repository.py`, make only first-binding CAS validate the
   prepared complete launch configuration atomically without persisting it in the binding
   row; replacement CAS retains its existing binding-only contract. Change the registry's
   first-binding loser path to close its candidate, re-resolve the now-bound Employee,
   assert its launch inputs are cleared, and load/adopt the winning binding without any
   set-config call. Keep
   `runtime/acp_step_gateway.py` unchanged except for type propagation: browser-first and
   automatic-first demand both converge on the same unbound registry branch before any
   prompt.

5. **Move setup into Kickoff.** Add
   `web/src/components/EmployeeConfigurationSetup.svelte` using the existing restrained
   form primitives. It renders Worker, Model, and conditional Reasoning; includes native
   default options mapped to null; loads the backend/candidate-model REST catalog;
   retains saved values during a local error; offers Retry; and uses an abort controller
   plus request generation so stale replies cannot win. Worker and model edits send one
   complete snapshot and adopt the server-normalized response. Add a small snippet seam
   to `TicketStageSection.svelte` so `TicketRoute.svelte` renders this component only in
   the Kickoff section immediately beside its approval flow. Remove the Worker control
   from the header. After freeze, remove the setup controls and do not render the stored
   model/reasoning as current settings anywhere. Use the served editable flag for both
   controls and `AcpConversation.deferInitialAttach`. Extend `web/src/lib/types.ts`; do
   not create a frontend provider inventory or a reset action.

6. **Complete the scripted proof and live docs.** Extend
   `tests/support/acp_scripted_agent.py` with model-dependent select config options,
   `session/set_config_option`, temporary close observation, deterministic delay/failure/
   disappearance controls, and prompt audit of the effective model/reasoning. Wire its
   registration in `tests/support/acp_e2e_server.py` to the generic ACP adapter. Update
   `docs/worker-types.md`, `docs/tickets-and-gates.md`, `docs/employee-runtime.md`, and
   the matching summary in `docs/README.md` so they describe Kickoff-local setup,
   seed-once defaults, first-session-only application, historical post-binding values,
   and ACP-owned live state; remove the current header-selector description.

## Named acceptance tests

- `tests/unit/test_db.py`
  - `test_fresh_v27_ticket_configuration_is_nullable_and_binding_schema_has_no_copy`
  - `test_v26_to_v27_preserves_ticket_and_binding_bytes_and_seeds_null_configuration`
  - `test_v27_reopen_preserves_explicit_ticket_configuration_and_is_idempotent`
- `tests/unit/test_worker_type_registry.py` and
  `tests/unit/test_worker_type_manifest_endpoint.py`
  - `test_worker_profile_declares_complete_employee_defaults`
  - `test_manifest_serves_complete_defaults_without_becoming_ticket_authority`
- Replace the backend-only cases in `tests/unit/test_ticket_edit_api.py` with:
  - `test_ticket_creation_copies_worker_type_configuration_once`
  - `test_employee_configuration_endpoint_requires_the_exact_complete_nullable_body`
  - `test_employee_configuration_writer_normalizes_worker_and_model_dependencies`
  - `test_employee_configuration_noop_after_freeze_emits_nothing`
  - `test_employee_configuration_change_rejects_every_pristine_freeze_boundary`
  - `test_employee_configuration_writer_and_first_binding_race_in_both_commit_orders`
    (in both orders, assert no published runtime carries stale loser launch inputs and no
    already-bound winner receives a loser configuration request)
- New `tests/unit/test_employee_configuration_catalog.py`
  - `test_catalog_discovery_is_cached_by_backend_and_candidate_model_and_closes_temporary_session`
  - `test_catalog_discovery_never_creates_a_ticket_session_or_binding`
  - `test_catalog_failure_returns_no_fabricated_options_and_can_retry`
  - `test_semantic_adapter_applies_model_before_reasoning_from_refreshed_options`
- Extend `tests/unit/test_acp_binding_repository.py`,
  `tests/unit/test_acp_employee_registry.py`, and
  `tests/unit/test_acp_employee_child.py` with:
  - `test_unbound_employee_resolution_carries_launch_inputs_and_bound_resolution_does_not`
  - `test_binding_cas_rejects_a_session_prepared_from_stale_configuration`
  - `test_first_unbound_session_configures_model_then_reasoning_before_binding`
  - `test_first_binding_loser_re_resolves_bound_winner_without_launch_inputs_or_reconfiguration`
  - `test_bound_load_replacement_and_new_conversation_do_not_reapply_kickoff_values`
  - `test_invalid_or_disappeared_selection_retires_child_without_binding_or_prompt`
  - `test_null_configuration_uses_native_state_without_set_requests`
- Update `web/tests/acp-components.test.mjs` (and component runtime coverage there) to
  assert no header configuration, exact Kickoff placement, conditional Reasoning,
  loading/error/Retry, stale-response suppression, null/default conversion, and
  complete control removal after freeze without presenting historical values as current.
- Add focused Playwright/scripted cases in `tests/e2e/test_acp_conversation.py`:
  - `test_kickoff_employee_configuration_survives_reload_then_freezes_on_approval`
  - `test_kickoff_catalog_loading_retry_and_stale_worker_response_states`
  - `test_opening_kickoff_configuration_creates_no_ticket_binding`
  - `test_browser_first_connection_uses_selected_launch_configuration_before_binding`
  - `test_first_automatic_prompt_uses_selected_model_then_reasoning`
  - `test_bound_session_reload_does_not_reapply_historical_kickoff_values`
  - `test_disappeared_selection_creates_no_binding_and_sends_no_prompt`

## Focused gates and handoff

Run the named Python unit files, `npm --prefix web run check`,
`npm --prefix web test`, and the `test_acp_conversation.py -k
employee_configuration` Playwright/scripted subset. MR-01 reserves the repository-wide
`./verify` for the final integrated MR-02/MR-03/MR-04 tree as stated in
`orchestration/acp-model-selection/tickets.md`. Before handoff, review the combined
diff against the frozen contract, especially the two binding-race orders and every
first-binding entry point, plus the negative proof that bound-session lifecycle paths
never reapply historical Kickoff values; no unresolved contract violation may remain.
