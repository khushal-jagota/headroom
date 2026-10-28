# Project measures

Goal charts read shared dated measures. Manual charts also offer owner controls for dated totals, cumulative snapshots, and individual readings.

Measures store dated numbers for Projects. A source runs a saved Python script. An assembled measure calculates arithmetic from retained observations. Another source needs a script and configuration, not a deployment.

The owner chose trusted scripts and simple, replaceable secret storage. Scripts run as ordinary child processes with the application's Python environment. They can use installed packages, files, subprocesses, and network access. This feature does not contain hostile code.

## Set up a source

Create a source with a name, Project, account, and responsible Manager or Chief. The existing assignment chain controls source work. The responsible agent and its assigned Workers can save scripts, configure schedules, test, and collect. There is no repair designation or publication step. Taken measures use that same source authority. Assembled and manual definitions use the existing owner/Chief authority for Project definitions.

The backend supports future owner secret submission through `POST /api/credentials` and replacement through `PUT /api/credentials/{id}/secret`. Supported authentication kinds are bearer token, named header, and Basic authentication. This build includes no UI for these operations. The credential CLI lists descriptive names and metadata only. Metadata responses omit secret values. Scripts reference named keys rather than embed secret literals.

Credentials use a separate replaceable cipher/resolver interface. The current implementation stores encrypted values in SQLite and a separate local encryption key. `credential_key_path` or `PLAN_CREDENTIAL_KEY_PATH` selects that key. New key files use private permissions. Back up the database and its key together. A missing or incorrect key causes a real credential failure; the system does not silently reset existing encrypted records.

A reference consists of a scheme and descriptive name within the source's Project and account. `local` is the current storage scheme. A missing name does not fall back to another account, Project, or environment variable. There are no destination, method, path, or credential-origin grants.

Create a source through `panels measure-source create`, with JSON on stdin:

```json
{"name":"Daily subscriptions","project_id":"PROJECT_ID","account":"Production","responsible":{"kind":"chief","id":"chief"}}
```

Create its measure through `panels measure create`:

```json
{"project_id":"PROJECT_ID","name":"Active subscriptions","unit":"subscriptions","origin":"taken","kind":"reading","timezone":"Europe/Berlin","source_id":"SOURCE_ID"}
```

Save the script through `panels measure-source submit-script SOURCE_ID`. Its body contains `expected_revision` and `text`. The save immediately replaces the current script. Revision checks prevent an older editor from overwriting a newer change.

## Write a script

The runtime supplies `context`, `request`, `record`, and `latest`. It resolves named credentials only when the script requests them. Ordinary runtime code can access resolved values. Key indirection avoids embedded secrets; it is not a security sandbox.

```python
response = request(
    "https://api.example.com/subscriptions/count",
    credential_ref="product-production",
)
response.raise_for_status()
output = context["required_outputs"][0]
record(output["measure_id"], output["required_date"], response.json()["count"])
```

`request` returns a standard `httpx.Response`. Scripts can inspect status, parse JSON, read text, and handle expected errors. Use `raise_for_status()` when an unsuccessful HTTP status must fail the run. Missing fields naturally raise a script error. The helper accepts `method`, `json_body`, and `headers`; JSON POST and script-controlled pagination need no approvals. HTTP behavior uses the ordinary library, including DNS, TLS, compression, proxies, and redirects. There are no custom response-size, request-count, MIME, address, or method policies.

The context includes the run ID, frozen time, configured outputs, and optional requested history dates. The historical field names `required_outputs` and `required_date` provide output identity and a suggested local collection date. They do not require every output or that particular date. A script can record any valid calendar date. `latest(measure_id)` returns the source's latest retained observation for that output.

`record(measure_id, date, value)` collects a candidate value. The system checks finite numeric representation, date syntax, output ownership, and duplicate dates in the batch. It does not judge trends, signs, integer counts, units, or whether the period is complete. Source code owns those choices.

A script can declare unavailable history with a null value and `provider_has_no_history` or `provider_has_no_value`. Missing values remain explicit. The system does not fabricate zeroes. Successful collection atomically overwrites each supplied measure/date, including past corrections. It retains other dates. Test runs return candidates without changing retained observations or collection health.

## Read and assemble numbers

Kinds (`reading`, `running_total`, `period_count`), units, timezone, and period are descriptive metadata. They do not constrain compatible arithmetic or whether values can rise or fall. Definitions can change after collection.

