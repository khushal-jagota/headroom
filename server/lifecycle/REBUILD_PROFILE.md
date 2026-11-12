# Rebuild baseline and profile

Matching Python-written version-4 projections do not need a TS replay. The reuse check preserves the exact projection digest and replays zero events. Record initialization takes 7.827 ms.

For the missing-view case, Python makes all 200,000 events current in 94.474 seconds on the same VPS. Its second-connection task finishes in 96.786 seconds, including final pacing. The earlier plain TS measurement takes 373.741 seconds. The new instrumented TS run takes 370.301 seconds. Instrumentation and different SQLite versions prevent a precise speed-ratio claim.

The TS profile shows repeated blocking: all 400 replay batches exceed 100 ms, and 19 exceed 500 ms. Median batch time is 416.726 ms. This is not an isolated final-install stall. The exact earlier 917.5 ms sample cannot be assigned retroactively.

| Profile category | Time |
| --- | --- |
| SQL read methods, including preparation and materialization | 103.156 s |
| SQL write methods | 50.923 s |
| JS codec/reduction/control and instrumentation | 21.739 s |
| Database scope overhead | 1.885 s |
| Yield, pacing, and other outside-scope control | 192.598 s |

The last category is not pure idle time. Replay uses 168.890 seconds of synchronous database scopes. The 20 installations use 2.296 seconds. No optimization accompanies this evidence.

Both languages queue a rebuild only when a conversation has events and its view state is missing or has the wrong projection version. A coverage lag at the same version is a separate invariant failure. This note does not inspect the live database or establish its version distribution.

During replay, snapshot, receipt, and tail reads return 409 for an upgrading projection. Page reads can still return 200 with empty or old rows. Body reads follow reference-specific validation. Other routes remain available between synchronous batches, but these measurements do not establish concurrent full-server latency.

Navigation initialization takes 89.729 ms on this same event fixture with current views. It contains 20 standalone conversations and no linked Tickets, Managers, Projects, or agents. This is not a populated navigation workload.

Fresh TS replay and Python produce the same exact digest across the five projection tables. Every completed mode preserves the canonical event digest. Initial Python attempts failed because the literal-schema fixture omitted migration singleton rows. The successful copies add only those declared prerequisites before timing. Failed-attempt summaries remain in the evidence below.

[Raw reports](evidence/rebuild-profile/) include the Python baseline, TS phase profile, reuse check, navigation sequence, stale-route answers, and failed-attempt summaries. The measurement drivers are explicit-only and add no default CI case. Independent review found no material timing or isolation error.

Reproduction requires a **fresh, empty `--compare-report-dir`**. The cases share their prepared data through that directory. A bare invocation uses separate temporary directories and is unsupported. An existing result directory refuses overwrite. Use scrubbed configuration and private provider homes before repository execution.

```sh
scripts/dev pytest tests/compare/direct/unit21_rebuild_profile.py -s --compare-report-dir /absolute/private/new-profile
```

<details>
<summary>Full methods, route behavior, setup correction, and limits</summary>

# When the conversation projection rebuilds

Python and TS use projection version 4. Both startup selectors require `latest_sequence > 0` and either a missing view-state row or a different `projection_version`.

The selectors do not check `through_sequence`. A current-version state row whose coverage trails the event sequence does not enter this rebuild queue. The selected-view coverage reader instead raises `Conversation projection is behind its events` for that invariant failure.

A first TS start on complete Python-written version-4 views reuses them. The explicit TS reuse probe confirms zero replayed events, identical view-row digests before and after initialization, and the same canonical-event digest. It initializes the record in 7.827 ms. It does not need the six-minute missing-view rebuild from the earlier measurement.

A projection-version bump or missing view rows can require the full rebuild. Conversations with zero events do not enter the queue. Startup starts a background rebuild task instead of awaiting all views. Each finished conversation installs all five projection tables atomically. Readers cannot see the private TEMP tables halfway through their reconstruction.

Source: `src/planner/conversation/views/rebuild.py::_OUT_OF_DATE`, `server/conversation/views/data/rebuild.ts::OUT_OF_DATE`, and the record runtime startup composition.

# What answers during the rebuild

The listener and unrelated routes are not withheld until all views are current. The original normal-main test-mode startup reaches health/meta/root readiness in 5.541 seconds on the 200,000-event fixture, before complete replay. This timing includes schema bootstrap and source copy. Rebuild is not an admitted workspace operation and does not make the workspace look busy for update drain.

TS uses its single writer on the event loop. Unrelated routes can therefore wait behind a synchronous replay batch or installation. The new profile reports repeated batch and installation distributions instead of treating the previous 917.5 ms maximum as one known isolated stall. Python computes private views in a worker thread on a second connection.

The routes have different gates. It is incorrect to say that all six selected-view routes return 409 while the view is stale.

| Selected-view route | Missing or wrong-version view behavior |
| --- | --- |
| Snapshot `/view` | 409, `Conversation view is upgrading` |
| Receipts `/view/receipts` | 409 through the coverage gate |
| Tail `/view/tail` | 409 before opening SSE, through the coverage gate |
| Pages `/view/pages` | No view-state coverage gate. Missing rows can return a 200 empty page. Old rows can remain readable until atomic replacement. A mismatched cursor can separately return 409. |
| Body `/view/body` | No blanket upgrading gate. A missing referenced row returns 422. A changed body reference returns 409. Valid live or plan references can remain readable. |
| Content `/view/body/content` | The same reference-specific behavior as body reads, with streamed content on success. |

