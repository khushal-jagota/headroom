This file records the allowance/source translation from T3 revision `75e4ceb96489a2fe92c710c79dbea59778cccfd1`.

The modules preserve upstream names, responsibilities, algorithms, contracts, and load-bearing operation order. The owner permits ordinary Python expressions and async construction.

## Design-level language substitutions

| Boundary | T3 source | Panels source | Substitution |
|---|---|---|---|
| Effect service/Ref/stream/fiber and semaphore construction | `apps/server/src/usage/UsageLimitSources.ts:64` | `src/planner/conversation/usage/UsageLimitSources.py:43` | Injected settings/API/background dependencies, an asyncio lock, subscriber queues, and owned tasks preserve refresh ordering, initial snapshots, duplicate suppression and scoped cleanup. |
| Effect HTTP, timeout and schema decoding | `apps/server/src/usage/cliproxyApi.ts:115` | `src/planner/conversation/usage/cliproxyApi.py:132` | Injected asynchronous transport replaces Effect HttpClient. asyncio.wait_for preserves the 15-second timeout. Local validators preserve required, optional and nullable protocol fields. Account reads retain concurrency four and failure isolation. |
| Pure dependency inside a native provider module | `apps/server/src/provider/Layers/CodexProvider.ts:102` | `src/planner/conversation/usage/cliproxyApi.py:38` | Only codexPlanLabel is extracted. Importing the complete provider would pull in the excluded native runtime. Labels and fallback semantics remain identical. |
| Effect DateTime/Option and JavaScript numeric distinctions | `apps/server/src/provider/Layers/codexUsageLimits.ts:49` | `src/planner/conversation/usage/codexUsageLimits.py:20` | Python datetime produces UTC ISO strings with millisecond precision. isNumber excludes Python bool, as JavaScript typeof number does. None represents nullable or absent values at the boundary. |
| Effect Ref for Claude scoped model names | `apps/server/src/provider/Layers/claudeUsageLimits.ts:193` | `src/planner/conversation/usage/claudeUsageLimits.py:119` | The injected mutable names dictionary replaces Ref.set. The mapper retains scoped-name selection and event conversion order. |
| Structural record and iterable operations | `apps/server/src/provider/providerUsageLimits.ts:60` | `src/planner/conversation/usage/providerUsageLimits.py:30` | Python dictionaries and stable sorting replace Map, object spread and toSorted. Unchanged updates return the original object. Sparse updates preserve missing resets and duration fields. |

The complete MIT notice and pinned revision belong to the root copy manifest. `UsageLimitSources.ts` has no adjacent upstream test file.

## Approved functional extension

`localUsageLimits.py` is the approved local-history adapter. It is separate from the language-substitution table.

- The reader emits selected allowance fields through callbacks. It never passes raw lines, messages, tool bodies, credentials, or credit balances.
- The adapter uses the scan inventory. It does not walk provider directories or open transcripts.
- The companion cache stores selected observations and actual timestamps. Stable observations and provisional tails remain separate.
- An unchanged file reuses its companion entry. An append keeps stable observations and replaces the provisional tail.
- `canResume` compares the companion with the token cache baseline. A missing, corrupt, or older companion forces a separate selected-file allowance repair.
- The token reader keeps its own resume decision and token cache during that repair. Guard rejection resets the adapter candidate too.
- Chronological observations use T3 converters and sparse-window merge. Model-specific Codex limits cannot replace the main allowance.
- `checkedAt` preserves the last valid observation time. Missing local allowance reports unavailable.
- `credits.balance` never becomes resetCredits or currency. No provider requests, CLI calls, or native work occur.

The independent review found that concurrent windows shared the adapter's candidates and selected inventory. One completion could erase another inventory or publish an observation with another file's metadata.

The correction gives each scan a ContextVar context before its thread starts. Candidates, selected entries, and resume baselines belong to that scan. Completed entries remain immutable. An RLock protects only the shared entry store and persistence. Token scans remain concurrent.

The originating scan task identifies its completed snapshot through a WeakKeyDictionary. After the shielded scan finishes, `readSummary` adopts that snapshot into its caller's context. The existing limits endpoint then reads the requested scan's snapshot. No summary field or endpoint contract changes.

## Upstream case inventory

The tests preserve upstream inputs and assertions. Python `None`, injected clocks, fake transports, and `asyncio.run` replace TypeScript nullable values and Effect test services.

The Codex legacy-map parameter retains three cases. The hub outcome parameter retains all three outcomes.

### codexUsageLimits

