# T3 server core translation

The owner approved this module layout and T3 design. The source is T3 revision `75e4ceb96489a2fe92c710c79dbea59778cccfd1`. Copyright 2026 T3 Tools Inc. The full MIT notice lives in `THIRD_PARTY_NOTICES.md`.

Python keeps the source module names, record keys, reducer order, guards, compact cache version, deduplication, provisional tails, price lookup, aggregation, and shared detached request lifetime. This implementation does not call the former Panels projection, native-response parser, import queue, SQLite store, or prefix-verification code.

## Runtime substitutions

| T3 source | Panels destination | Required substitution |
|---|---|---|
| `UsageService.ts:113–612` | `UsageService.py` | Effect services, filesystem, semaphore, cached load, and detached Deferred/fiber become injected dependencies, standard filesystem, asyncio lock/task/shield, and one protected cache load. |
| `UsageService.ts:232–262` | `UsageService.py:collectDirs` | T3 provider-home and settings resolution become injected transcript directories and settings. The root integration owns configured home resolution. |
| `UsageService.ts:403–570` | `UsageService.py:scanSummary` | Transcript walks and reads run through `asyncio.to_thread`; rate loading runs concurrently. Mutable cache operations use a thread lock. |
| `UsageService.ts:579–606` | `UsageService.py:readSummary` | Atomic enrollment/task creation contains no await. `asyncio.shield` preserves the shared scan after caller cancellation. |
| `usageTranscriptReader.ts:89–148` | `usageTranscriptReader.py` | Node directory entries, stat, and device/inode become standard Python filesystem calls. |
| `usageTranscriptReader.ts:187–313` | `usageTranscriptReader.py:readTranscriptRecords` | Node byte streams become 64 KiB Python reads. Byte offsets, CRLF trimming, pending chunks, fork state, tail copies and FNV guard retain source behavior. |
| `usageTranscripts.ts:36–488` | `usageTranscripts.py` | JavaScript object reducers become dictionaries. Number/finite checks explicitly exclude Python booleans. Date parsing produces integer epoch milliseconds, like Date.parse. |
| `usageScanCache.ts:91–366` | `usageScanCache.py` | Maps/Sets become ordered dicts/sets. The INVALID sentinel distinguishes corrupt Codex state from a valid null state. Version 3 and interned row fields remain unchanged. |
| `usageAggregation.ts:27–231` | `usageAggregation.py` | Intl.DateTimeFormat becomes zoneinfo, with the same UTC fallback. Exact hourly anchors and ordering remain. |
| `usagePricing.ts:35–216` | `usagePricing.py` | Maps become dictionaries. Exact trimmed override keys and normalized public keys remain separate. |

## Approved functional extension

The reader optionally emits selected allowance observations and its resume decision. It passes only timestamps, event type, provisional status and whitelisted rate-limit fields. It never passes message, tool, content or credential fields. This is the approved local allowance extension, not a token-parser change.

`UsageService` calls the adapter from the same inventory and stream. An unchanged token entry reuses the companion allowance cache. Missing or mismatched companion data receives an adapter-only full read without replacement of the token cache. Append reuse checks the companion against the previous token-cache size and modification time through `canResume`. The adapter owns persistence and converters in separate modules.

## Verification and test translation

All 73 source test cases have one translated Python case. Each Python docstring preserves the exact upstream title. Inputs and assertions remain equivalent. Effect scheduling and clocks become asyncio scheduling and an injected clock. The cancellation case retains all 31 candidates. No new browser host is added.

The test risk is replacement of the former ingestion algorithms. Existing tests asserted different behavior and could not prove the required T3 behavior. These translated upstream cases prove reducer attribution/deduplication, resume/tails, cache corruption/pruning, price aliases/overrides, time bounds and cancellation. They reuse pytest temporary directories and require no service or provider connection.

The focused run passed 73 tests in 9.24 seconds. One existing PreviewClientSession deprecation warning remained. Ruff passes all six modules and their test files. Mypy passes all six modules. Follow-up checks follow the final source revision.

## Source-to-destination function map

### UsageService

| Upstream symbol | Source line | Destination line |
|---|---:|---:|
| `UsageService` | 99 | 43 |

| Upstream case title | Source line | Python case and line |
|---|---:|---|
| reprices unchanged transcripts when custom prices are added, edited, or removed | 101 | `test_UsageService.py:test_reprices_unchanged_transcripts_when_custom_prices_are_added_edited_or_removed:63` |
| counts appended usage on a rescan of a grown transcript | 144 | `test_UsageService.py:test_counts_appended_usage_on_a_rescan_of_a_grown_transcript:94` |
| does not share an in-flight scan after custom prices change | 162 | `test_UsageService.py:test_does_not_share_an_in_flight_scan_after_custom_prices_change:110` |
| shares one scan between concurrent identical requests | 221 | `test_UsageService.py:test_shares_one_scan_between_concurrent_identical_requests:151` |
| refetches a rate table inside its TTL only when the client asks | 253 | `test_UsageService.py:test_refetches_a_rate_table_inside_its_TTL_only_when_the_client_asks:175` |
| does not orphan an in-flight scan when its first caller is interrupted | 295 | `test_UsageService.py:test_does_not_orphan_an_in_flight_scan_when_its_first_caller_is_interrupted:203` |

