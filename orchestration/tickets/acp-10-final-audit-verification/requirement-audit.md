# ACP-10 requirement audit

Audit snapshot: **2026-07-21, before the ACP-10 final review and canonical verifier**.

This ledger follows the precedence actually in force: live owner decisions in `decisions.md`, then
the amended ACP program and ticket contracts, then the original implementer brief. A later owner
decision can therefore make an older requirement `owner-overridden`; it does not silently delete the
older requirement from this audit.

Verdicts are the four values frozen by the ACP-10 contract:

- `proved` — current source plus a named test, focused result, or actual runtime record proves the
  invariant;
- `owner-overridden` — a later owner decision explicitly replaced the cited older requirement;
- `not applicable` — the requirement's condition does not exist in the settled design;
- `unproved` — the required final evidence does not yet exist. Every such row blocks ACP-10.

Focused evidence is useful only for the invariant it exercises. It is not presented as the final
repository-wide completeness claim. Historical source and test names under the deleted legacy
system are cited only where the v25 migration/absence contract deliberately seals them.

## Owner amendments and superseded clauses

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| OVR-01 | Implementer brief, “Compaction signifier” and “The bar”; early ACP-02/05 summary clauses | Compaction should expose an expandable, readable summary. | Superseded by `D-compaction-is-an-opaque-lifecycle`, `D-compaction-contract-follows-backend-lifecycle-signals`, and the amendment at the top of `orchestration/acp-migration/plan.md`: context is backend-private and absent from Python, wire, persistence, and UI. | owner-overridden |
| OVR-02 | Implementer brief, Goal/Backends; original ACP-09 plan | Deliver Hermes, Codex, Claude Code, and Gemini. | `D-acp-gemini-out-of-scope`; amended program ACP-09; current product catalog is exactly `hermes, codex, claude` in `src/planner/conversation/backend_catalog.py`; `tests/unit/test_worker_type_registry.py::test_production_employee_backend_catalog_is_ordered_hermes_codex_claude`. | owner-overridden |
| OVR-03 | `D-codex-acp-1-1-4-remains-unregistered` and early fork/summary qualification clauses | Keep Codex 1.1.4 unregistered because it has no Hermes-style fork/summary and replays a stored plan as prose. | Superseded by `D-compaction-contract-follows-backend-lifecycle-signals` and `D-codex-stored-plan-prose-is-a-pinned-presentation-limit`; amended ACP-07/10 contracts require functional Codex with the presentation limit pinned, not a parser or shim. | owner-overridden |
| OVR-04 | Early 10-second service-shutdown reuse and owner discussion of a 60-second compaction budget | Treat a short timeout as compaction failure. | `D-compaction-has-an-emergency-deadlock-breaker-and-exact-failure`; `src/planner/conversation/configuration.py` sets `ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS = 300`; `tests/unit/test_conversation_turn_broker.py::test_production_compaction_capture_budget_is_distinct_from_shutdown`. | owner-overridden |
| OVR-05 | `D-acp-null-load-is-a-cutover-transition`; cancelled `acp-05-missing-session-cutover/contract.md` | Permanently remint a missing legacy `planner-chat` session through the ACP runtime. | `D-acp-cutover-does-not-carry-legacy-session-compatibility`; the cancelled contract says no source/test change; schema v25 deletes old bindings and fresh ACP conversations are the one-way cutover. | owner-overridden |
| OVR-06 | Original same-child clauses in `acp-05-durable-compaction-fork/contract.md` | Hermes should fork and privately load/publish the successor on the same child generation. | The contract's own supersession notice, `D-acp-compaction-handoff-is-session-aware-and-cross-process`, and `acp-05-compaction-private-load-hang/contract.md` require source-child fork plus fresh unpublished-child load/publication. | owner-overridden |
| OVR-07 | Any inference that acp-ui should supply Panels' visual design, or that ACP warrants a broad redesign | Preserve the existing restrained Panels split rail, tokens, typography, spacing, and cardless messages; acp-ui is behavior-only and Zed is the legibility bar. | `D-acp-reference-design-boundary`; `acp-03-browser-state-pane/contract.md`; `web/src/components/acp/**`; `web/tests/acp-browser-components.test.mjs`; current `PROGRESS.md` records real `8767` layout confirmation after the v25 cutover. | owner-overridden |
| OVR-08 | Original brief/process references requiring Codex CLI review and a broad multi-round pipeline | Independent sub-agents may review; one focused round is normal, with a narrow correction check only for a concrete unresolved finding. | `D-acp-ticket-reviews-use-subagents`, `D-observed-small-ui-fixes-use-spot-check-and-dogfood`, current AGENTS instructions, and the READY ticket review artifacts. | owner-overridden |
| OVR-09 | `D-acp-verify-at-checked-in-relay-default` | Run verification at the old relay-off default and then restore a live relay toggle. | ACP-06 removed the relay, its config toggle, and its startup path. ACP-10 verifies the sole ACP tree, so there is no relay default to restore. | not applicable |