| T3 case | Python case |
|---|---|
| maps primary and secondary onto the session and weekly windows | `tests/unit/t3_usage/test_codexUsageLimits.py:19` `test_maps_primary_and_secondary_onto_the_session_and_weekly_windows` |
| treats a lone duration-less primary as monthly on Free and Go | `tests/unit/t3_usage/test_codexUsageLimits.py:60` `test_treats_a_lone_duration_less_primary_as_monthly_on_free_and_go` |
| selects the main Codex allowance and leaves Spark out | `tests/unit/t3_usage/test_codexUsageLimits.py:77` `test_selects_the_main_codex_allowance_and_leaves_spark_out` |
| supports legacy reads with no bucket map: %j | `tests/unit/t3_usage/test_codexUsageLimits.py:106` `test_supports_legacy_reads_with_no_bucket_map` |
| does not show a model-specific legacy snapshot as the main allowance | `tests/unit/t3_usage/test_codexUsageLimits.py:113` `test_does_not_show_a_model_specific_legacy_snapshot_as_the_main_allowance` |
| carries only the windows the notification names | `tests/unit/t3_usage/test_codexUsageLimits.py:125` `test_carries_only_the_windows_the_notification_names` |
| ignores Spark notifications so they cannot overwrite the main allowance | `tests/unit/t3_usage/test_codexUsageLimits.py:142` `test_ignores_spark_notifications_so_they_cannot_overwrite_the_main_allowance` |
| keeps the JSON-RPC code and nothing else from a request failure | `tests/unit/t3_usage/test_codexUsageLimits.py:168` `test_keeps_the_json_rpc_code_and_nothing_else_from_a_request_failure` |
| phrases a dead process differently from a bad answer | `tests/unit/t3_usage/test_codexUsageLimits.py:184` `test_phrases_a_dead_process_differently_from_a_bad_answer` |
| counts available credits and reports the soonest expiry | `tests/unit/t3_usage/test_codexUsageLimits.py:191` `test_counts_available_credits_and_reports_the_soonest_expiry` |
| rides along on the probe's limits | `tests/unit/t3_usage/test_codexUsageLimits.py:206` `test_rides_along_on_the_probes_limits` |
| names the exhausted window and the workspace's missing credits | `tests/unit/t3_usage/test_codexUsageLimits.py:220` `test_names_the_exhausted_window_and_the_workspaces_missing_credits` |
| points a reached spend cap at the workspace owner | `tests/unit/t3_usage/test_codexUsageLimits.py:240` `test_points_a_reached_spend_cap_at_the_workspace_owner` |
| names no window when credits run out without one | `tests/unit/t3_usage/test_codexUsageLimits.py:259` `test_names_no_window_when_credits_run_out_without_one` |
| says only what it knows without a snapshot | `tests/unit/t3_usage/test_codexUsageLimits.py:268` `test_says_only_what_it_knows_without_a_snapshot` |
| keeps windows an update does not carry | `tests/unit/t3_usage/test_codexUsageLimits.py:275` `test_keeps_windows_an_update_does_not_carry` |
| ignores a model-specific snapshot so it cannot replace the main allowance | `tests/unit/t3_usage/test_codexUsageLimits.py:291` `test_ignores_a_model_specific_snapshot_so_it_cannot_replace_the_main_allowance` |

### claudeUsageLimits

| T3 case | Python case |
|---|---|
| maps the session, weekly, and model-scoped weekly windows | `tests/unit/t3_usage/test_claudeUsageLimits.py:12` `test_maps_the_session_weekly_and_model_scoped_weekly_windows` |
| names the overage-included bucket only from a scoped entry that drew a row | `tests/unit/t3_usage/test_claudeUsageLimits.py:73` `test_names_the_overage_included_bucket_only_from_a_scoped_entry_that_drew_a_row` |
| reports API key and Bedrock accounts as unsupported | `tests/unit/t3_usage/test_claudeUsageLimits.py:90` `test_reports_api_key_and_bedrock_accounts_as_unsupported` |
| skips a window the endpoint reports without a utilization | `tests/unit/t3_usage/test_claudeUsageLimits.py:96` `test_skips_a_window_the_endpoint_reports_without_a_utilization` |
| scales the 0–1 utilization and epoch-second reset onto the probe's window id | `tests/unit/t3_usage/test_claudeUsageLimits.py:119` `test_scales_the_0_1_utilization_and_epoch_second_reset_onto_the_probes_window_id` |
| lands the streamed overage-included bucket on the row the probe named | `tests/unit/t3_usage/test_claudeUsageLimits.py:142` `test_lands_the_streamed_overage_included_bucket_on_the_row_the_probe_named` |
| ignores windows the page does not render and events without a utilization | `tests/unit/t3_usage/test_claudeUsageLimits.py:158` `test_ignores_windows_the_page_does_not_render_and_events_without_a_utilization` |

### providerUsageLimits

