# Test pruning outcome

This program compares the current suite with `origin/staging` revision
`b7ca8e047967e405feeebe058f5cd2ef82b2c2e5`. Python collection was performed
separately for unit, integration, and E2E because collecting all three together
loads incompatible nested `conftest.py` plugins. No product code, Conversation
backend implementation, or frozen migration implementation changed.

## Counted result

The acceptance metric is runnable pytest and Vitest cases. Legacy Node scripts
are reported separately because one script may contain many assertions and is
not comparable to one collected test case.

| Suite | Baseline | Pruning branch | Known integrated projection |
|---|---:|---:|---:|
| Python unit | 1,924 | 935 | 937 after the Sprint feature's two new cases |
| Python integration | 14 | 8 | 8 |
| Playwright E2E | 39 | 19 | 19 |
| Vitest runtime cases | 332 | 178 | 178 |
| **Comparable total** | **2,309** | **1,140** | **1,142** |

The branch removes 1,169 of 2,309 comparable cases, or 50.6%. The known
integrated projection removes 1,167, or 50.5%, leaving twelve cases of margin
under the required ceiling of 1,154. Root must recollect after integration;
these figures do not assume that a definition with `it.each` is one case.

Legacy browser/Node scripts move from 12 to 9 on this branch: Atlas, the surface
token source scan, and the exact-geometry CSS script are gone. The Sprint feature
adds one new script, so the known integrated projection is 10. This separate
journey count does not enter the 2,309-case denominator.

The static surface moves from about 83,363 lines to about 50,975 lines:

| Surface | Baseline lines | Branch lines |
|---|---:|---:|
| Python unit | 59,679 | 34,698 |
| Python integration | 1,044 | 888 |
| Python E2E | 5,227 | 3,380 |
| Web tests and harnesses | 13,495 | 7,922 test-file lines plus 215 harness lines |
| Python support and typing fixtures | 3,918 | 3,872 |

## Decisions from independent review

The independent plan review in `04-test-pruning-review.md` was binding. Its
findings changed the implementation as follows:

- Migration transformation fixtures remain. Only repeated assertions that each
  feature migration reaches the current symbolic head were removed. `test_db.py`
  remains the one canonical head assertion. The supervisor wake migration now
  asks Alembic for `head`, so a later migration does not require editing it.
- `test_ticket_conversation_history_migration.py`, the approval metadata
  migration, Planning Day stranded-stage migration, and notification delivery
  graph migrations remain.
- The exact failed Markdown save retry and untouched loaded-preview proposal
  approval E2E cases remain.
- The retained-page same-origin release replacement E2E case remains.
- Conversation HTTP tagged send-fate serialization, sender identity, stale
  active-pointer refusal, multimodal delivery, replay/tail gaplessness, held
  mutation wiring, every persisted event variant, legacy readable rows, and
  transactional writes remain at their actual boundaries.
- Conversation start-wiring retains active-pointer races, held/refused
  configuration timing, and reset/discard behavior.
- Five deployment-asset cases still execute the real checked-in scripts. Only
  static spelling and retired-file assertions were removed.
- The CSS parser/build gate, verify mode parser/reporting, and real/test clock
  isolation remain. Only the retired SPEC skip-pattern scanner and its synthetic
  examples were removed.
- Follow-up review restored one Conversation stream-recovery Vitest case and
  replaced the preview E2E artifact case with a compact HTML journey covering
  sibling resources, script execution, refresh, and in-place navigation. E2E
  remains at 19.

## Retained proof map