## Architecture, protocol, contracts, and ownership

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| ARCH-01 | Brief Architecture; program Outcome/System shape; ACP-00/01 | Panels server is the ACP v1 client and uses the official Python SDK over stdio; browser and model never speak directly. | `agent-client-protocol==0.11.0` in `pyproject.toml` and `requirements.txt`; `src/planner/conversation/sdk_child.py` uses `acp.stdio.spawn_agent_process` and `PROTOCOL_VERSION`; `tests/unit/test_acp_employee_child.py::test_official_sdk_child_delegates_and_load_waits_for_sink_consumption`; ACP-00/01 READY reviews. | proved |
| ARCH-02 | Brief Architecture; program Wire contracts; ACP-00/03/04 | Browser speaks one thin Panels WebSocket with typed ACP session updates plus only Panels routing/lifecycle envelopes; no browser JSON-RPC or parallel event vocabulary. | Closed unions in `src/planner/conversation/wire_contracts.py` and `web/src/lib/acp/contracts.ts`; `/api/conversation` in `src/planner/core/server.py`; strict transport in `web/src/lib/acp/panelsTransport.ts`; `tests/unit/test_conversation_contracts.py`; `web/tests/acp-contracts.test.mjs`. | proved |
| ARCH-03 | Brief “What to adopt”; program ACP-00 | Pin ACP SDK/types and vendor only the framework-free donor core with license/provenance; no stale npm core package or React. | Python 0.11.0 and TS SDK 1.2.1 pins; donor revision `525a9d83c5ace577ac0417bf82bf983da4042663` in `web/src/vendor/acp-components-core/UPSTREAM.md`; `tests/unit/test_conversation_contracts.py::test_python_and_browser_dependency_pins_and_forbidden_absences` and `::test_vendor_provenance_hashes_license_and_closed_import_graph`. | proved |
| ARCH-04 | Program Ordered typed replay; ACP-00 probes 1/9/10; ACP-01 | SDK observer validates/reserves only; one bounded consumer per employee/session drains in wire order, never silently drops invalid replay, and emits typed rejection rather than text fallback. | `src/planner/conversation/ordered_ingress.py`; `tests/unit/test_acp_employee_child.py::test_ordered_ingress_reserves_raw_order_and_uses_typed_payloads`, `::test_invalid_observer_frame_consumes_visible_rejection_without_typed_callback`, overflow/retirement tests; ACP-00 probe harness. | proved |
| ARCH-05 | Program/ACP-01/04 durable identity | One durable complete binding owns employee, entity, ACP session, backend, and positive binding generation; process child generation is separate; repository CAS commits before publication and mirrors a Ticket atomically. | `ConversationSessionBinding` in `contracts.py`; `SqliteConversationBindingRepository`; `tests/unit/test_acp_binding_repository.py::test_binding_cas_updates_ticket_mirror_atomically`; registry crash/reload/CAS tests; ACP-01/04 READY reviews. | proved |
| ARCH-06 | Program replay/reconnect; ACP-01/03/04 | `session/load` is ready only after typed replay is consumed; refresh/reconnect keeps the same binding; reset/replay/ready is ordered; gaps, wrong identity, stale generation, partial update, and reset overflow fail closed. | `sdk_child.py`, `hub.py`, `conversationController.ts`; load/barrier and restart tests in `test_acp_employee_child.py`, `test_conversation_hub.py`, `web/tests/acp-browser-state.test.mjs`; `tests/e2e/test_acp_conversation.py::test_fresh_uvicorn_process_resumes_durable_binding`. | proved |
| ARCH-07 | Program Product/runtime boundary; ACP-04/06 | One production `ConversationComposition`, hub, registry, broker, permission owner, step gateway, and route own conversation work; conversation events do not write canonical resource invalidations or legacy transcript rows. | `src/planner/conversation/composition.py`; `src/planner/core/server.py`; `tests/unit/test_acp_conversation_composition.py`; `tests/unit/test_conversation_turn_broker.py::test_generic_runtime_has_no_backend_name_or_canonical_product_writer_branch`; ACP-06 READY review. | proved |
| ARCH-08 | ACP-04 route/mount contract | Chief Workspace, Chief route, and Ticket route mount the same production ACP wrapper; one socket is attach-first and permanently employee-scoped; actions are the exact frozen five. | `web/src/components/AcpConversation.svelte`; three route imports/mounts; `src/planner/conversation/hub.py`; `web/tests/acp-production-mount.test.mjs`; `tests/unit/test_acp_conversation_websocket.py`. | proved |
| ARCH-09 | ACP-01/02 lifecycle contracts | Runtime shutdown, partial initialization, child death, slow consumer, sink/publication failure, and ingress retirement close exact owners under one caller deadline and cannot leave older writers or waiters alive. | Registry, child, hub, broker, permission, and terminal hard-deadline tests named in their focused suites; ACP-01/02 correction reviews both `READY`. | proved |
| ARCH-10 | Brief “Skills stay agent-side; commands ride the protocol” | ACP adds no skills primitive or browser-side skill loader. Each provider receives its worker role/config through its own supported process/session seam, while slash commands remain typed session state. | Hermes provisioning in `backend_catalog.py`/`hermes_backend_configuration.py`; Claude's preset system-prompt append in `claude_backend.py`; normal worker prompt construction plus real Codex/Claude Automatic Employee results in `PROGRESS.md`; command proof in UI-05. | proved |

## Browser transcript and interaction

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| UI-01 | Brief “always tell what agent is doing”; program Outcome; ACP-03 | One persistent textual status represents connecting/loading/idle/thinking/working/compacting/waiting/interrupted/failed, including visible connection/protocol errors. | `ConversationActivityState`; `ConversationStatus.svelte` has one `role="status"`; mounted runtime coverage in `tests/support/acp_component_runtime.py`; ACP-03 READY correction review. | proved |
| UI-02 | Motivating bug; ACP-00 probe 2; ACP-03 | Live and loaded `agent_thought_chunk` stays a distinct thought part, closed by default and absent from assistant message text. | `conversationState.ts` thought branch; `ThoughtView.svelte`; `test_acp_conformance_harness.py`; `web/tests/acp-browser-conformance.test.mjs`; ACP-03 review spot-check. | proved |
| UI-03 | ACP-00 probe 3; ACP-03; `D-acp-missing-message-ids-close-on-terminal-settlement` | Message IDs group exactly; missing-ID fallback is deterministic and closes at typed role/tool/plan/terminal or interrupted boundaries, including remote human echo. | `conversationState.ts`; `web/tests/acp-browser-state.test.mjs`; `D-send-now-recovery-replays-its-captured-human-boundary`; exact recovery order tests in hub/broker/browser suites. | proved |
| UI-04 | Brief typed viewer; ACP-00 probe 4; ACP-03 | Tool calls reconcile by ID; typed tool content, real line diff, terminal state, full plan replacement, usage, modes/config/session metadata, and unsupported content remain typed and legible. | `ToolCallCard.svelte`, `DiffView.svelte`, `PlanView.svelte`, `conversationState.ts`; ACP browser state/component/conformance tests; ACP-00a terminal wire tests. | proved |
| UI-05 | Program Browser contracts; ACP-03/04/06 | Commands come only from typed `available_commands_update`; Stop and New conversation remain lifecycle controls; no legacy commands/skills endpoint or hard-coded backend command list. | `conversationState.ts`, `ConversationComposer.svelte`; `web/tests/acp-contracts.test.mjs`; `web/tests/resource-catalogue.test.mjs`; ACP-06 READY review. | proved |
| UI-06 | Brief image affordance; ACP-04/06 | Ordered images are ACP prompt content blocks and replay/render as typed content; rejected admission retains draft/images; no legacy chat upload path. | `AcpComposer.svelte`, `ConversationComposer.svelte`, `pendingConversationImages.js`; `web/tests/acp-images.test.mjs`; ACP-06 correction review finding 4 resolved. | proved |
| UI-07 | Brief permission bar; ACP-03 and permission-diff correction | Permission prompt is prominent, exact, user-driven, preserves option order/kind/ID, displays supplied edit diffs through the existing diff view, and does not become a Ticket proposal. | `PermissionPrompt.svelte`; permission browser tests; `acp-05-permission-diff/implementation-review.md` READY; backend first-settlement tests under PERM-01/02. | proved |
| UI-08 | Brief steer-vs-queue; program/ACP-03 | Mid-turn controls expose Steer, Send Now, and Queue; Steer uses explicit `supportsSteer`; queued prompts remain visible/cancellable in FIFO order; receipts show accepted/queued/started/interrupted/rejected. | `AcpComposer.svelte`; `conversationController.ts`; ACP browser state/component tests; required strict capability wire tests in ACP-00b. | proved |
| UI-09 | Current compaction contract; ACP-03/05 | Public compaction is one stable, non-interactive content-free lifecycle row: compacting, compacted, or failed with exact reason; it never exposes a summary/disclosure. | `ContextCompaction` has no summary field; `conversationState.ts` and `TranscriptView.svelte`; contract/browser tests; actual Hermes/Codex/Claude records in current `PROGRESS.md`. | proved |
| UI-10 | ACP-03 visual contract; owner visual override | Ordinary messages are cardless; thought/completed tools are compact disclosures; only permission is a blocking inset; only existing Panels tokens are used; no acp-ui/React/card-grid redesign. | `web/src/components/acp/**`; component source-boundary/runtime tests; ACP-03 READY review; real v25 cutover observation in `PROGRESS.md`. The final consolidated screenshot record is separately `CLOSE-03`. | proved |
| UI-11 | ACP-03 component contract | Disclosures use native accessible controls and closed defaults; permission options retain exact order and focus behavior; transcript autoscrolls only while the reader is following; focus-visible/reduced-motion conventions and immutable snapshots prevent UI mutation of reducer state. | ACP Svelte components, `conversationController.ts`, mounted component runtime, ACP browser component/state tests, and ACP-03 READY correction review. | proved |