### usageTranscriptReader

| Upstream symbol | Source line | Destination line |
|---|---:|---:|
| `fnv1a` | 80 | 29 |
| `listTranscriptFiles` | 100 | 36 |
| `readDirectoryVolumeId` | 148 | 70 |
| `guardMatches` | 157 | 78 |
| `readTranscriptRecords` | 193 | 159 |

| Upstream case title | Source line | Python case and line |
|---|---:|---|
| parses only appended lines when resuming a grown file | 64 | `test_usageTranscriptReader.py:test_parses_only_appended_lines_when_resuming_a_grown_file:75` |
| carries the Codex reducer state across the resume boundary | 85 | `test_usageTranscriptReader.py:test_carries_the_Codex_reducer_state_across_the_resume_boundary:94` |
| suppresses a Codex duplicate usage event that straddles the boundary | 103 | `test_usageTranscriptReader.py:test_suppresses_a_Codex_duplicate_usage_event_that_straddles_the_boundary:110` |
| defers an unterminated trailing line to tailRecords, then consumes it once terminated | 125 | `test_usageTranscriptReader.py:test_defers_an_unterminated_trailing_line_to_tailRecords_then_consumes_it_once_terminated:126` |
| re-parses from the start when the guard bytes no longer match | 148 | `test_usageTranscriptReader.py:test_re_parses_from_the_start_when_the_guard_bytes_no_longer_match:145` |
| re-parses from the start when the file shrank below the resume point | 165 | `test_usageTranscriptReader.py:test_re_parses_from_the_start_when_the_file_shrank_below_the_resume_point:158` |
| parses a line larger than one stream chunk | 181 | `test_usageTranscriptReader.py:test_parses_a_line_larger_than_one_stream_chunk:173` |
| returns null for an unreadable file | 207 | `test_usageTranscriptReader.py:test_returns_null_for_an_unreadable_file:199` |

### usageTranscripts

| Upstream symbol | Source line | Destination line |
|---|---:|---:|
| `parseTimestampMs` | 37 | 41 |
| `addTotals` | 43 | 53 |
| `totalTokens` | 53 | 57 |
| `mightCarryUsage` | 70 | 61 |
| `grokCostTicksToUsd` | 82 | 71 |
| `parseClaudeLine` | 99 | 75 |
| `initialCodexScanState` | 173 | 115 |
| `isForkedSessionMeta` | 194 | 126 |
| `parseCodexLine` | 213 | 139 |
| `readGrokUsageTotals` | 326 | 207 |
| `grokTotalsToUsage` | 342 | 227 |
| `parseGrokLine` | 369 | 239 |

