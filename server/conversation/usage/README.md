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
- `UsageService.ts` is the request and lifecycle facade. A lazy worker runs
  `UsageEngine.ts`, which owns scans, rate caches, aggregation, and persistence.
  Settings and directory callbacks stay on the main thread. Injected clocks and
  rate fetchers use small messages. Transcript records and caches stay in the worker.
  `routes.ts` consumes only the service and backend state.

The public runtime interface is `RecordedUsageService`: `readSummary`,
`refreshRates`, and `finish_operations`. The concrete constructor keeps the Python
shape `UsageService(stateDir, transcriptDirs, options)`. Defaults and limits live
in `configuration.ts`. No Effect runtime or provider adapter is required.

`finish_operations` drains admitted operations and unreferences an idle worker
without discarding its cache. `stop({deadline})` seals admission, aborts injected
rate work, and terminates and joins the worker within the shutdown budget.

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
  parent through `core/path_resolution.ts`. Missing path tails retain Python's
  non-strict resolution behavior. A DB-file symlink places `usage` beside its target.
- Transcript home paths retain pathlib's lexical `..` segments. They do not use
  Node's collapsing path join before source fingerprints and filesystem reads.
- Persisted cache documents use strict UTF-8 decoding and reject text BOMs.
  Transcript decoding retains Python's replacement behavior.
- Installed timezone aliases such as `posixrules` use their canonical zone target.
  An installed key without an ICU mapping uses UTC under the accepted zone-data
  deviation. Invalid or uninstalled HTTP keys still answer 422. A focused test
  uses one tiny public fixed-offset TZif file under a synthetic key to pin this
  distinction. Python uses that file's real offset, so this fallback is not
  described as exact custom-zone parity.

These are parity adaptations, not additional usage policies. Later upstream
provider types, compressed transcripts, cache versions, context tiers, and speed
pricing do not enter this port.

## Safety and evidence

Production composition alone resolves configured provider homes. Test composition
uses explicit synthetic directories and recording rate fetchers. The production
rates transport refuses test contexts. Real provider transcripts, authentication
files, provider CLIs, and billing endpoints do not participate in local proof.

The rates transport follows Linux urllib's scheme-specific proxy environment
selection, lowercase precedence, CGI exclusion for uppercase `HTTP_PROXY`, and
`no_proxy` suffix bypass. HTTPS tunnels use `https-proxy-agent` through HTTP or
HTTPS proxies. Each redirect selects the proxy again. Recording proofs exercise
the pure selector without a network connection. `/limits` requires a conversation
runtime and answers 503 when it is absent. Summary and rates refresh remain local
service routes independent of that runtime.

Direct reader/cache comparisons use tiny source files and hash only the 64-byte
guard. The HTTP proof uses the ordinary app adapters with the actual service and
recording dependencies. It compares populated summaries, saved limits, and rate
refresh answers. Focused service tests cover concurrency and retained scan
lifetime. Verification results belong to the Ticket evidence after those checks
run. No browser host is needed for these server boundaries.