| Risk | Cheapest retained proof |
|---|---|
| Provider event translation and real delivery | All Claude, Codex, Hermes adapter tests and the 51-case real-process Conversation conformance suite are unchanged. |
| Conversation HTTP shapes | `test_conversation_api.py` retains tagged send fates, sender IDs, files/images, stale pointers, held mutations, public replay, and tail lifecycle. |
| Conversation persistence | `test_conversation_storage.py` retains every event payload round trip, legacy row readability, atomic delivery writes, ordering, races, and change-signal behavior. |
| Conversation orchestration races | `test_conversation_system.py` retains stale-turn rejection, child replacement/recovery, compaction, sender admission, kill/start/janitor races, and unknown-exception recovery. |
| Ticket-to-Conversation association | `test_conversation_start_wiring.py`, `test_ticket_conversation_routes.py`, and the active-history E2E retain association races, refused first sends, reset/discard, and past-pointer refusal. |
| Worker prompt boundary | `test_worker_step_readiness_loop.py` retains one-winner claim, refusal release, queued success, opener text, pending-context delivery-before-ack, and paired-stage opener. The real Worker skill integration proof remains. |
| Ticket canonical writes | `test_tickets_engine.py`, `test_ticket_edit_api.py`, value-edit tests, and generic registry tests retain lifecycle, proposal resolution, atomic patch failure, scope, recap, and blocker rollback. |
| Planning time | Scheduled Ticket exact-minute/pre-05:00/cadence cases and the planning-date domain tests remain. |
| Database upgrades | `test_db.py` retains current head, supported-old-database adoption, populated reshape, table rebuild integrity, concurrent creation, and rollback on three failure classes; feature migration fixtures retain their own transformed rows. |
| Deployment and restore | Six deployment state-machine cases, ten backup/restore cases, five lifecycle cases, and five real script-asset cases retain healthy cutover, rollback, corruption rejection, managed skill restore, symlink safety, locking, and service-manager wiring. |
| Change notification | The write-door suite retains ordinary/quiet/failed commit, rollback/no-op, cross-thread delivery, and delivery-after-lock; web change-stream tests retain coalescing, reconnect, hidden-tab catch-up, and stop cleanup. |
| Browser-only behavior | The 19 E2E cases below retain only risks that cross a live server and real browser and are not fully proved at a lower boundary. |

## Whole-file deletions

These unit files were removed because their useful behavior is retained at the
owning writer, API, installed-process, or browser boundary:

```text
test_application_shutdown_process.py
test_authctx_routes.py
test_board_view.py
test_bounded_list_reads.py
test_change_signal.py
test_chat_ingress_contract.py
test_chief_conversation_routes.py
test_chief_external_work.py
test_cli_blockers.py
test_cli_record_projection.py
test_core_loops.py
test_day_api.py
test_days.py
test_dev_server_proxy.py
test_environment_cli.py
test_environment_contracts.py
test_external_work_generic.py
test_go_no_go_gate.py
test_hermes_home.py
test_in_memory_conversation_system_conformance.py
test_links.py
test_new_worker_type.py
test_path_observer.py
test_planning_day_worker_type.py
test_probe_type.py
test_production_worker_types.py
test_project_backend_skill_links.py
test_projects.py
test_readiness_wake_actions.py
test_response_compression.py
test_return_for_revision.py
test_review_ticket_decisions_type_driven.py
test_send_message.py
test_server_changes_stream.py
test_server_lifecycle_control.py
test_server_meta_and_health.py
test_server_shutdown_process.py
test_server_static_paths.py
test_sprint_item_delete.py
test_sprint_item_delete_cli.py
test_stage_ownership_backend.py
test_supervisor_review_cli.py
test_ticket_approval_gate_domain.py
test_ticket_delete.py
test_ticket_judgments.py
test_ticket_lifecycle.py
test_worker_help.py
test_worker_my_ticket.py
test_worker_settings.py
test_worker_type_manifest_endpoint.py
```

The removed web unit files were presentation or source-shape mirrors. Canonical
wire values remain in Python transport/storage tests; user-visible behavior
remains in retained component and browser tests.

```text
conversation-signal-presentation.test.ts
conversation-task-progress.test.ts
conversation-thread-items.test.ts
conversation-thread-plan.test.ts
conversation-tool-presentation.test.ts
conversation-turn-time.test.ts
conversation-wire-values.test.ts
day-presentation.test.ts
deployment-status.test.ts
priority-tile.test.ts
query-catalogue.test.ts
resource-state-for-queries.test.ts
scheduled-tasks.test.ts
usage-rings.test.ts
browser-css.test.mjs
surface-token-discipline.test.mjs
```

## Consolidated large suites

The largest case reductions are explicit rather than hidden in loops:

| File | Cases before | Cases after | What remains |
|---|---:|---:|---|
| `test_conversation_system.py` | 108 | 60 | SQLite/child/race/recovery/compaction behavior beyond conformance |
| `test_conversation_api.py` | 64 | 34 | HTTP serialization, files, public replay and tail lifecycle |
| `test_conversation_storage.py` | 68 | 50 | all durable event variants and transaction/order risks |
| `test_conversation_snapshot.py` | 61 | 33 | distinct provider inventory/update mappings and process outcomes |
| `test_conversation_backend_usage.py` | 33 | 17 | distinct provider translations, cache refresh and serialization |
| `test_conversation_voice_transcription.py` | 31 | 12 | fabrication, silence, codec, provider and route boundaries |
| `test_conversation_start_wiring.py` | 31 | 15 | active-pointer/configuration/reset races |
| `test_worker_step_readiness.py` | 45 | 19 | planning-day, ownership, blocker and closeout-lane decisions |
| `test_sprint_item_supervisors.py` | 40 | 12 | scoping, delivery-before-mutation, current conversation and delete/restart races |
| `test_tickets_engine.py` | 54 | 29 | lifecycle, proposal, recap, scope and blocker transactions |
| `test_ticket_edit_api.py` | 26 | 11 | defaults, configuration validation and atomic compound patches |
| `test_request_identity.py` | 26 | 6 | actor classification, trusted override and exact planning capability |
| `test_scheduled_tickets.py` | 22 | 8 | exact time/cadence, suppression, placement, idempotency and failure isolation |
| `test_deployment.py` | 31 | 6 | install/cutover/rollback/lock/backup validation |
| `test_database_backups.py` | 28 | 10 | integrity, retention, restore rollback and managed files |
| `test_deployment_lifecycle.py` | 18 | 5 | transition, malformed/stale state, atomic replace and rollback authority |
| `test_deployment_assets.py` | 14 | 5 | real shell/script execution boundaries |

Frontend matrices were reduced by removing format, label, malformed-input, and
presentation permutations after one representative boundary remained. The
runtime list, with `it.each` expanded, is 178 cases. Load-bearing retained files
include `file-preview.test.ts`, `conversation-draft-transaction.test.ts`,
`change-stream.test.ts`, `conversation-outgoing-storage.test.ts`,
`ticket-conversation-state.test.ts`, and the restored stream-recovery test in
`conversation-feed.test.ts`.

## E2E inventory

The retained 19 cases are:

```text
test_dev_conversation_pane.py                       2
test_live_update.py                                 2
test_notifications_frontend.py                     1
test_pending_proposal_edit.py                       1
test_release_freshness.py                           1
test_review_keyboard.py                             1
test_sprint_item_workspace.py                       1
test_ticket_conversation_history_browser.py         1
test_ticket_conversation_reply.py                   1
test_ticket_dev_server_links.py                     1
test_ticket_file_previews.py                        4
test_trusted_ingress_browser.py                     1
test_workers_frontend.py                            2
```

Deleted E2E files were blocker/status presentation (`test_blockers_frontend.py`,
`test_board_stage_indicators.py`, `test_ticket_verdict.py`), repeated transport
permutations (`test_connection_status.py`), and four tests of the reusable test
fixture itself (`test_reusable_server_isolation.py`). The started-pane, picture,
and model-catalog permutations were removed from the developer pane; lower
Conversation API/provider/frontend tests retain those boundaries. Preview cases
that duplicated URL/sandbox/byte validation were removed only after the compact
HTML artifact journey was retained.

## Focused verification

No `./verify` or broad pytest suite ran. Focused gates completed during pruning:

- `test_instrument.py`: 2 passed; changed verifier files passed Ruff.
- Conversation system/in-memory/real conformance cohort: 135 passed before the
  later consolidation; the final Conversation API/storage/system cohort also
  completed without failure.
- Deployment/environment retained cohort: 35 passed.
- Domain boundary cohort: 133 passed.
- API/readiness retained cohort: 98 passed.
- Writer/API/CLI retained cohort: 127 passed.
- Migration-head cohort: 26 passed.
- Final changed frontend parameter cohort: 146 Vitest/typecheck executions
  passed; the later six-file buffer cohort passed 28 executions with no type
  errors.
- Collection-only final branch counts: unit 935, integration 8, E2E 19; Vitest
  runtime list 178.

Root still owns the final integrated collection, the one final `./verify`, and
the completeness claim. The only material count uncertainty is merge fallout:
if an integrated feature adds more cases than the two already counted, root must
recount against the 1,154 ceiling rather than relying on this projection.