| Upstream case title | Source line | Python case and line |
|---|---:|---|
| extracts token totals and a dedupe key | 40 | `test_usageTranscripts.py:test_extracts_token_totals_and_a_dedupe_key:104` |
| gives every content block of one message the same dedupe key | 56 | `test_usageTranscripts.py:test_gives_every_content_block_of_one_message_the_same_dedupe_key:120` |
| ignores records that are not assistant messages | 66 | `test_usageTranscripts.py:test_ignores_records_that_are_not_assistant_messages:128` |
| attributes usage to the model from the preceding turn context | 101 | `test_usageTranscripts.py:test_attributes_usage_to_the_model_from_the_preceding_turn_context:134` |
| skips a repeated token_count so deltas are not double counted | 116 | `test_usageTranscripts.py:test_skips_a_repeated_token_count_so_deltas_are_not_double_counted:148` |
| drops usage that arrives before any model is known | 126 | `test_usageTranscripts.py:test_drops_usage_that_arrives_before_any_model_is_known:156` |
| does not let a pre-model event poison the duplicate signature | 131 | `test_usageTranscripts.py:test_does_not_let_a_pre_model_event_poison_the_duplicate_signature:161` |
| keeps the child session id over copied ancestor metas | 174 | `test_usageTranscripts.py:test_keeps_the_child_session_id_over_copied_ancestor_metas:169` |
| drops the re-stamped copied burst and keeps the first real event | 184 | `test_usageTranscripts.py:test_drops_the_re_stamped_copied_burst_and_keeps_the_first_real_event:178` |
| recognizes subagent spawns without forked_from_id | 215 | `test_usageTranscripts.py:test_recognizes_subagent_spawns_without_forked_from_id:202` |
| does not suppress anything in a rollout that is not a fork | 228 | `test_usageTranscripts.py:test_does_not_suppress_anything_in_a_rollout_that_is_not_a_fork:214` |
| does not add reasoning on top of output | 242 | `test_usageTranscripts.py:test_does_not_add_reasoning_on_top_of_output:225` |
| extracts per-model totals and provider-reported cost ticks | 309 | `test_usageTranscripts.py:test_extracts_per_model_totals_and_provider_reported_cost_ticks:293` |
| emits one record per model when modelUsage has several entries | 329 | `test_usageTranscripts.py:test_emits_one_record_per_model_when_modelUsage_has_several_entries:315` |
| inherits top-level cost ticks for a single model without its own ticks | 362 | `test_usageTranscripts.py:test_inherits_top_level_cost_ticks_for_a_single_model_without_its_own_ticks:344` |
| falls back to a generic grok model when modelUsage is absent | 381 | `test_usageTranscripts.py:test_falls_back_to_a_generic_grok_model_when_modelUsage_is_absent:360` |
| pro-rates top-level cost ticks across multi-model turns without per-model ticks | 399 | `test_usageTranscripts.py:test_pro_rates_top_level_cost_ticks_across_multi_model_turns_without_per_model_ticks:380` |
| pro-rates aggregate cost when a zero-token sibling carries costUsdTicks: 0 | 430 | `test_usageTranscripts.py:test_pro_rates_aggregate_cost_when_a_zero_token_sibling_carries_costUsdTicks_0:406` |
| allocates leftover aggregate ticks to models that omit per-model ticks | 469 | `test_usageTranscripts.py:test_allocates_leftover_aggregate_ticks_to_models_that_omit_per_model_ticks:440` |
| does not invent a colliding dedupe key when prompt_id is missing | 501 | `test_usageTranscripts.py:test_does_not_invent_a_colliding_dedupe_key_when_prompt_id_is_missing:470` |
| ignores non-turn lines and empty usage | 523 | `test_usageTranscripts.py:test_ignores_non_turn_lines_and_empty_usage:492` |
| falls back to the outer unix-seconds timestamp when agent meta is missing | 543 | `test_usageTranscripts.py:test_falls_back_to_the_outer_unix_seconds_timestamp_when_agent_meta_is_missing:514` |

### usageScanCache

| Upstream symbol | Source line | Destination line |
|---|---:|---:|
| `encodeScanCache` | 89 | 20 |
| `decodeCodexState` | 273 | 66 |
| `decodeScanCache` | 145 | 101 |
| `pruneScanCache` | 323 | 221 |
| `dedupeWithinFile` | 353 | 246 |

| Upstream case title | Source line | Python case and line |
|---|---:|---|
| restores records unchanged | 58 | `test_usageScanCache.py:test_restores_records_unchanged:57` |
| drops an entry whose persisted parse state is corrupt | 100 | `test_usageScanCache.py:test_drops_an_entry_whose_persisted_parse_state_is_corrupt:96` |
| drops an entry whose guard length is outside the supported range | 114 | `test_usageScanCache.py:test_drops_an_entry_whose_guard_length_is_outside_the_supported_range:103` |
| rejects a document from the previous cache version | 126 | `test_usageScanCache.py:test_rejects_a_document_from_the_previous_cache_version:110` |
| interns repeated model and session strings | 133 | `test_usageScanCache.py:test_interns_repeated_model_and_session_strings:117` |
| treats a corrupt or foreign document as an empty cache | 142 | `test_usageScanCache.py:test_treats_a_corrupt_or_foreign_document_as_an_empty_cache:126` |
| skips malformed file entries but keeps good ones | 149 | `test_usageScanCache.py:test_skips_malformed_file_entries_but_keeps_good_ones:132` |
| rejects the whole cache when an intern table holds a non-string | 160 | `test_usageScanCache.py:test_rejects_the_whole_cache_when_an_intern_table_holds_a_non_string:139` |
| drops the whole entry when any row is corrupt, forcing a cold re-parse | 169 | `test_usageScanCache.py:test_drops_the_whole_entry_when_any_row_is_corrupt_forcing_a_cold_re_parse:146` |
| drops entries older than retention | 194 | `test_usageScanCache.py:test_drops_entries_older_than_retention:155` |
| drops in-window entries whose file has disappeared | 208 | `test_usageScanCache.py:test_drops_in_window_entries_whose_file_has_disappeared:165` |
| keeps entries outside the walked window that are still within retention | 221 | `test_usageScanCache.py:test_keeps_entries_outside_the_walked_window_that_are_still_within_retention:174` |
| keeps entries the walk saw | 237 | `test_usageScanCache.py:test_keeps_entries_the_walk_saw:184` |
| keeps in-window entries for a provider whose directory was not walked | 252 | `test_usageScanCache.py:test_keeps_in_window_entries_for_a_provider_whose_directory_was_not_walked:196` |
| keeps entries under a sibling path that only shares the walked root prefix | 268 | `test_usageScanCache.py:test_keeps_entries_under_a_sibling_path_that_only_shares_the_walked_root_prefix:212` |
| keeps the first record per dedupe key | 284 | `test_usageScanCache.py:test_keeps_the_first_record_per_dedupe_key:228` |
| keeps every record that has no dedupe key | 295 | `test_usageScanCache.py:test_keeps_every_record_that_has_no_dedupe_key:241` |