## Turn, cancellation, compaction, and recovery

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| TURN-01 | Program Turn delivery; ACP-02 | `normal` starts only when idle; `queue` only while active; `send_now` cancels/settles before successor; `steer` only with a real declared strategy and never silently falls back. | `src/planner/conversation/turn_broker.py`; choice/order/rejection tests in `test_conversation_turn_broker.py`; Hermes strategy tests; ACP-00 probe 7. | proved |
| TURN-02 | ACP-02; `D-acp-delivery-captures-the-submitted-record` | Broker is the sole active/FIFO writer; duplicate IDs reject; accepted work keeps its exact acquired runtime; FIFO positions and delivery are exact-once. | `test_conversation_turn_broker.py::test_fifo_publishes_exact_positions_and_delivers_each_prompt_once`, `::test_duplicate_and_wrong_session_are_rejected_without_delivery`, `::test_accepted_generation_n_delivery_never_redirects_to_concurrent_n_plus_one`. | proved |
| TURN-03 | `D-acp-cancellation-cause-freezes-successor`; cancel-classification correction | Cancellation cause and successor policy are frozen before ACP cancel; ordinary prompt exceptions remain generation failure; a successful requested cancellation followed by exceptional unwind is interruption/recovery. | Turn-broker cancel/classification tests; `acp-05-cancel-classification/implementation-review.md`; ACP-02 READY correction review. | proved |
| TURN-04 | Requested-cancel contract | Stop/Send Now exceptional unwind quarantines late old output, retires the exact child, loads the unchanged durable session into a fresh child generation, publishes same-binding reset/replay/ready, then settles/starts once. | Registry/hub/broker recovery tests; `tests/e2e/test_acp_conversation.py::test_official_requested_cancel_exception_recovers_same_session_for_stop_and_send_now`; requested-cancel correction review. | proved |
| TURN-05 | Send Now human-boundary contract | Recovery re-emits reset-erased FIFO and Send Now human echoes before successor start so old agent/user/new agent remain distinct even without message IDs. | Hub/broker exact-order tests and browser reducer regression; `acp-05-send-now-human-boundary/implementation-review.md` READY. | proved |
| TURN-06 | ACP-02 child-death rule; new-conversation/shutdown contracts | Child death rejects the active turn and FIFO once; stale/planned death cannot affect a replacement; New conversation rejects old intent and persists one successor; shutdown advances no FIFO. | Child-death/new-conversation tests in broker and registry; `test_acp_employee_registry.py::test_late_old_generation_death_cannot_retire_replacement`; shutdown tests. | proved |
| TURN-07 | Hermes compaction corrections | Hermes explicit/automatic compaction uses one actor-owned 300-second transaction: exact source fork, session-aware async capture on a fresh unpublished child, private typed load, CAS N→N+1, winner publication, old-source retirement, reset/replay/ready, and exact-once queue retarget. | `sdk_child.py`, `employee_registry.py`, `hub.py`, `hermes_turn_strategy.py`; official-fork e2e tests; registry/broker/hub suites; ACP-05 correction reviews. | proved |
| TURN-08 | `D-acp07-provider-compaction-is-in-place`; ACP-07/08 | Codex and Claude normalize their real same-session lifecycle through the generic optional in-place hook; no fork, CAS, generation/reset, summary, or backend-name branch is added. | `runtime_ports.py`; `codex_turn_strategy.py`; `claude_turn_strategy.py`; provider strategy tests and activation READY review. | proved |
| TURN-09 | Five-minute breaker/current compaction contract | One absolute 300-second budget is only an extreme deadlock breaker. Concrete backend/protocol/persistence/publication errors fail immediately with exact phase/type/message; backend `TimeoutError` is not relabelled as Panels expiry; no hidden retry/sub-budget exists. | `configuration.py`; deadline/failure tests in registry and turn broker; `acp-05-compaction-capture-deadline/focused-checks.txt`; READY review. | proved |
| TURN-10 | Compaction replay privacy/provenance | Replay is classified exactly once by the selected provider strategy; private context is suppressed, not rendered or copied to SQLite; only ordered boundary ID/trigger provenance is durable where Hermes needs it; malformed private content fails closed. | `hermes_turn_strategy.py`, `claude_turn_strategy.py`, `sqlite_binding_repository.py`, hub replay classifier; binding/strategy/browser tests; Claude exact-template regression. | proved |

