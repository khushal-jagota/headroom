# Backend comparison

The runner sends one request list to Python and a candidate server on separate copies of one database. It compares responses, read probes, and source rows. Python is the reference.

## Add an area

Add two files with the same name:

- `tests/compare/lists/unit-NN.json`
- `tests/compare/rules/unit-NN.json`

The runner and CI discover these files without a registry or runner change. Use the `unit-*.json` naming convention. Put the area's Python source directories in `source_roots` for CI selection. Branch updates run every list.

Start the rule file from `rules/unit-01.json` to retain the shared metadata rules. Add only necessary area exceptions. Each exception must state its reason.

A list contains requests, captures, and read probes:

```json
{
  "source_roots": ["src/planner/example/"],
  "requests": [
    {
      "area": "example",
      "method": "POST",
      "path": "/api/example",
      "status": 200,
      "json": {"name": "Example"},
      "capture": {"example_id": "id"}
    },
    {
      "area": "example",
      "method": "GET",
      "path": "/api/example/${example_id}",
      "status": 200
    }
  ],
  "probes": ["/api/example"]
}
```

Replace the example routes with real area routes. Every request requires `status`. Both servers must return that status before their answers compare. Read probes require HTTP 200. Equal failures must not pass as successful calls.

Use `headers` for caller identity and other request headers. For refused calls, the caller must exist. Create or select the caller first, then use its id. A nonexistent caller proves rejection of a nonexistent caller, not the intended authority rule. Do not copy the placeholder caller ids from Unit 1 into new authority proofs.

Captures use dotted object paths and numeric array indices. An empty path captures the complete body. `${capture}` substitutes separately on each server. A complete-string substitution preserves the captured JSON type. Arrays retain order.

The rule file contains reason maps:

- `ignore`: exact `table.column` exceptions for source rows.
- `tokens`: exact `table.column` counters or digests compared by change.
- `response_ignore`: exact response paths, such as `body.events.*.created_at`.
- `response_tokens`: exact response paths for counters or digests compared by change.
- `derived_tables`: tables excluded from SQL comparison. Add probes for their public output.

The `*` response path segment denotes an array item. It is not an arbitrary wildcard. Do not hide content digests as random ids. Give each wall-time field an explicit rule and reason.

## Run a list

A list can set `"compare_skill_files": true` to compare the files beneath each
server database parent's `skills/` tree after its requests. The runner compares
relative filenames and exact bytes, including supporting files. Missing, extra,
or changed files appear in `differences` with the filename and each side's hex
bytes (`null` means absent). No normalization or rule applies to these bytes.
The report also lists the reference filenames in `skill_files`.

From the repository root:

```sh
scripts/dev pytest tests/compare --compare-list tests/compare/lists/unit-NN.json --compare-report-dir /tmp/panels-comparison
```

Repeat `--compare-list` to select several lists. Without that option, pytest discovers every list. Four cheap oracle tests also run. Each report contains differences, route reach, source table counts, loop results, and the applied rules.

## TypeScript host contract

Select the TS launcher with:

```sh
scripts/dev pytest tests/compare --compare-list tests/compare/lists/unit-NN.json --compare-right-module tests.compare.ts_launcher
```

The launcher replaces itself with Node. Its default entry is `server/test_host.ts`. Set `PANELS_COMPARE_TS_ENTRY` to select another entry.

That host must:

- Accept the inherited listener from `PLAN_SERVER_LISTENER_FD`. Do not bind another port.
- Use the fixture's database, fake clock, synthetic homes, logs, and other `PLAN_*` settings.
- Serve `GET /api/meta` with HTTP 200 and `test_mode: true`.
- Serve `/` with HTTP 200 and the `data-svelte-app` marker, as the shared fixture requires.
- Expose the production routes and response shapes that the selected list calls.
- Expose `/api/test/set-now` for clock changes and any existing test routes that the selected list uses.
- Expose `POST /api/test/poll/readiness`, `/notifications`, `/manager-wakes`, and `/sources` under `/api/test/poll/`.
- Return the Python host's loop response shapes: `ticket_ids`, `delivered` and `intents`, `batch_ids`, and `complete`, respectively.
- Wait for each requested loop pass to finish. Use scripted agents and recording notification adapters. Do not start real providers or external sends.

The TS host grows with the ported areas. A missing route is not a passing comparison. Unit 1 calibrates Python against Python and does not prove the TS host.

## Streams, restarts, and shared seed requests

An SSE step reads the same route on both servers concurrently:

```json
{"kind":"stream","method":"GET","path":"/api/navigation/view/events?view=...","status":200,"frames":1,"deadline_seconds":5}
```

`frames` counts complete frames with a `data` field. Comments and heartbeat lines do not count. The runner compares HTTP status, selected headers, event names, parsed JSON data, and recognized `id` and `retry` values. Reconnection values persist across frames, as SSE specifies. Non-JSON data remains text. Each side has one total deadline, including headers and body reads. Both connections close after the frame count or deadline. A short stream, incomplete frame, or deadline before the required count fails even if both sides produce the same silence. Captures use the frame list as their body.

A restart step stops and starts both hosts on their existing databases, ports, and host modules:

```json
{"kind":"restart"}
```

Captures survive the restart. The fixture bootstraps a database before launch, so the runner restores a SQLite backup after that bootstrap. This preserves the pre-restart rows for the server under comparison. The fake clock starts again at the fixture's configured time.

A list can supply `seed_requests` before its ordinary `requests`. Each seed request has the same method, path, status, JSON, headers, and capture fields as an ordinary request. The runner replays them once against the default Python host and stops it. SQLite backups of that database then supply both sides. Seed captures are available on both sides. This can create real caller records without a partial TS server's unsupported creation routes. Seed requests do not count as compared route coverage. No direct seed row writes occur.

### Writes on an open stream

A stream step can include `after_open`. Each request runs after the preceding complete frame on that same side. The first request follows the initial frame. The next request waits for the next frame. Use `frames` equal to one plus the number of requests, so every write has a following frame. The total deadline covers the writes too.

```json
{
  "kind": "stream", "method": "GET",
  "path": "/api/navigation/view/events?view=...", "status": 200,
  "frames": 3, "deadline_seconds": 10,
  "after_open": [
    {"method": "PATCH", "path": "/api/projects/${project}", "status": 200, "json": {"name": "Renamed"}},
    {"method": "POST", "path": "/api/projects", "status": 200, "json": {"name": "New project"}, "capture": {"new_project": "id"}}
  ]
}
```

Nested requests use the same captures and substitutions as ordinary requests. New captures remain available after the stream closes. The comparison includes their status, selected headers, and bodies under `after_open`. A failed write cannot pass through equal failures. Stream frame captures still refer to the frame list. This extension proves live delivery on one open connection, without a new host or another test server.

After each `after_open` write, the runner also executes the list's read probes. Each probe requires HTTP 200. These reads do not consume a stream frame or advance the next action. Their compared answers live under `after_open.*.probes.*`, with response bodies under `after_open.*.probes.*.body`. A normalization rule for those bodies needs that full path. The ordinary stream frames remain under `body.*.data`.

For review evidence, `report_captures` can name existing captures to include in the report. It changes no comparison or substitution behavior. Navigation uses this to retain ordered overview windows and the actual stream frames.

The shared `navigation_view_ordered` index is a TypeScript-only representation. The runner classifies that single table explicitly as derived storage for every list and includes the reason in each report. This prevents unrelated domain comparisons from treating the index as a canonical source table. Navigation's populated list compares its member IDs, order, paging, counts, and closures.