The direct Python production-function probe on the same 200,000-event input confirms snapshot/receipts/tail=409, pages=200 with zero items, and body/content=422 for an absent row reference. It costs 5.23 ms of calls and 2.69 seconds of pytest startup/teardown. It adds no HTTP host or production provider. TS route behavior in this table comes from the matching production coverage, page, and body handlers; this probe does not claim a new HTTP observation of all TS routes.

Source: Python `conversation/views/api.py::_coverage`, snapshot, tail, receipts, page/body handlers; TS `server/conversation/views/routes.ts::view_coverage` and corresponding handlers. Page storage in both languages does not check stored projection version.

# Baseline and setup limits

The completed Python baseline uses the production `rebuild_out_of_date_views` coroutine. It keeps the application connection open and lets the production function create its private second connection in `asyncio.to_thread`. All 20 views become current in 94.474 seconds. The task completes in 96.786 seconds, including 2.312 seconds of final pacing. Process CPU time is 49.421 seconds. The 5 ms heartbeat's maximum gap is 19.814 ms.

The runtime is Python 3.13.5 and SQLite 3.46.1 on the same VPS. The prior plain TS result uses Node 22.23.1 and SQLite 3.54.1. The new TS phase profile adds clocks and SQL bookkeeping, so its instrumented wall time is not a clean speed ratio against uninstrumented Python.

The original schema-only fixture extracted table statements but omitted migration-owned singleton rows. Production Python commit participants require `navigation_meta`; the initial Python attempts could not install views. The corrected measurement copies add the exact `chief_page` and `navigation_meta` initialization from the baseline migration before timing. The original event source stays unchanged. Both failed attempts, their databases, and the concrete exception trace remain in the report directory.

The canonical event digest is `0fba82ebd75a85717f05cc3a9425df8b78c5627cafa142ecd6a6cd11b3de16db` before and after all completed modes. The event input contains 20 conversations, 40,000 turns, and 200,000 events. Every completed projection contains 20 states, 160,000 rows, and 40,000 turns, groups, and lookups each.

# Where the TS run spends its time

The instrumented run takes 370.301 seconds until all views are current. It replays all 200,000 events and preserves the canonical event digest. The measurement does not optimize the production writer or rebuilder.

| Phase category | Wall contribution | Meaning |
| --- | --- | --- |
| SQLite read methods | 103.156 s | Driver preparation, execution, and row materialization in get/all. |
| SQLite write methods | 50.923 s | Driver preparation and execution in run. |
| Reduction and callback control | 21.739 s | JS codec/reduction/control plus profiling instrumentation. This is not pure reducer time. |
| Database scope overhead | 1.885 s | BEGIN/COMMIT, write-door checks, and scope control outside the callback. |
| Yield, pacing, and outside-scope control | 192.598 s | Deliberate pacing, cooperative yields, timer scheduling, and other work outside database scopes. This is not pure idle time. |

The database scopes total 177.703 seconds. Replay batches account for 168.890 seconds of that total. The 20 final installation transactions total 2.296 seconds.

The 400 replay batches each exceed 100 ms. Their median is 416.726 ms, p95 is 495.448 ms, maximum is 625.473 ms, and 19 exceed 500 ms. These are repeated stalls, not one isolated long batch. The 20 installation transactions range up to 171.967 ms.

A 5 ms heartbeat records 413 gaps above 50 ms, 405 above 100 ms, and 104 above 500 ms. Its maximum gap is 984.700 ms. The event-loop histogram's maximum is 836.239 ms. The low histogram p99 includes many samples during pacing and does not mean that the repeated batch stalls disappear.

The phase probe wraps each SQL method with clocks and bookkeeping. Its wall time includes that overhead. SQL method time combines native statement preparation, execution, and materialization. Callback residual includes the profiler. No optimization decision rests on an exact pure-reducer percentage. The raw report includes slow scopes, query totals, and these explicit limits.

# Navigation startup on the same synthetic event data

The navigation probe copies Python's current version-4 projections, then runs the real areas prepare/initialize, Signal initialization, and navigation attachment with the area's actual commit steps. It does not concurrently replay conversation views.

| Navigation phase | Time |
| --- | --- |
| Area prepare | 1.759 ms |
| Area initialize, with current conversation views | 47.861 ms |
| Signal source initialize | 6.867 ms |
| Navigation attach and initialize | 32.516 ms |
| Total measured sequence | 89.729 ms |

The source has 200,000 conversation events and 20 standalone conversations, but zero Tickets, Managers, Projects, agents, or linked conversations. The output has 23 navigation records, 20 Signal publications, and zero ordered members. This measures startup on this exact large-event fixture. It does not establish startup cost on a populated application navigation workload.

All measurements run sequentially on the same VPS. Python finishes before the TS profile starts. No live database, files folder, or provider enters either run. Reports retain the failed setup attempts and the completed raw JSON. No service or child process remains.

</details>