## Permissions and reverse services

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| PERM-01 | Brief reverse calls; ACP-00/02 | Preserve exact ACP request/session/tool-call/backend identity and exact ordered option IDs, names, and kinds; first valid attached-browser response wins and later responses are already-settled. | Permission contracts/broker; `test_conversation_permission_broker.py::test_no_browser_cancels_without_visible_request_and_first_valid_browser_wins`; exact contract serialization tests. | proved |
| PERM-02 | ACP-02 attached-browser/timeout rules | No browser rejects immediately; one detach preserves a request while another remains; last detach, timeout (300 seconds), prompt cancel, child death, new conversation, shutdown, or actor tombstone settles exactly once. | `CONVERSATION_PERMISSION_RESPONSE_TIMEOUT_SECONDS = 300`; permission broker detach/timeout/tombstone/terminal-cause tests; ACP-00 probe 5. | proved |
| PERM-03 | `D-acp-permission-outcome-commits-before-activity`; `D-acp-permission-origin-is-admission-state` | Visible outcome publication commits the exact response before fallible activity restoration; stale worker/session permissions and late reverse callbacks cannot win. | Permission outcome/publication failure tests; `test_acp_step_gateway.py::test_worker_permission_guard_proves_binding_ticket_turn_and_active_epoch`; ACP-02 review correction. | proved |
| PERM-04 | ACP-02 filesystem contract | Filesystem callbacks are advertised only with the complete service; absolute normalized paths are descriptor-backed and confined against traversal, symlink, race, cross-session, and invalid UTF-8 cases; line/limit semantics are exact. | `reverse_services/filesystem.py` and `path_confinement.py`; complete filesystem suite including final-path-swap and official SDK bridge tests. | proved |
| PERM-05 | ACP-00a/02 terminal contract | Terminal calls use argv-not-shell, confined cwd/env, employee+session+terminal IDs, bounded UTF-8 output, typed snapshots/replay, exact release/kill cleanup, and capability advertisement only with a complete service. | `reverse_services/terminal.py`; `ConversationTerminalState`; terminal service/contract/browser tests; ACP-02 READY review. | proved |

## Automatic Employee and product/runtime boundary

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| WORK-01 | Program Product/runtime boundary; ACP-04/06 | Eligibility → Ticket claim → prompt → `AcpStepGateway` → existing selected child/session → proposal/status remains the one Automatic Employee path; human chat and automatic work share that durable ACP conversation. | `src/planner/runtime/employee_step_runner.py`, `acp_step_gateway.py`, production composition; `tests/e2e/test_acp_conversation.py::test_browser_and_worker_share_one_real_sdk_session` and fake non-Hermes continuity test. | proved |
| WORK-02 | ACP-04 callback/thread contract | Required binding exists before worker demand; `on_session_key` claim/CAS executes on the runner's caller thread before prompt; lost claim/callback failure sends no prompt and cannot settle a winner. | `AcpStepGateway`; callback/order/failure tests in `test_acp_step_gateway.py`; ACP-04 READY review. | proved |
| WORK-03 | Worker-context contract and current AGENTS boundary | `pending_worker_context` is prepared into the actual ACP prompt exactly once and acknowledged only after tracked prompt admission; transcript/event/correctness rows are never model context; acknowledgement failure retains at-least-once redelivery without resubmission. | `acp_step_gateway.py`; `test_acp_step_gateway.py` prepare/admit/ack tests; `tests/e2e/test_acp_conversation.py::test_automatic_worker_delivers_pending_context_through_official_sdk_once`. | proved |
| WORK-04 | ACP-04/06 settlement contract | `employee_step_runs` is correctness state only; complete/interrupted/errored/busy/death/lost-claim/restart/shutdown settle the exact row and Ticket once, reuse required existing session, and store no transcript/prompt/reply. | `employee_step_repository.py`, `employee_step_runner.py`; employee-step repository/runner tests; ACP-06 READY review and v25 live record count in current `PROGRESS.md`. | proved |
| WORK-05 | ACP-04 collector boundary | Worker `RunResult` contains only typed agent-message text from the exact prompt epoch; thought/tool/plan/terminal/permission content is not flattened; interrupt validates current binding and cannot cancel a successor. | `acp_step_gateway.py`; terminal mapping, interrupt, late-interrupt, observational-status tests in `test_acp_step_gateway.py`. | proved |
| WORK-06 | Functional-backend owner decision | Hermes, Codex, and Claude real Automatic Employee runs use the same session as human Ticket chat and produce normal worker-authored proposal/status; chat remains usable afterward. | Current `PROGRESS.md` records Hermes proof and exact Codex Ticket `t_nkq3108b` / Claude Ticket `t_1xbdpkq0`, unchanged generation-1 bindings, matching completed step-run session IDs, Success proposals, and later/restart replies. The consolidated final matrix is still `CLOSE-03`. | proved |

