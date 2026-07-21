# ACP-05 compaction capture deadline implementation report

Status: **P1 + CORRECTION CHECK ADDRESSED — READY FOR REAL HERMES RETEST**

## Implemented

- Added the named `ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS = 300` production default and
  kept `ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS = 10` unchanged.
- The broker actor still mints one absolute monotonic deadline. It passes that exact value through hub
  admission, official fork/private replay, normalization, durable settlement, actor rekey, browser
  commit, and transition completion. The configured duration travels separately only as diagnostic
  context and never creates another wait budget.
- `ConversationCompactionCaptureDeadlineExpired` records the exact phase, configured budget, durable
  binding disposition, original generation, and whether the runtime is generation-fatal.
- Registry expiry now distinguishes hub admission, fork, private fork load, durable CAS/resolve,
  original restore, and winner adoption. Actor-owned expiry distinguishes actor rekey and browser
  commit. Normalization retains its own phase even when the required same-deadline restore proves the
  child uncertain.
- Durable disposition is truthful and three-way: stayed on N, committed N+1, or unresolved inside the
  expired budget. Every uncertain-child outcome invalidates that exact runtime; unresolved state is
  read authoritatively only on ordinary fresh attach.
- The broker publishes the exact deadline reason to the failed compaction boundary and tracked result,
  logs the same reason, and reuses the expired absolute deadline for fatal settlement. It does not
  retry capture or mint rollback/cleanup time.
- Immediate non-timeout failures use the same exact phase vocabulary and preserve the concrete
  exception type plus its nonblank message (or the type alone when blank). Hub admission, fork/private
  load, normalization, CAS/resolve, original restore, winner adoption, actor rekey, and browser commit
  now reach the tracked result, failed compaction boundary, and server log without generic capture or
  generation text.
- When normalization/private-load/CAS failure and its required same-deadline restore or abort both
  fail, the visible reason retains both concrete causes. CAS settlement remains its own owner: once
  commit begins, the proxy does not perform a false second abort, so a restored original generation
  remains usable and queued work can continue.
- `ConversationWaitDeadlineExpired` is the internal timer-won signal shared by the registry, strategy
  proxy, and actor wait helpers. It subclasses `TimeoutError` so unrelated existing timeout handling is
  unchanged, while compaction deadline conversion catches only this narrow type. An operation that
  completes by raising its own `TimeoutError` is therefore an immediate phase-and-cause failure, not a
  false five-minute expiry.

## Red/green proof

- The configuration regression first failed import because no dedicated capture constant existed; it
  now proves 300 seconds, the unchanged 10-second shutdown value, and the broker constructor default.
- The private-load expiry regression first received the old generic generation-fatal text; it now
  names `private fork load`, `0.02-second`, stayed generation 1, child invalidation, and fresh-load
  recovery from the unchanged durable binding.
- The CAS expiry regression first continued into a post-timeout resolve/restore path and returned a
  recoverable CAS failure. It now stops at the one deadline, reports `durable CAS/resolve` with an
  unresolved N/N+1 disposition, invalidates the child, releases the gate, and recovers by fresh attach.
- Existing hub-commit expiry now proves `browser commit`, the injected `0.02-second` budget, committed
  generation 2, exact tracked/boundary/log text, and replacement-generation invalidation.
- The successful end-to-end broker/registry transaction advances a deterministic monotonic clock by
  four seconds at each of fork, private replay, durable CAS, and browser commit. Sixteen seconds is
  beyond the legacy shutdown budget and inside the five-minute capture budget; N→N+1 still commits and
  all owners observe the original actor deadline.
- P1 regressions first reproduced the opaque `Conversation context capture failed` and `Conversation
  runtime generation failed during compaction` results. They now prove immediate normalization
  provenance, paired normalization/CAS plus restore provenance, primary-timeout plus restore-failure
  provenance, visible boundary/tracked/log equality, and no false deadline wording for immediate
  backend errors. The successful N→N+1 adoption regression also guards single transition consumption.
- The correction-check regression first proved that a backend-raised `TimeoutError` with nearly 300
  seconds remaining was mislabeled as configured-budget expiry. It now surfaces immediately as
  `TimeoutError: backend private load timed out`, retains the failed restore cause, and contains no
  `exceeded its configured` wording. The adjacent real timer-expiry regression still produces the
  dedicated deadline exception and exact budget diagnostics.

## Verification

The exact commands and output are in `focused-checks.txt`. Scoped Ruff passed, strict Mypy passed on
all five affected source files, and all 134 named registry, broker, hub, composition, Hermes strategy,
and official-SDK conversation e2e tests passed. The existing Starlette/httpx deprecation warning is
unchanged. Canonical `./verify` was intentionally not run, and `web/dist` was not rebuilt or edited.

The Hermes checkout remained read-only.
