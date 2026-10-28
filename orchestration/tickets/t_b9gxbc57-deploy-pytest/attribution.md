# Deploy pytest attribution

The failed deploy contains 17 failed cases: 15 obsolete test setups and two temporary-storage failures. No product regression was identified. The corrections preserve the behavioral assertions and the production contracts.

The source base is `f723c5a551c7d5db05aaa74b8081efe0080dbf90`, fetched from `origin/staging` before the isolated Ticket worktree was created. The failed run is [40653278046](https://github.com/khushal-jagota/panels/actions/runs/40653278046), requested source `50841b899414898ec62a236121ea25464640914f`. The previous successful run is [40548925603](https://github.com/khushal-jagota/panels/actions/runs/40548925603), source `1391058a`.

## Causal evidence

Each historical worktree used its own fresh virtual environment, exact requirements, and editable installation. Browser checks also used that revision's Node dependencies and frontend build. No test from a newer revision was overlaid on older source. Adjacent parent/child reproductions isolate the causal commits directly.

| Failed cases | Causal commit | Parent result | Commit result | Verdict |
| --- | --- | --- | --- | --- |
| Ingress own-Worker proposal | `15439b68784cd7308d4291ce09b0a8ca26b54a20` | `d6933fec`: pass | Same test fails with ineligible review holder | Obsolete fixture |
| Ten diagnostic retention cases | `23ad773e6040d88f30e91d7e1f7d9f3b2d8d49f3` | `bd44caa7`: 46 unit cases and one integration case pass | Nine unit cases and one integration case fail at schema refusal | Obsolete fixtures |
| Two Review upgrade cases | `23ad773e6040d88f30e91d7e1f7d9f3b2d8d49f3` | `bd44caa7`: both pass | Both fail because the video ledger already exists | Obsolete fixture |
| Unread refusal and owner-send history cases | `12fa32776d1dbb85cbc5c3cc01f4ce844ea8cda3` | `08068fcf`: both pass | Both fail at old initial-entry assumptions | Obsolete fixtures |
| Historical reader `[86]` | No source defect identified | The last successful deploy completed integration | Original log reports ENOSPC during historical pip installation | Environment |
| Video upload/playback | No source defect identified | This connected case was introduced with video support after the successful baseline | Unchanged current test passes on disk and fails on low-space `/tmp` with reservation HTTP 507 | Environment |

The source-caused failures all start after `1391058a`. There is no claim that they predate the successful deploy. The environmental failures do not have a causal source commit. A setup failure does not prove a contract change or a product regression.

## Complete failed-case inventory

Paths below are relative to `tests/`.

| Suite and file | Case | Cause group |
| --- | --- | --- |
| unit/test_type_driven_ingress.py | test_proposal_route_accepts_only_the_ticket_own_worker | Ingress |
| unit/test_diagnostic_history_maintenance.py | test_exact_removal_records_sparse_ranges_and_rebuilds_reader | Retention |
| unit/test_diagnostic_history_maintenance.py | test_atomic_rollback_includes_delete_projection_and_new_metadata | Retention |
| unit/test_diagnostic_history_maintenance.py | test_stale_protection_fact_and_actor_refuse_before_delete | Retention |
| unit/test_diagnostic_history_maintenance.py | test_database_runtime_owner_and_sqlite_reader_block_apply | Retention |
| unit/test_diagnostic_history_maintenance.py | test_metadata_schema_drift_refuses_before_deletion[table] | Retention |
| unit/test_diagnostic_history_maintenance.py | test_metadata_schema_drift_refuses_before_deletion[trigger] | Retention |
| unit/test_diagnostic_history_maintenance.py | test_real_expiry_rebuild_preserves_deep_full_supervisor_controls_and_tool_identity | Retention |
| unit/test_diagnostic_retention_policy.py | test_report_reads_without_initialization_and_binds_source_schema_and_database_identity | Retention |
| unit/test_diagnostic_retention_policy.py | test_report_refuses_an_unfamiliar_projection_payload_store | Retention |
| integration/test_diagnostic_history_inactive.py | test_startup_restart_and_background_loops_never_expire_and_reconcile_offline_dirty | Retention |
| integration/test_navigation_current_workspace.py | test_review_projection_upgrade_resets_old_cursors_without_granting_authority[automatic_deployment_recovery-8-4] | Review upgrade |
| integration/test_navigation_current_workspace.py | test_review_projection_upgrade_resets_old_cursors_without_granting_authority[review_messages-9-5] | Review upgrade |
| integration/test_navigation_historical_reader.py | test_exact_staging_ingress_reader_reversal_and_lossy_fixture_restore[86] | Environment |
| e2e/test_conversation_signals.py | test_history_refused_row_keeps_unread_credit_until_latest_delivery[chromium] | Initial entry |
| e2e/test_conversation_video_upload.py | test_video_upload_early_send_and_native_range_playback[chromium] | Environment |
| e2e/test_ticket_conversation_shared_behaviors.py | test_owner_send_lands_at_the_end_of_the_conversation_not_the_retained_window[chromium] | Initial entry |

## Contract and correction decisions

### Ingress

The approved What Changes for “Only the user or the direct manager may hold a review” separates review eligibility from general authority. The original fixture grants a probe Ticket general authority, then names it as review holder. The replacement assigns the target to a Manager and names that direct Manager as holder. Both that Manager and the generally authorized probe must still fail to propose for the target. The target's own Worker must succeed.

### Diagnostic retention

The video commit adds `video_upload_ledger` after `owner_message_preview`. The destructive maintenance policy deliberately refuses this unreviewed schema. The old fixtures create HEAD, then expect maintenance to accept it.

The approved expiry plan requires refusal of unfamiliar schema versions. The approved follow-up “Keep conversation reads honest and complete after diagnostic expiry” preserves version refusals. The approved admission repair also preserves controls, tool identity, expiry, and replay assertions. These contracts authorize fixture corrections, not a wider production allowlist.

The maintenance fixtures now build the real migration chain through `owner_message_preview`. They do not relabel a current database. The existing report policy case explicitly protects current-schema refusal and retains its source/schema/database binding checks. The unfamiliar-projection case still reaches its intended refusal on supported schema.

The integration case retains two independent current-schema startup/restart checks without expiry. A separate supported archive undergoes offline maintenance. Shipped migrations are restored before the current runtime opens that archive. Retained events, controls, and dirty-publication reconciliation remain asserted.

### Review upgrades

The fixture creates the current schema, then rewinds only its historical migration marker. The video commit therefore causes it to replay CREATE TABLE against an existing video ledger. The fixture now asserts that this synthetic ledger is empty and removes it before the rewind. Epoch rotation, old cursor refusal, prepared/current schema values, and runtime-only authority assertions remain unchanged. Product migrations remain unchanged.

### Conversation entry

The approved plan for “Opening a conversation lands at the bottom, always” explicitly removes first-unread history admission. Both affected tests silently depend on that removed entry behavior.

The replacement setup opens at newest and uses actual upward paging until the retained window no longer contains newest. It settles reconnects and pending reads before either proof starts. The unread case injects its first new unread message only after this setup. It retains stable anchor, absent row, frozen canonical credit, all unread projections, and Latest delivery assertions. The owner-send case retains its non-vacuous history precondition, catch-up, single copy, persistent visibility, and final-order assertions.

These existing E2E cases protect reader, browser, tail, canonical read-writer, and server boundaries. A lower-layer check cannot prove those connected outcomes. No new test case or browser host is added.

## Temporary-storage evidence and release requirement

The original historical reader output contains `OSError: [Errno 28] No space left on device` during installation for exact revision `86da50467e292a863283695dbbbdcf6f0b9d49a7`. Its unchanged current test passes when pytest temporary state resides on the disk under `/home/vps`. Exact historical pins, source identity, old/current readers, and lossy fixture restoration all execute.

The approved “Video upload for Panels” contract requires a 5 GiB free-disk reserve and refusal at capacity. The unchanged current browser case passes with disk-backed temporary state. It fails under `/tmp` with approximately 2 GiB free. The server returns `507 Insufficient Storage` before any PUT, which explains the zero-transfer assertion. The production disk-reserve policy remains unchanged.

The release environment must provide sufficient temporary storage before the owner reruns the deploy. This Ticket changes neither deployment configuration nor the historical reader and video assertions. Disk-backed verification proves the source with the required capacity. It does not claim that an unchanged low-space deploy environment can pass.

## Verification route

Run Ruff against changed tests, mypy, and all unit, integration, and E2E tests as three separate pytest processes. Use disk-backed temporary state. E2E uses the deployment workflow's artifact shadow so managed artifact paths cannot reach production. Record the exact checked revision and results in the Implementation proposal.

Independent review checks the fixture corrections against these contracts. Any newly exposed product regression requires a separate report without a repair or weaker assertion. Integration remains one pull request into staging after its merged-tree mypy check passes. Main and deployment remain owner actions.