## Backend catalog, Ticket selection, and exact providers

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| SEL-01 | `D-employee-backend-and-worker-registry-are-one-configured-pair`; ACP-07 | One immutable configured pair owns the ordered backend catalog and Worker-type registry; startup, manifests, Ticket writers, bindings, discovery, chat, and runner consume that exact authority. | `src/planner/worker_types/configuration.py`; composition/catalog source; pair-divergence test and ACP-07 selector READY review. | proved |
| SEL-02 | ACP-07/08 activation | Production registration order is exactly `hermes, codex, claude`; Chief and all shipped Worker defaults remain Hermes; catalog availability is live and materialized keys cannot drift. | `PRODUCTION_EMPLOYEE_BACKEND_REGISTRATIONS`; worker registry/catalog/composition tests; activation READY review. | proved |
| SEL-03 | Owner default/override decision; ACP-07 | Every Worker profile has a required registered default; Ticket creation stores the explicit override or exact Worker default; backend is distinct from model/reasoning/skill. | Worker/Ticket contracts and data writers; registry/manifest/create ingress tests; schema v26 tests. | proved |
| SEL-04 | `D-pristine-ticket-observation-does-not-bind`; ACP-07 | Fresh awaiting-approval `needs_kickoff` shows a small preselected generic backend control but opens no binding; first prompt attaches once; Kickoff advance enables ordinary eager attach. | `TicketRoute.svelte`; deferred controller; e2e selector/first-prompt/Kickoff-advance tests; ACP-07 READY review. | proved |
| SEL-05 | Kickoff freeze/binding equality contract | Canonical writer allows only pristine Kickoff with no session/binding; first demand or approval freezes selection; binding CAS requires registered key and exact Ticket selection; human and automatic paths use it. | `tickets/data.py::write_employee_backend`; `sqlite_binding_repository.py`; API race/freeze tests; binding tests; fake non-Hermes e2e. | proved |
| SEL-06 | Hermes baseline | Hermes remains registered through its read-only adapter definition, declares real capabilities including native Steer, and has real human + Automatic Employee + compaction/reload/restart continuity evidence. | `hermes_backend.py`; Hermes definition/strategy tests; actual ACP-05 and ACP-06 runtime records in `PROGRESS.md`. | proved |
| SEL-06A | ACP-05 and ACP-10 Hermes live permission matrix | Actual Computer Use should exercise permission allow and reject/each supplied option in the real app, not relabel a fixture as that live action. | `computer-use-evidence.md` records the original real diff/Allow and the later Safari native edit card on Ticket `t_x2f5up6e`: selecting its supplied `Deny` option settled once, Hermes reported permission denied, and `data/acp-dogfood-diff.txt` remained `alpha` / `delta`. | proved |
| SEL-07 | ACP-07 Codex exact contract | Codex uses locked `@agentclientprotocol/codex-acp@1.1.4`/Codex 0.144.6, absolute Node+entrypoint, confined env, ACP v1 identity/load, reverse `false,false,true`, no Steer, typed live/replay except pinned stored-plan prose, same-session compaction, and lazy version-only availability probe. | `agent_backends/package*.json`; `codex_backend.py`, `codex_turn_strategy.py`; Codex definition/conformance/strategy tests; activation READY review. | proved |
| SEL-08 | Owner functional-worker minimum; ACP-07/10 Codex continuity | Ordinary Kickoff selects/freezes Codex; real human → naturally discovered automatic → human continuity uses one session, with typed thought/tool, permission, content-free compaction, hard reload, and server restart. | Current `PROGRESS.md` and `computer-use-evidence.md` record Ticket `t_nkq3108b`, session `019f81a8-7041-7bb3-b7da-4445912fc3d0`, generation 1, matching step run, Success proposal, `CODEX AFTER AUTO OK`, and Safari `CODEX RESTART OK`. | proved |
| SEL-08A | ACP-07 required proof 4; ACP-10 Codex matrix | Actual Codex Computer Use must exercise exact permission allow **and reject**, Queue, Send Now, unavailable Steer, and observe/accept the stored-plan presentation limitation. | `computer-use-evidence.md` records real Allow Once plus the ordered local-curl options and selected Reject; disabled Steer; a queued successor with pending Cancel and exact FIFO auto-start; a clean interrupted Send Now successor with no forbidden predecessor completion; and a live typed plan whose typed presentation was absent after refresh/exact child recovery while the unchanged binding answered. Panels added no prose parser or invented plan. | proved |
| SEL-09 | ACP-08 exact Claude contract | Claude uses locked `@agentclientprotocol/claude-agent-acp@0.60.0`, absolute Node+entrypoint, confined env and worker-role append, reverse `false,false,true`, no Steer, typed internal Bash, same-session lifecycle, and one initialize-only preflight completed before admission. | `agent_backends/package*.json`; `claude_backend.py`, `claude_turn_strategy.py`; Claude definition/preflight/strategy tests; server startup-order tests; activation READY review. | proved |
| SEL-10 | ACP-08 functional Claude and replay-privacy proof | Ordinary Kickoff selects/freezes Claude; real human → automatic → human, typed internal Bash/tool state, compaction/reload/restart remain on one binding; exact provider-private `isCompactSummary` replay is replaced provider-locally by content-free lifecycle while ordinary user rows remain untouched. | Current `PROGRESS.md` records Ticket `t_1xbdpkq0`, session `e2562875-0b3f-482e-8f75-52e0d0e46731`, generation 1, matching step run, Success proposal, `CLAUDE AFTER AUTO OK`, and `CLAUDE RESTART OK`; `test_claude_replay_replaces_private_compaction_summary_with_lifecycle`. | proved |
| SEL-10A | ACP-10 Claude matrix | Actual Claude Computer Use must show unavailable Steer, Queue/Send Now, and a real permission interaction in addition to Bash, continuity, compaction, reload, and restart. | `computer-use-evidence.md` records disabled Steer, a real queued successor and FIFO auto-start, the initial late-background-output Send Now failure and passing fresh-child retest, and a real native `ExitPlanMode` permission card with five provider options settled through `Yes, and use "auto" mode`; ordinary Bash remains truthfully unprompted under `auto`. | proved |
| SEL-10B | `D-claude-requested-cancel-requires-fresh-child`; ACP-10 correction ticket | Because pinned Claude may emit an autonomous old-turn answer after a terminal requested-cancel response, capability-true Stop/Send Now must keep the old source quarantined and use existing same-binding fresh-child recovery; other backends retain normal generation-N reuse. | `BackendTurnCapabilities.requires_fresh_child_after_requested_cancel`; Claude true, Hermes/Codex false definition tests; focused broker Stop/Send Now/false-control regressions; `acp-10-claude-requested-cancel-isolation` report/checks; live PID `2477` -> `7324` retest on unchanged session/binding with exact successor and no forbidden old completion after the prior late interval. | proved |
| SEL-11 | Owner Gemini removal | Gemini is absent from registration, selector, product source, delivery tests, dependencies/config, and current-state docs. Historical research and installed dependency READMEs are not product delivery. | Product search finds no Gemini in `src`, `tests`, `web/src`, `web/tests`, `config.yaml`, or project manifests; current docs explicitly say it is not registered. Final captured command output remains `CLOSE-05`. | proved |

## Schema cutovers, sole production path, and documentation

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| MIG-01 | ACP-06 schema-v25 contract | In one rollback-safe v25 transaction: validate/add compaction provenance, create/index `employee_step_runs`, copy only worker-step correctness with exact mapping, rewrite only matching start events, release stranded Tickets, delete bindings, remove legacy tables/Day column/events, set version 25. | `src/planner/core/db.py::_migrate_to_v25`; `test_db.py::test_v25_cutover_converts_worker_correctness_and_deletes_conversation_state`, rollback and invalid-provenance tests; ACP-06 READY review. | proved |
| MIG-02 | ACP-06 Employee-step rehome | `SqliteEmployeeStepRepository` is the sole live SQL owner; immutable eight-field records and first-wins settlement replace Chat turns without changing eligibility/runner correctness. | `employee_step_repository.py`; repository/runner/eligibility tests; ACP-06 review. | proved |
| MIG-03 | ACP-06 deletion | Delete live `planner.chat`, `planner.minds`, `planner.hermes_backend`, old adapters/routes/panes/resources/config/dependencies/tests and managed Chat-file surface; keep `/api/conversation`, worker-self, ACP typed images/previews, worker context, and product correctness state. | Tracked source deletions; `tests/unit/test_chat_ingress_contract.py`; `web/tests/acp-production-mount.test.mjs`, `resource-catalogue.test.mjs`, `acp-images.test.mjs`; rebuilt-bundle closure in ACP-06 READY review. | proved |
| MIG-04 | ACP-06 sealed recognizer exception | Legacy Chat vocabulary may remain only in the v25 migration/parser and its migration fixtures; it is not a live import, route, writer, table, or served bundle surface. | `src/planner/core/db.py` v25 recognizer; `tests/unit/test_db.py` migration fixtures; live absence tests. The final complete search transcript must list this exception (`CLOSE-05`). | proved |
| MIG-05 | One-way cutover decision | No compatibility mode, old-session import, legacy fallback, remint-on-null, parallel route, or Hermes source patch remains; fresh ACP generation-1 bindings replace the old state. | `D-acp06-one-way-deletion-is-authorized`; v25 deletes bindings; current source has one route/composition; real v25 cutover record in `PROGRESS.md`. | proved |
| MIG-06 | ACP-07 schema-v26 contract | Add required no-default `tickets.employee_backend`; pre-v26 rows become exact Hermes only after contradiction checks; rebuild is rollback-safe/idempotent and preserves bytes; valid v26 non-Hermes selection is preserved. | `db.py::_migrate_to_v26`; v26 fresh/migrate/contradiction/reopen/corruption tests in `tests/unit/test_db.py`; ACP-07 review. | proved |
| MIG-07 | Brief “Don't touch”; all ACP contracts | Hermes checkout remains read-only; Panels uses official ACP adapter/fork behavior and owns all corrections locally, with no required Hermes patch or private DB parser. | Current source calls the external executable through SDK only; compaction/private replay corrections live in `src/planner/conversation`; `PROGRESS.md` records the read-only boundary throughout. No Hermes-checkout command or write is part of any implementation report. | proved |
| MIG-08 | Program/ACP-06/10 docs contract | Live docs and root architecture maps must describe only the current ACP system, exact providers, default/override/freeze behavior, install/auth/capability limits, automatic-session sharing, and opaque compaction, with no stale legacy alternative. | Current `docs/README.md`, `docs/chat.md`, `docs/employee-runtime.md`, `docs/worker-types.md`, `docs/systems.md/.html`, `docs/cli.md`, `AGENTS.md`, and `CLAUDE.md`; complete live-doc/root-doc searches and classifications in `legacy-absence-evidence.md`; historical `PLAN_HERMES_BIN` wording is explicitly sealed in `DOGFOOD.md`. | proved |

