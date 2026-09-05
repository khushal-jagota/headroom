# Conversation snapshot, usage, and voice pruning cohort

## Plan before editing

Baseline collection is 125 cases: 61 snapshot, 33 usage, and 31 voice. This
cohort will retain roughly 55 cases and remove the rest as actual pytest cases.
It will not move cases into loops or touch production/provider code.

The snapshot file keeps one direct proof for each provider-specific card mapping:
Claude identity/catalog/per-model effort/default resolution, Codex identity/catalog/
per-model effort/default resolution, and Hermes configured-executable/catalog/default
resolution. It also keeps one malformed Hermes inventory case for each distinct
invariant family (row completeness, provider uniqueness, runnable-default validity),
the native Hermes update check/refusal/safe-command/lifecycle paths, Codex's native
and npm-prefix update paths, update serialization, cross-backend concurrency,
subprocess timeout/cancellation cleanup, and snapshot cache/explicit refresh behavior.
Install-path permutations, equivalent signed-out/unknown/missing/version cases,
published-version ordering permutations, repeated update outcome cases, and the
opt-in real-machine smoke are removed because they repeat the same card/update door
without protecting another provider translation, cache, or process boundary.

The usage file keeps fresh and stale Codex snapshot behavior, the exact isolated
refresh command, unavailable/failed/no-new-snapshot outcomes, one malformed Codex
event, both current and legacy Claude response mappings, alternate credential home,
privacy-safe rate-limit and unauthenticated results, one malformed credential, one
transport failure, one malformed provider payload, and per-backend refresh locking.
Boolean/value matrices and adjacent error permutations are removed after one value
continues to cross each parsing/error door.

The voice file keeps exact-whole hallucination filtering, phrase-containing speech,
edge trimming, real ffmpeg tone and unreadable-input behavior, silent-input provider
short-circuit, and the actual HTTP route boundaries for transient audio/no storage,
fresh-audio validation, provider failure, missing configuration, media allowlisting,
and byte ceiling. Phrase spelling/case matrices, a second direct silent-trim case,
the direct missing-key helper case, route-catalog absence, and a second successful
codec-qualified upload are removed because the retained cases exercise the same
decision or route path.

The narrow gate is collection plus pytest for exactly these three files. The final
evidence below will record exact post-edit counts and the full gate result.

## Result

Collection fell from 125 to 62 cases, removing 63 cases (50.4%):

| File | Before | After | Removed |
|---|---:|---:|---:|
| `test_conversation_snapshot.py` | 61 | 33 | 28 |
| `test_conversation_backend_usage.py` | 33 | 17 | 16 |
| `test_conversation_voice_transcription.py` | 31 | 12 | 19 |

The retained inventory still has separate provider card/default/model mappings,
three distinct Hermes inventory-invalidity families, native Hermes advisory and
update lifecycle coverage, Codex native/npm update targeting, snapshot caching,
refresh locking, cross-backend concurrency, and subprocess timeout/cancellation
cleanup. Usage retains fresh/stale/future Codex snapshots, the isolated refresh
command and outcomes, both Claude response shapes, credentials/privacy/error
mapping, malformed inputs, and per-backend serialization. Voice retains the filter,
real ffmpeg tone/invalid/silent behavior, provider short-circuit, and six HTTP ingress
risks.

On this macOS host the untouched baseline's three successful-ffmpeg-dependent cases
fail because production pins `/usr/bin/ffmpeg`, while Homebrew provides
`/opt/homebrew/bin/ffmpeg`. The retained real-process tests now resolve the installed
binary through `PATH` and patch only their test invocation. This lets the narrow gate
exercise ffmpeg's real output without changing production behavior.

Focused gate:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_snapshot.py tests/unit/test_conversation_backend_usage.py tests/unit/test_conversation_voice_transcription.py
..............................................................           [100%]
```

No production code, provider adapter, other test file, broad suite, or `./verify`
was changed or run by this cohort.