| T3 case | Python case |
|---|---|
| returns the published object itself when no window moved | `tests/unit/t3_usage/test_providerUsageLimits.py:29` `test_returns_the_published_object_itself_when_no_window_moved` |
| upserts by id and keeps the reset a percent-only update omits | `tests/unit/t3_usage/test_providerUsageLimits.py:45` `test_upserts_by_id_and_keeps_the_reset_a_percent_only_update_omits` |
| leaves an unsupported account and an empty update alone | `tests/unit/t3_usage/test_providerUsageLimits.py:64` `test_leaves_an_unsupported_account_and_an_empty_update_alone` |
| preserves reset credits when a streamed window update changes usage | `tests/unit/t3_usage/test_providerUsageLimits.py:80` `test_preserves_reset_credits_when_a_streamed_window_update_changes_usage` |
| keeps the last good windows through a failed probe but not an unsupported one | `tests/unit/t3_usage/test_providerUsageLimits.py:96` `test_keeps_the_last_good_windows_through_a_failed_probe_but_not_an_unsupported_one` |

### cliproxyApi

| T3 case | Python case |
|---|---|
| reads both accounts and their earliest unexpired credits without plugin endpoints | `tests/unit/t3_usage/test_cliproxyApi.py:123` `test_reads_both_accounts_and_their_earliest_unexpired_credits_without_plugin_endpoints` |
| keeps usage when the credits endpoint fails | `tests/unit/t3_usage/test_cliproxyApi.py:157` `test_keeps_usage_when_the_credits_endpoint_fails` |
| isolates a failed account and never publishes upstream error bodies | `tests/unit/t3_usage/test_cliproxyApi.py:178` `test_isolates_a_failed_account_and_never_publishes_upstream_error_bodies` |
| maps Claude scoped windows without a scheduler plugin | `tests/unit/t3_usage/test_cliproxyApi.py:199` `test_maps_claude_scoped_windows_without_a_scheduler_plugin` |
| pins redemption to the displayed credit and clears only that account's cooldown | `tests/unit/t3_usage/test_cliproxyApi.py:229` `test_pins_redemption_to_the_displayed_credit_and_clears_only_that_accounts_cooldown` |
| reports ${code} accurately | `tests/unit/t3_usage/test_cliproxyApi.py:264` `test_reports_code_accurately` |
| reports redemption success even if cooldown clearing fails | `tests/unit/t3_usage/test_cliproxyApi.py:275` `test_reports_redemption_success_even_if_cooldown_clearing_fails` |
| skips disabled accounts and rejects redemption on them | `tests/unit/t3_usage/test_cliproxyApi.py:284` `test_skips_disabled_accounts_and_rejects_redemption_on_them` |
| keeps the same redemption id after an uncertain upstream failure | `tests/unit/t3_usage/test_cliproxyApi.py:295` `test_keeps_the_same_redemption_id_after_an_uncertain_upstream_failure` |
| rejects unknown accounts without forwarding a redemption | `tests/unit/t3_usage/test_cliproxyApi.py:328` `test_rejects_unknown_accounts_without_forwarding_a_redemption` |

## Added-test admission

Upstream has no local-history adapter. Its tests cannot cover local extraction, companion persistence, or token/allowance cache disagreement.

`test_localUsageLimits.py` covers latest main-account selection, sparse windows, old observation provenance, missing/malformed data, prohibited balance mapping, warm/restart reuse, append, guard reset, provisional tails, and corrupt-cache repair.

One connected unit case joins the real reader, token cache, and companion cache over synthetic temporary files. It proves that missing companion state during append keeps token resume and performs allowance-only repair. No browser or server host is created.

Two focused review regressions protect concurrency. One holds the fake rates response until two real service scans finish collection. Different windows must return their own 42% and 99% observations, then reuse the unchanged cache. The other interleaves two reader candidates for one path. A restart must never match newer file metadata with the older observation. Both cases use synthetic local files and existing unit setup.

`test_UsageLimitSources.py` adds one missing boundary: empty source configuration sends zero injected transport requests. The real Usage path configures no external sources.

## Verification

Focused checks pass: Ruff, strict mypy on all six modules and six test files, and 55 pytest cases in 8.53 seconds. The only warning is the existing PreviewClientSession inheritance deprecation.

After the concurrency correction, all 19 adapter and UsageService cases pass in 5.31 seconds. Ruff and strict mypy pass on both source modules and both test files. Two typing-only repairs in the UsageService tests use pytest monkeypatch for method injection and declare the gathered pricing result type. Inputs and assertions stay unchanged. The six service cases pass again in 3.04 seconds after those repairs.

The independent reviewer reran both concurrency regressions and closed the finding. No unresolved defect remains in this correction.

No commit, provider call, native operation, installation, service action, or production mutation occurred in this implementation scope.
