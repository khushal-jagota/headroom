# Recorded usage

This area reads local native transcripts and returns priced usage buckets. It
keeps contract version 5 and scan cache version 5. The browser
DTO types mechanically copy the existing frontend contracts. Their extensionless
`./index` imports require bundler resolution and fail the server's NodeNext check,
so the server keeps only the required DTOs locally with the same shapes and brands.
The global resolver and frontend files stay unchanged. Provider allowance reads
reuse the backend-state observations rather than acquire new provider limits.

The original TypeScript comes from T3 Code commit
`75e4ceb96489a2fe92c710c79dbea59778cccfd1`. Copyright (c) 2026 T3 Tools Inc.
The full MIT copyright and permission notice is retained in [T3-LICENSE](T3-LICENSE).

## Dependency boundaries

- `usageTranscripts.ts` holds the usage record and the helpers parsers share.
  Each driver's transcript parser lives in its own package, as
  `drivers/<kind>/runtime/transcript_parser.ts`, and its `transcript_usage.ts`
  names the homes it reads. `grokTranscriptParser.ts` reads the owner's own Grok
  CLI transcripts: Grok is a usage source, not a driver.
- A source is `lines` or `files`. A line parser carries state from line to line
  and across scans. A whole-file parser lists its own files with a fingerprint
  each, and a file is read again only when its fingerprint changes. The parser
  then finishes every listed file's records together, cached or fresh, because
  one file's records can depend on another's. Antigravity lists only `.db`
  conversations, keeps a database whose `-wal` log changed in the window, and
  names a model id from any listed database. The usage worker cannot receive
  functions, so each directory names its parser module, which the worker imports.
- `usagePricing.ts` prices those records from public rates and explicit overrides,
  by a record's `pricing_model` when it has one and by its model otherwise.
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
`refreshRates`, and `finish_operations`. The concrete constructor is
`UsageService(stateDir, transcriptDirs, options)`. Defaults and limits live
in `configuration.ts`. No Effect runtime or provider adapter is required.

`finish_operations` drains admitted operations and unreferences an idle worker
without discarding its cache. `stop({deadline})` seals admission, aborts injected
rate work, and terminates and joins the worker within the shutdown budget.

## Adaptations to the copied modules

The copied modules retain their original responsibilities and Map-based
contracts. These changes keep the existing Panels usage behavior:

- Timestamp intake uses the shared ISO parser, with millisecond truncation. It
  rejects invalid calendar values that JavaScript's `Date.parse` normalizes.
- Codex parsing retains native JSON integer and float kinds in its duplicate
  signature. Token arithmetic projects only the consumed numeric values.
- Object checks reject arrays wherever an object is required. A valid
  empty Claude usage object still produces its zero-total record.
- Directory walks use `opendir` iteration to retain native directory order.
  First-copy deduplication follows that source order.
- Bigint file metadata preserves device and inode identities. Modification time
  converts the integer quotient and fractional remainder separately, avoiding an
  early rounded conversion of nanoseconds.
- Bucket order uses Unicode code points rather than locale collation. Model
  normalization uses a fixed whitespace set.
- Database paths resolve filesystem symlinks before composition chooses the cache
  parent through `core/path_resolution.ts`. Missing path tails resolve
  non-strictly. A DB-file symlink places `usage` beside its target.
- Transcript home paths retain lexical `..` segments. They do not use
  Node's collapsing path join before source fingerprints and filesystem reads.
- Persisted cache documents use strict UTF-8 decoding and reject text BOMs.
  Transcript decoding replaces invalid bytes.
- Installed timezone aliases such as `posixrules` use their canonical zone target.
  An installed key without an ICU mapping uses UTC under the accepted zone-data
  deviation. Invalid or uninstalled HTTP keys still answer 422. This fallback
  ignores a custom zone file's real offset.

Request pricing selects long-context rates above 272,000 prompt tokens and retains
Claude one-hour cache-write tokens. Codex compaction records count once, with
covered identities reserved across all files before date filtering. Cache version 5
retains pending compactions, service tiers, and cache-write duration metadata.

These are adaptations, not additional usage policies. Later upstream provider
types, compressed transcripts, and speed pricing are not part of this copy.

## Safety and evidence

Production composition reads the transcript sources each driver names, through
`driverTranscriptSources.ts`. Every joined machine composes the same service over
its own homes. `GET /summary?machine_id=` relays a `read_usage_summary` machine
control to that machine, which answers with its aggregated summary only. The
machine advertises the operation in its lease; older machine code answers 503.
The browser merges the machine summaries. Test composition maps each source to a synthetic directory, and uses recording
rate fetchers. The production
rates transport refuses test contexts. Real provider transcripts, authentication
files, provider CLIs, and billing endpoints do not participate in local proof.

The rates transport follows the usual scheme-specific proxy environment
selection, lowercase precedence, CGI exclusion for uppercase `HTTP_PROXY`, and
`no_proxy` suffix bypass. HTTPS tunnels use `https-proxy-agent` through HTTP or
HTTPS proxies. Each redirect selects the proxy again. `/limits` requires a conversation
runtime and answers 503 when it is absent. Summary and rates refresh remain local
service routes independent of that runtime.
