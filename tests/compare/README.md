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

## Candidate selection in branch CI

After a list passes against TypeScript, add `"candidate": "typescript"` at its
root. CI and local pytest runs then compare that list against
`tests.compare.ts_launcher`. Lists without the marker retain Python against
Python calibration. An explicit `--compare-right-module` overrides the marker.

Pull requests select affected lists through `source_roots`. Shared harness,
core, or `server/` changes select every list. Branch pushes run every list.
The workflow installs `server/` dependencies and runs on Node 22 and 24, as the
server workflow does. A difference fails the job. Reports upload even after
failure, with one artifact for each Node version.

## Synthetic host configuration

A request list can supply `host_environment`. The runner accepts only these names:

| Name | Accepted synthetic values |
| --- | --- |
| `PLAN_TRUSTED_INGRESS_PROVIDER` | `tailscale` |
| `PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN` | A login at a domain that ends in `.invalid` |
| `PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN` | An HTTPS origin at a domain that ends in `.invalid`, without credentials, port, path, query, or fragment |
| `PLAN_APNS_KEY_ID` | Uppercase letters or digits with the prefix `TEST` |
| `PLAN_APNS_TEAM_ID` | Uppercase letters or digits with the prefix `TEST` |
| `PLAN_APNS_BUNDLE_IDS` | A comma-separated list of reverse-domain identifiers that start with `invalid.` |

For example:

```json
{
  "host_environment": {
    "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
    "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@comparison.invalid",
    "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "https://panels.comparison.invalid",
    "PLAN_APNS_KEY_ID": "TESTKEY10",
    "PLAN_APNS_TEAM_ID": "TESTTEAM10",
    "PLAN_APNS_BUNDLE_IDS": "invalid.panels.ios"
  }
}
```

Unsupported names and nonsynthetic values fail before any server starts. The allowlist is explicit in `runner.py`. An extension requires a code change and review. A list cannot set `HOME`, provider homes, database paths, server URLs, credential paths, key paths, or adapter/mode selection.

If a list supplies APNs metadata, it must supply all three APNs names. The runner generates a fresh P-256 PKCS8 PEM key inside the test directory with mode `0600`. Only the runner supplies `PLAN_APNS_KEY_PATH`. The same synthetic configuration and generated key reach the Python seed host, both comparison hosts, and their restarts through the existing fixture hook. The seed still uses the real scripted Python core. The separate `conversation_system` selection applies only to the comparison hosts.

This configuration permits successful authenticated device registration and durable native queue comparison. It does not select a real transport. Both comparison hosts keep mandatory recording adapters for notification passes and disable production loops in test mode. Synthetic push targets use reserved invalid domains and device tokens. No comparison path constructs a real push adapter or sends a push. Existing source-row inspection still opens host databases read-only.

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

For later domain lists, a navigation snapshot probe after each write is enough. No source registration is required. Existing source triggers and the shared navigation commit step update the records and views.

## Conversation record comparison (Unit 14)

`unit-14.json` uses a Python-only seed and compares the six selected-view routes
beneath `/api/conversation/conversations/{conversation_id}/view`. The seed host
keeps the existing `ScriptedChild` handles. Its finish, questions, rejection, and
owner-message controls reuse the existing browser host's child methods and core
operations. Each completed control awaits the real core's `wait_until_quiescent`.
No timer guess decides that a seed is ready.

The additional `backend-news` control reports typed tool, plan, usage, compaction,
command, failed-input, permission, and turn-ending facts through the child's real
`BackendEventSink`. The core owns their event rows and projections. These facts
have no existing `ScriptedChild` method. This control is absent from the TS host.
It never injects stored event payloads or performs comparison-time stream writes.

When seed requests keep attachments, the runner copies the seed's synthetic
`files/conversations/` tree into each server database directory. The database and
attachments therefore name the same durable bytes and media sidecars. Existing
restart steps retain those files in the same isolated directory. The runner does
not copy live files or provider homes.

The TS comparison host replays each seeded conversation through its real TS
projection writer before area initialization. The Python host retains the Python
projection. The request list therefore checks both the projection rows and the
public reads, rather than merely reading copied Python projections on both sides.
All five `conversation_view_*` tables remain in the source-row comparison.

The 42 seed requests create these visible facts:

| Seed conversation | Facts exposed by compared reads |
| --- | --- |
| Rich completed history | A durable CSV attachment, owner prompts and steer, two explicit owner messages, orphan and settled tools, plans, token usage, compaction, command output, answered asks, expired unanswered asks, discarded and refused prompts, sender-ID deduplication, and a run-setting change |
| Small completed turn | A short snapshot and bounded replay from cursor zero |
| Open turn | Simultaneous permission and input requests plus an owner message, left open for startup reconciliation |
| Interrupted turn | The real interrupt path and its durable ending |
| Failed turn | A scripted backend failure and its durable error text |

The rich history includes 125 settled tools in one work group. Compared pages
exercise the 100-item boundary, older and newer cursors, and turn/group expansion.
Its command output exceeds 128 KiB. Body reads compare consecutive bounded UTF-8
chunks, complete content, and stale references. The existing first chunk request
uses `offset=0_0`, an accepted Pydantic integer spelling. Receipt reads include a
repeated sender ID and an absent ID.

Tail requests compare SSE headers, snapshots, replay from zero, and gap resets for
an old or future cursor. Version and malformed-cursor refusals compare their
explicit product error text. Restart reads compare the stopped view again. A new
event on an already open HTTP stream remains a named Unit 15 proof. Local tail
handoff and overflow use the admitted in-process proof instead.

### What the HTTP list proves from the view oracle

The oracle fixture is a fixed historical vocabulary. The current core cannot
produce every historical row. The following map preserves that distinction:

| Existing oracle fixture | HTTP comparison coverage and explicit limit |
| --- | --- |
| `owner-visible-turn-work-and-final-answer` | Owner prompt, tools, two addressed owner messages, closed turn, and collapsed/expanded work. Historical `agent_message` rows cannot be created by the current core, which keeps backend prose in the live tail |
| `hidden-prompt-turn-and-owner-steer` | Owner steer, addressed owner messages, and multiple turn boundaries. A hidden non-owner prompt is not produced by the owner-only unassociated send path in this list |
| `legacy-owner-and-forged-label` | Modern owner provenance is compared. Missing legacy provenance and deliberately forged non-owner labels cannot be minted through current intake |
| `settlement-and-reconciliation` | Refusal, discard, exact sender-ID retry, and receipt lookup. Uncertain prompt reconciliation needs native delivery evidence and remains outside this HTTP seed |
| `asks-answer-and-turn-expiry` | Permission and input answers, plus unanswered asks that expire with a completed turn |
| `failed-input-without-request-and-retired-missing-reply` | A failed input with no request is produced. The retired `explicit_reply_missing` kind is no longer written by the current core |
| `stopped-without-ending` | The open seeded turn and both pending request kinds lose their process on restart, with no synthetic ending event |
| `tool-orphans-plan-and-work-run-boundaries` | Orphan tool settlement, work groups, newest plan, token usage, compaction, model metadata, and owner messages. A tool event before the first prompt and historical durable backend prose cannot be produced by the active-token backend control |
| `simultaneous-pending-permission-and-input` | Both asks coexist in seed rows. Their post-restart dead state is compared; their active callbacks and live ask status require Unit 15 |
| `running-multiple-owner-messages-and-time-skew` | Two owner messages, owner steer, a valid minted timestamp, and an out-of-window timestamp clamp. The copied database does not preserve a running process |

The list's real core produces `command_reply` and `user_input_failed`, two of the
four kinds absent from the backup audit. It does not produce `answer_applied` or
`credential_request`. The separate 13 Python-generated examples cover all four
codecs. Credential writer/delivery integration belongs to Unit 19. Saved-answer
callback application and native uncertainty remain their owning core/executor
integration proofs.

The fixed oracle still proves its full ten-case vocabulary locally. Its committed
generator and the record README describe regeneration after a staging merge. The
unreachable rows above do not receive a false claim of new HTTP coverage.

## Direct checks

Add a `test_*.py` driver under `tests/compare/direct/` that compares current Python with an export-only TS companion under `server/`.
Build synthetic state, copy it before each side writes, compare answers and rows, and save evidence through `--compare-report-dir`.
The comparison workflow runs this directory once on both Node versions, before list selection, so every staging merge regenerates Python answers.