### usageAggregation

| Upstream symbol | Source line | Destination line |
|---|---:|---:|
| `makeDayFormatter` | 26 | 20 |
| `UsageAggregator` | 85 | 38 |
| `resolveCostSource` | 227 | 150 |

| Upstream case title | Source line | Python case and line |
|---|---:|---|
| requires exact bounds for hourly aggregation | 64 | `test_usageAggregation.py:test_requires_exact_bounds_for_hourly_aggregation:67` |
| keeps only the first record for a repeated dedupe key | 77 | `test_usageAggregation.py:test_keeps_only_the_first_record_for_a_repeated_dedupe_key:81` |
| still sums records that carry no dedupe key | 90 | `test_usageAggregation.py:test_still_sums_records_that_carry_no_dedupe_key:90` |
| buckets by the day in the requested time zone | 97 | `test_usageAggregation.py:test_buckets_by_the_day_in_the_requested_time_zone:97` |
| splits an hourly request into fixed buckets anchored to its exact start | 105 | `test_usageAggregation.py:test_splits_an_hourly_request_into_fixed_buckets_anchored_to_its_exact_start:103` |
| uses an inclusive start and exclusive end for rolling windows | 121 | `test_usageAggregation.py:test_uses_an_inclusive_start_and_exclusive_end_for_rolling_windows:119` |
| keeps daily payloads collapsed when hourly resolution is not requested | 140 | `test_usageAggregation.py:test_keeps_daily_payloads_collapsed_when_hourly_resolution_is_not_requested:141` |
| prices against the rate table | 151 | `test_usageAggregation.py:test_prices_against_the_rate_table:154` |
| counts tokens but not cost for a model with no rate | 159 | `test_usageAggregation.py:test_counts_tokens_but_not_cost_for_a_model_with_no_rate:161` |
| prefers a reported cost over the rate table | 168 | `test_usageAggregation.py:test_prefers_a_reported_cost_over_the_rate_table:170` |
| drops records outside the window | 175 | `test_usageAggregation.py:test_drops_records_outside_the_window:177` |
| reports whether a record contributed | 182 | `test_usageAggregation.py:test_reports_whether_a_record_contributed:184` |
| separates providers and models into their own buckets | 195 | `test_usageAggregation.py:test_separates_providers_and_models_into_their_own_buckets:194` |

### usagePricing

| Upstream symbol | Source line | Destination line |
|---|---:|---:|
| `createOverrideRateTable` | 34 | 16 |
| `parseRateTable` | 74 | 38 |
| `normalizeRateKey` | 126 | 77 |
| `bareModelName` | 130 | 81 |
| `stripVariantSuffix` | 140 | 85 |
| `lookupRate` | 161 | 89 |
| `priceUsage` | 179 | 97 |
| `cacheSavingsUsd` | 207 | 119 |

| Upstream case title | Source line | Python case and line |
|---|---:|---|
| uses custom token rates ahead of public and provider-reported costs | 26 | `test_usagePricing.py:test_uses_custom_token_rates_ahead_of_public_and_provider_reported_costs:28` |
| prices unknown models offline and uses input prices for omitted cache rates | 46 | `test_usagePricing.py:test_prices_unknown_models_offline_and_uses_input_prices_for_omitted_cache_rates:48` |
| preserves explicit zero rates and matches only the exact trimmed model ID | 59 | `test_usagePricing.py:test_preserves_explicit_zero_rates_and_matches_only_the_exact_trimmed_model_ID:60` |
| keeps the canonical Fable rate separate from DeepInfra in either order | 85 | `test_usagePricing.py:test_keeps_the_canonical_Fable_rate_separate_from_DeepInfra_in_either_order:85` |
| prices a bracketed context-tier variant at the base model's rate | 103 | `test_usagePricing.py:test_prices_a_bracketed_context_tier_variant_at_the_base_models_rate:98` |
| adds a bare alias when every qualified entry has the same rate | 112 | `test_usagePricing.py:test_adds_a_bare_alias_when_every_qualified_entry_has_the_same_rate:105` |
| leaves an ambiguous bare name unpriced | 123 | `test_usagePricing.py:test_leaves_an_ambiguous_bare_name_unpriced:113` |
