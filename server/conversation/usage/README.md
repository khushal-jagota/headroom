# Recorded usage

This area reads local native transcripts and returns priced usage buckets. It
keeps the existing Python contract version 5 and scan cache version 3. The browser
DTO types mechanically copy the existing frontend contracts. Their extensionless
`./index` imports require bundler resolution and fail the server's NodeNext check,
so the server keeps only the required DTOs locally with the same shapes and brands.
The global resolver and frontend files stay unchanged. Provider allowance reads
reuse the backend-state observations rather than acquire new provider limits.

The original TypeScript comes from T3 Code commit
`75e4ceb96489a2fe92c710c79dbea59778cccfd1`. Copyright (c) 2026 T3 Tools Inc.
The full MIT copyright and permission notice is retained in [T3-LICENSE](T3-LICENSE).

## Dependency boundaries

- `usageTranscripts.ts` contains line reducers and their record/state types.
- `usagePricing.ts` prices those records from public rates and explicit overrides.
- `usageAggregation.ts` groups records by day or exact hourly anchors and applies
  global first-copy deduplication. `timeZone.ts` validates installed, case-sensitive
  ZoneInfo keys and follows canonical symlink targets for ICU formatters.
- `usageTranscriptReader.ts` owns asynchronous file access, native directory
  order, byte offsets, provisional tails, and the 64-byte FNV resume guard.
- `usageScanCache.ts` owns the compact versioned cache and pruning. It performs
  no filesystem or network operations itself.
- `UsageService.ts` owns the shared scan, rate fetch/cache policy, and persistence.
  The service receives its transcript directories, clock, settings, and rate
  fetcher as dependencies. `routes.ts` consumes only that service and backend state.

The public runtime interface is `RecordedUsageService`: `readSummary`,
`refreshRates`, and `finish_operations`. The concrete constructor keeps the Python
shape `UsageService(stateDir, transcriptDirs, options)`. Defaults and limits live
in `configuration.ts`. No Effect runtime or provider adapter is required.

## Adaptations for the current Python implementation

The copied modules retain their original responsibilities and Map-based
contracts. These language-boundary changes preserve Python behavior:

- Timestamp intake uses the shared ISO parser, with millisecond truncation. It
  rejects invalid calendar values that JavaScript's `Date.parse` normalizes.
- Codex parsing retains native JSON integer and float kinds in its duplicate
  signature. Token arithmetic projects only the consumed numeric values.
- Object checks reject arrays wherever Python requires a dictionary. A valid
  empty Claude usage object still produces its zero-total record.
- Directory walks use `opendir` iteration to retain native `os.scandir` order.
  First-copy deduplication therefore uses the same source order as Python.
- Bigint file metadata preserves device and inode identities. Modification time
  converts the integer quotient and fractional remainder separately, avoiding an
  early rounded conversion of nanoseconds.
- Bucket order uses Unicode code points rather than locale collation. Model
  normalization uses Python's whitespace set.
- Database paths resolve filesystem symlinks before composition chooses the cache
  parent. Missing path tails retain Python's non-strict resolution behavior.
- Persisted cache documents use strict UTF-8 decoding and reject text BOMs.
  Transcript decoding retains Python's replacement behavior.
- Installed timezone aliases such as `posixrules` use their canonical zone target.
  An installed key without an ICU mapping raises an explicit error. It never
  silently substitutes UTC or accepts an invalid key through ICU normalization.

These are parity adaptations, not additional usage policies. Later upstream
provider types, compressed transcripts, cache versions, context tiers, and speed
pricing do not enter this port.

## Safety and evidence

Production composition alone resolves configured provider homes. Test composition
uses explicit synthetic directories and recording rate fetchers. The production
rates transport refuses test contexts. Real provider transcripts, authentication
files, provider CLIs, and billing endpoints do not participate in local proof.

Direct reader/cache comparisons use tiny source files and hash only the 64-byte
guard. The HTTP proof uses the ordinary app adapters with the actual service and
recording dependencies. It compares populated summaries, saved limits, and rate
refresh answers. Focused service tests cover concurrency and retained scan
lifetime. Verification results belong to the Ticket evidence after those checks
run. No browser host is needed for these server boundaries.