## Process, experiential proof, and ACP-10 closeout

| ID | Source | Exact requirement | Authoritative source, test, or evidence | Verdict |
|---|---|---|---|---|
| CLOSE-01 | Brief “How to work”; program research | Study Zed/acp-ui/ACP references by actual computer use and source inspection before setting the interaction bar. | Current `PROGRESS.md` records Zed 1.11.3 and acp-ui 0.1.16 hands-on observations; `orchestration/acp-migration/research.md` and `direction.md`. | proved |
| CLOSE-02 | Ticket operating model; owner review overrides | Each substantive slice received contract-scoped planning/implementation and one independent focused review, with bounded correction checks only for concrete findings; no unresolved ticket finding remains before ACP-10. | READY reviews in ACP-00 through ACP-08 directories, especially ACP-06, ACP-07 selector, and combined ACP-07/08 activation. Final settled-tree review is a separate gate (`CLOSE-06`). | proved |
| CLOSE-03 | ACP-10 Real Computer Use matrix | Record one consolidated actual Computer Use matrix against only production-served `http://127.0.0.1:8767` (Safari is acceptable as the owner's live override), including restrained Chief/Ticket layout and the required Hermes/Codex/Claude controls, replay, permission, queue/send/cancel, compaction, recovery, and human→automatic→human observations; fixtures must be labelled as fixtures. | `computer-use-evidence.md` now consolidates the restrained production UI, typed replay/recovery, all three same-Ticket human→natural-automatic→human continuities, Hermes allow/reject, Codex and Claude provider-specific control matrices, exact screenshots, compaction/restart results, and the real Claude late-output failure/correction/retest. Its fixture section labels only deterministic race complements as fixtures. | proved |
| CLOSE-04 | ACP-10 Exact backend truth | Create `backend-qualification.md` with exact executable/package/hash/version, advertised/declared capabilities, conformance, registration/selector state, and real worker proof for Hermes/Codex/Claude; record Gemini absence and Codex's pinned plan limitation. | `backend-qualification.md` records installed versions/hashes/integrities, declared and advertised capabilities, catalog/selector state, focused conformance, read-only DB corroboration, exact real workers, Codex limitation, and Gemini absence; final disposition PASS. | proved |
| CLOSE-05 | ACP-10 legacy/documentation proof | Create `legacy-absence-evidence.md` with complete commands/output across source/tests/config/live docs/root docs/new `web/dist`, explicit exclusions and sealed migration recognizer, plus retained route/worker-self/step/binding proofs. | `legacy-absence-evidence.md` records commands, complete outputs/statuses, negative-test and live-doc classifications, sealed v25/old-ticket recognizers, current `web/dist`, retained ACP owners, schema-v26 read-only DB inspection, and PASS disposition. | proved |
| CLOSE-06 | ACP-10 independent review | One fresh independent sub-agent reviews the settled tree, this ledger, CU evidence, qualification, legacy proof, docs, ordered ingress, binding/CAS, cancel/FIFO, permission, compaction, v25/v26 migration, configured pair, and step gateway; every finding is disposed with no unresolved violation. | `independent-review.md` reports `READY` with zero unresolved P0/P1 findings after inspecting every named seam; `review-disposition.md` accepts it in full with no correction or second broad round. | proved |
| CLOSE-07 | ACP-10 start/freeze gate | No implementation agent or process may still write product, tests, docs, config, or generated assets; stop live server/employee/build work and record exact status plus sorted verified-input manifest before the canonical run. | Attempt 1's freeze and unchanged manifest remain retained. After its bounded failure correction, `freeze-report-attempt-2.md` records the fresh zero-writer/process gate and exact exclusions; `freeze-git-status-attempt-2.txt` retains the complete corrected status; `verified-input-manifest-attempt-2.sha256` is the final sorted content/absence manifest. | proved |
| CLOSE-08 | ACP-10 sole canonical verification | On the frozen no-writer tree run `./verify` exactly once with pipe failure preserved; retain full stdout/stderr at `data/verify/acp-10-final.log`, exit zero, final `VERIFY: PASS`, line/byte count, timing, suite counts, SHA-256, and an unchanged post-run input manifest. A failure must be retained and not hidden by immediate retry. | `verification-report.md` retains attempt 1 at `data/verify/acp-10-final-attempt-1.log` (exit 1 / `VERIFY: FAIL`, digest `daa64377...541bf`). After bounded correction/focused proof/fresh freeze, the final 1,162-input snapshot received one clean run: exit 0, 157 lines/9,051 bytes, Ruff/Mypy/build/frontend green, 1,019 unit + 103 e2e passed, final `VERIFY: PASS`, log SHA-256 `d66e7bfd939d9622ac8673b6cba00937f1124c0b3a95d0d6a37bcda4513a8963`, and unchanged manifest SHA-256 `d7265946efa9bac6a5cee8130ba020dd4827dba8b25a1963491e58f6cd67c611`. | proved |
| CLOSE-09 | ACP-10 memory/result closure | After success, change only the permitted evidence/result lines; update `PROGRESS.md` and `decisions.md` with exact final evidence, zero unproved requirements, zero unresolved findings, backend tuple, CU result, legacy/docs result, log path/digest/status, and reconfirm the manifest. | `verification-report.md`, this ledger, `PROGRESS.md`, and `decisions.md` record zero unproved requirements/findings, exact catalog `hermes, codex, claude`, PASS qualification/CU/legacy/docs, the retained failed attempt, the clean final log path/digest/marker, unchanged final manifest, and the post-verify Safari frame. Only contract-authorized result files changed after the clean gate; Safari/server activity wrote ignored runtime data only. | proved |
| CLOSE-10 | ACP-10 completion artifact set | Retain this ledger, CU evidence, backend qualification, legacy absence, independent review/disposition, verification report/full log/digest, and current docs/memory. ACP-10 adds no feature, shim, compatibility path, redesign, Gemini work, or Hermes write; a material product correction invalidates and refreshes the affected audit snapshot. | The full artifact set exists: ledger, CU evidence, qualification, legacy proof, independent review/disposition, both freeze snapshots, failed-attempt and clean-final logs/digests, final verification report, and current docs/memory. Delivery remains exactly the owner-approved ACP migration: no Gemini, compatibility path, ACP redesign, compaction disclosure, provider fork, or Hermes-checkout write. | proved |

## Frozen ticket-contract coverage

This table makes the grouping explicit. It is not a substitute for the requirements above: each
contract points to the stable IDs that carry all of its current normative invariants. Superseded or
cancelled clauses point to the corresponding override row rather than disappearing.

| Frozen contract | Stable requirement IDs |
|---|---|
| ACP-00 contracts/conformance | ARCH-01–04, ARCH-06, UI-02–05, TURN-01, PERM-01, CLOSE-02 |
| ACP-00a terminal wire | UI-04, PERM-05 |
| ACP-00b Steer capability wire | UI-08, TURN-01 |
| ACP-01 child runtime/Hermes | ARCH-01, ARCH-04–06, ARCH-09, SEL-06, MIG-07 |
| ACP-02 broker/reverse services | TURN-01–03, TURN-06–10, PERM-01–05, ARCH-09 |
| ACP-03 browser state/pane | ARCH-02–03, ARCH-06, UI-01–11, OVR-07 |
| ACP-04 Hermes vertical cutover | ARCH-05–09, WORK-01–05, SEL-06, MIG-05 |
| ACP-05 browser turn boundary | UI-03, TURN-02, TURN-05 |
| ACP-05 cancel classification | TURN-03 |
| ACP-05 compaction deadline | OVR-04, TURN-09 |
| ACP-05 async compaction handoff/private-load | OVR-01, OVR-06, TURN-07, TURN-09–10 |
| ACP-05 durable compaction fork | OVR-01, OVR-06, TURN-07, TURN-10 |
| ACP-05 missing-session cutover (cancelled) | OVR-05, MIG-05 |
| ACP-05 permission diff | UI-07 |
| ACP-05 requested-cancel recovery | TURN-03–05 |
| ACP-05 Send Now human boundary | UI-03, TURN-05 |
| ACP-05 websocket writer cleanup | ARCH-09 |
| ACP-05 worker-context delivery | WORK-03 |
| ACP-06 legacy deletion | ARCH-07–08, UI-05–06, WORK-03–04, MIG-01–05, MIG-08, CLOSE-05 |
| ACP-07/08 shared activation | SEL-01–02, SEL-07–10, TURN-08–09 |
| ACP-07 Codex backend | SEL-07–08, OVR-03, TURN-08–10 |
| ACP-07 exact compaction prompt failure | TURN-09 |
| ACP-07 Worker/backend selection | SEL-01–05, MIG-06 |
| ACP-08 Claude backend | SEL-09–10, TURN-08–10 |
| ACP-10 final audit/verification | All IDs, especially CLOSE-03–10 |

## Live ACP-decision coverage

The current decision sequence is likewise explicit. Review-disposition decisions that govern only
how already-settled tickets were reviewed map to `CLOSE-02`; product decisions map to their exact
invariant rows.

| Decision(s) | Stable requirement IDs |
|---|---|
| `D-acp-single-conversation-path`, `D-acp-server-client-browser-envelope` | ARCH-01–02, ARCH-07–08, MIG-03–05 |
| `D-acp-ordered-ingress`, `D-acp-observer-reserves-typed-ingress`, `D-acp-ingress-retirement-is-explicit` | ARCH-04, ARCH-09 |
| `D-acp-explicit-turn-delivery`, `D-acp-steer-capability-is-connection-state` | UI-08, TURN-01–02 |
| `D-acp-compaction-normalizer` and all later compaction lifecycle/deadline/handoff/replay decisions | OVR-01, OVR-04, OVR-06, TURN-07–10 |
| `D-acp-permissions-are-transient`, `D-acp-permission-open-is-a-turn-actor-command`, `D-acp-permission-outcome-commits-before-activity`, `D-acp-permission-origin-is-admission-state` | UI-07, PERM-01–03 |
| `D-acp-commands-come-from-the-session`, `D-acp-browser-types-are-pinned-source`, browser metadata/gap/snapshot/recovery decisions | ARCH-03, ARCH-06, UI-01–05 |
| `D-acp-backends-are-definitions`, `D-acp-backend-definition-means-functional-worker` | SEL-06–10, WORK-06 |
| `D-acp-hermes-readonly` | MIG-07 |
| `D-acp-child-generation-is-not-binding-generation`, binding-row/reset/active-attach/worker-first-demand decisions | ARCH-05–06, WORK-01–02 |
| `D-acp-child-death-rejects-the-turn-queue`, cancellation-cause/requested-cancel/deadline/planned-retirement/delivery-capture decisions | TURN-02–06, ARCH-09 |
| `D-acp-reverse-services-are-session-confined`, `D-acp-filesystem-io-is-descriptor-backed`, reverse-state retirement | PERM-04–05, ARCH-09 |
| `D-acp-step-callback-runs-on-the-runner-thread`, `D-acp-step-gateway-delivers-pending-worker-context` | WORK-02–05 |
| `D-ticket-kickoff-selects-employee-backend`, pristine/editable/configured-pair decisions | SEL-01–05, MIG-06 |
| `D-acp-gemini-out-of-scope` | OVR-02, SEL-11 |
| `D-codex-acp-1-1-4-remains-unregistered` (superseded), `D-codex-stored-plan-prose-is-a-pinned-presentation-limit` | OVR-03, SEL-07–08 |
| Claude reverse-service/preflight/private-summary decisions | SEL-09–10, TURN-08–10 |
| ACP-06 one-way deletion/live-cutover decisions | MIG-01–05, CLOSE-05 |
| Review-process and bounded-correction decisions (`D-acp-program-review`, ticket review/correction/final-check decisions, small-UI ruling) | OVR-08, CLOSE-02 |

For avoidance of doubt, every ACP decision heading in the current decision sequence has an exact
destination below (comma-separated headings share the listed invariant; this is still the same
stable ledger, not a second verdict system):

- `ARCH-01/02/07/08`: `D-acp-single-conversation-path`,
  `D-acp-server-client-browser-envelope`.
- `ARCH-04/09`: `D-acp-ordered-ingress`, `D-acp-observer-reserves-typed-ingress`,
  `D-acp-ingress-retirement-is-explicit`, `D-acp-generation-publication-quiesces-old-sinks`,
  `D-acp-runtime-publication-failure-stops-delivery`.
- `ARCH-05/06`: `D-acp-child-generation-is-not-binding-generation`,
  `D-acp-load-means-replay-consumed`, `D-acp-browser-gaps-fail-closed`,
  `D-acp-binding-row-mirrors-product-session-fields`,
  `D-acp-active-attach-uses-the-typed-reset-buffer`,
  `D-acp-same-binding-reset-rebases-a-restarted-stream`,
  `D-acp-worker-first-demand-owns-a-stream-epoch`.
- `ARCH-03`: `D-acp-browser-types-are-pinned-source`.
- `UI-01/04/11`: `D-acp-browser-metadata-is-nontranscript-state`,
  `D-acp-browser-recovery-is-proven-by-ready`,
  `D-acp-browser-snapshot-is-an-immutable-projection`,
  `D-acp-terminal-state-is-a-panels-envelope`.
- `UI-03/TURN-05`: `D-acp-missing-message-ids-close-on-terminal-settlement`,
  `D-send-now-recovery-replays-its-captured-human-boundary`.
- `UI-05`: `D-acp-commands-come-from-the-session`.
- `UI-07/PERM-01–03`: `D-acp-permissions-are-transient`,
  `D-acp-permission-open-is-a-turn-actor-command`,
  `D-acp-permission-outcome-commits-before-activity`,
  `D-acp-permission-origin-is-admission-state`.
- `UI-08/TURN-01`: `D-acp-explicit-turn-delivery`,
  `D-acp-steer-capability-is-connection-state`.
- `TURN-02/03/06`: `D-acp-child-death-rejects-the-turn-queue`,
  `D-acp-cancellation-cause-freezes-successor`,
  `D-acp-planned-retirement-is-not-child-death`,
  `D-acp-delivery-captures-the-submitted-record`.
- `TURN-03/04/09`: `D-acp-requested-cancel-exception-is-interruption`,
  `D-acp-cancel-detection-timeout-is-not-the-recovery-deadline`,
  `D-acp-requested-cancel-recovery-replaces-the-process-not-the-session`,
  `D-acp-deadline-has-no-cancellation-grace`.
- `TURN-07–10/OVR-01/04/06`: `D-acp-compaction-normalizer`,
  `D-acp-capture-replaces-the-child-generation`,
  `D-acp-hermes-summary-is-structural-not-private-provenance`,
  `D-acp-compaction-persists-through-official-fork`,
  `D-acp-capture-has-one-actor-owned-deadline`,
  `D-acp-compaction-admission-closes-after-owner-settlement`,
  `D-compaction-has-an-emergency-deadlock-breaker-and-exact-failure`,
  `D-acp-compaction-handoff-is-session-aware-and-cross-process`,
  `D-acp-compaction-replay-persists-provenance-not-summary`,
  `D-compaction-is-an-opaque-lifecycle`,
  `D-compaction-contract-follows-backend-lifecycle-signals`,
  `D-acp07-provider-compaction-is-in-place`.
- `PERM-04/05/ARCH-09`: `D-acp-reverse-services-are-session-confined`,
  `D-acp-filesystem-io-is-descriptor-backed`,
  `D-acp-reverse-state-retires-with-its-child-generation`.
- `WORK-01–05`: `D-acp-step-callback-runs-on-the-runner-thread`,
  `D-acp-step-gateway-delivers-pending-worker-context`.
- `SEL-01–05`: `D-ticket-kickoff-selects-employee-backend`,
  `D-ticket-backend-remains-editable-through-unaccepted-kickoff`,
  `D-pristine-ticket-observation-does-not-bind`,
  `D-employee-backend-and-worker-registry-are-one-configured-pair`,
  `D-acp07-selector-settles-before-backend-activation`.
- `SEL-06–10/WORK-06`: `D-acp-backends-are-definitions`,
  `D-acp-backend-definition-means-functional-worker`,
  `D-claude-acp-declares-only-real-reverse-services`,
  `D-codex-stored-plan-prose-is-a-pinned-presentation-limit`,
  `D-acp-claude-private-summary-is-provider-replay-metadata`,
  `D-claude-requested-cancel-requires-fresh-child`.
- `MIG-05/OVR-05`: `D-acp-null-load-is-a-cutover-transition`,
  `D-acp-cutover-does-not-carry-legacy-session-compatibility`,
  `D-acp06-one-way-deletion-is-authorized`, `D-acp06-live-cutover-follows-one-ready-review`.
- `MIG-07`: `D-acp-hermes-readonly`.
- `OVR-02/SEL-11`: `D-acp-gemini-out-of-scope`.
- `OVR-03/SEL-07`: `D-codex-acp-1-1-4-remains-unregistered` (superseded),
  `D-codex-stored-plan-prose-is-a-pinned-presentation-limit`.
- `OVR-07/UI-10`: `D-acp-reference-design-boundary`.
- `OVR-09`: `D-acp-verify-at-checked-in-relay-default` (now not applicable).
- `CLOSE-02/OVR-08`: `D-acp-program-review`, `D-acp-ticket-reviews-use-subagents`,
  `D-acp-00a-review-disposition`, `D-acp-03-plan-review-disposition`,
  `D-acp-01-correction-check-closes-the-runtime-ticket`,
  `D-acp-03-correction-check-closes-the-unmounted-pane`,
  `D-acp-02-correction-check-closes-the-broker-ticket`,
  `D-acp-remainder-keeps-the-established-bar`,
  `D-acp-04-final-correction-is-proof-only`,
  `D-observed-small-ui-fixes-use-spot-check-and-dogfood`.
- `ARCH-07/09`: `D-acp-04-uses-one-reentrant-safe-sequencer`,
  `D-acp-websocket-writer-owns-iteration-waits`.

## Blocking summary

All product/runtime and closeout requirements have direct source plus focused/test/runtime evidence.
There are zero `unproved` rows and zero unresolved findings. ACP-10 and the full ACP migration satisfy
their completion contract.