Assembled expressions support constants, references with integer day offsets, addition, subtraction, multiplication, division, and an explicit `sum_daily` operation. `sum_daily` selects the requested date's day, ISO week, or calendar month. It does not require the current period to finish. If an input is absent, the result identifies the missing input. Division by zero and invalid arithmetic also remain explicit.

The system prevents dependency cycles and unknown input references. Formula reads use retained values and report the input provenance. They do not call providers or models. Cross-Project formulas are supported. They do not grant credential access across Projects.

There is no fixed history-window, day-offset, collection-row, source-output, expression-size, or evaluation-count product limit. Values use finite binary64 numbers in SQLite REAL fields. Python's date, nesting, and memory representation still applies. List APIs retain ordinary pagination. An explicit measure-read `limit` selects a result page rather than rejecting the requested history range.

## Automatic collection and repair

Enable a daily server-local `HH:MM` schedule through `panels measure-source set-schedule`. The existing daily scheduler creates one receipt per source/date/time. Manual `request-run` works even when the daily schedule is disabled. Both tests and collections use the same saved script and require its current revision.

Runs execute outside the request and polling threads. At most two run concurrently, with one per source. This worker count is an operational setting in `measure_sources/settings.py`. Existing receipts preserve pending work; an interrupted run reports interruption. There is no catch-up across missed days, automatic request retry choreography, or durable provider-job continuation. A script can submit and poll an asynchronous provider job during its run.

`measure_source_timeout_seconds` in configuration, or `PLAN_MEASURE_SOURCE_TIMEOUT_SECONDS`, sets an ordinary execution timeout. Its default is 30 seconds and it has no compiled maximum. Process cleanup terminates the owned process group and reaps its direct child. Shared shutdown retains ownership while work remains live and reports cleanup failures after independent loop drains.

An actual collection failure preserves prior values and notifies the responsible agent through the existing notice system. Repeated failures update the same open failure episode. The agent can save a corrected script and rerun it directly, or ask the user for missing information or a replacement key. A successful collection clears the failure. A test does not clear a failed collection. The API and CLI expose actual run status and retained values. They do not infer overdue failures from a clock threshold.

Failure records contain structured categories, HTTP status where applicable, and a known exception type and script line when available. They do not copy raw exceptions, provider responses, or stdout/stderr into records. Output streams are drained and discarded.

## Provider scope

A provider supplies the meaning of its figure. A PostHog query can return a provider-computed count through JSON POST. This build does not choose a Stripe MRR source or define revenue accounting. Any Stripe example must identify the actual value it collects.

New scripts can use ordinary installed Python capabilities. This is not a universal integration engine. Unsupported libraries or new storage adapters can require host changes. Async provider work must fit the configured run duration unless the script supplies its own provider-specific continuation data.


## Manual observations

A manual measure has `origin: "manual"`, no source, and no formula. Owner controls use the existing owner/Chief authority. Goal-agent writes remain outside this approved operation.

`GET /api/measures/{id}/manual-state?date=YYYY-MM-DD` returns the selected observation, latest snapshot through that date, timezone, current local date, definition revision, and write revision. Without a date, the snapshot selects the measure-local current date from the server clock. An absent observation remains missing. Every snapshot carries the measure-wide write revision, including absent dates.

`POST /api/measures/{id}/manual-writes` accepts an explicit date, request ID, definition revision, and `date` or `latest` basis. A `replace` request supplies `value` and `expected_write_revision`. An `increment` request supplies `delta`. The server checks replacements against the measure-wide write revision. It calculates increments from the accepted base within one write transaction.

Daily increments use the selected date. Cumulative increments use the latest snapshot on or before the explicit action date. Only an explicit increment starts an absent base at zero. Cumulative reads never sum snapshots. Individual readings use date-specific replacements. Signed numbers, fractions, decreases, and values above a chart target remain valid. Targets stay separate from observations.

Each accepted request stores its authenticated actor, payload fingerprint, accepted date/value/revision, and timestamp. The record supports attribution and safe replay after a lost response. The same request ID and payload return the original acknowledgement, even after later edits. A reused ID with another payload or actor returns a conflict. No history browser, prior-value stream, undo, or automatic retry system exists.

Each numeric observation references exactly one real source run or manual receipt. Formula inputs retain that provenance. Manual writes do not fabricate source runs or change provider gaps. HTTP 409 responses for stale definition or value edits include the current snapshot in `error.detail.state`.
