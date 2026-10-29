# Project measures

Goal charts read shared dated measures. Manual charts also offer owner controls for dated totals, cumulative snapshots, and individual readings.

Measures store dated numbers for Projects. A source runs a saved Python script. An assembled measure calculates arithmetic from retained observations. Another source needs a script and configuration, not a deployment.

The owner chose trusted scripts and simple, replaceable secret storage. Scripts run as ordinary child processes with the application's Python environment. They can use installed packages, files, subprocesses, and network access. This feature does not contain hostile code.

## Set up a source

Create a source with a name, Project, account, and responsible Chief, Manager, or goal. Source work follows that responsibility. A goal agent can create and manage a source responsible to its own goal without another approval. This does not grant control over another goal's source. The responsible agent and authorized Workers can save scripts, configure schedules, test, and collect. There is no repair designation or publication step. Taken measures use that same source authority. Assembled and manual definitions use the existing owner/Chief authority for Project definitions.

Credentials use descriptive names such as `Panels_posthog_production`. The names identify their use without exclusive Project ownership. Source Project/account remain data metadata. The credential CLI lists names and metadata without secret values. Scripts reference named keys rather than embed secret literals.

An agent can use `panels credential request` to request a bearer or named-header API key in its conversation. The owner saves or rejects the inline card. Replacement targets an existing credential and its revision. Basic authentication retains its existing private store operations outside this card. Secret submission uses the private owner path and never becomes a chat answer.

Credentials use a separate replaceable cipher/resolver interface. The current implementation stores encrypted values in SQLite and a separate local encryption key. `credential_key_path` or `PLAN_CREDENTIAL_KEY_PATH` selects that key. New key files use private permissions. Back up the database and its key together. A missing or incorrect key causes a real credential failure; the system does not silently reset existing encrypted records.

A reference consists of a scheme and reusable descriptive name. `local` is the current storage scheme. Lookup does not use source Project/account or an environment-variable fallback. A matching saved name needs no additional permission card. Multiple legacy matches report explicit ambiguity with metadata candidates. The owner can rename a selected credential by ID and expected revision. Update affected source references before the next run. There are no destination, method, path, or credential-origin grants.

The migration preserves existing ciphertext, identities, names, and revisions with their original private decryption context. Rename authenticates and reencrypts the same saved value. Replacement authenticates the current record and requires its current revision.

Saved, Replaced, and Rejected describe request resolution. Delivery has a separate result. A queued result waits in the conversation. Confirmed refusal permits metadata-only delivery retry. Unknown backend writes and lost queues remain uncertain and receive no automatic retry. A saved value does not prove that the provider accepts the key.

Before a source or measure write, inspect the accepted request through these read-only commands:

```sh
panels measure-source schema --json
panels measure schema --json
```

Each command returns `schema`, `examples`, and `notes` without stdin or a server request. Examples include goal responsibility and a taken daily count measure. Replace example identifiers with saved record IDs. CLI help and goal agent startup guidance point to these commands. OpenAPI describes the same create requests from the shared contract reference. [Goals](goals.md) describes chart discovery.

Create the source through `panels measure-source create` and its measure through `panels measure create`. Both commands accept JSON on stdin.

_Code paths:_ `src/planner/measure_sources/`, `src/planner/measures/`, and `src/planner/core/request_guidance.py`.

Save the script through `panels measure-source submit-script SOURCE_ID`. Its body contains `expected_revision` and `text`. The save immediately replaces the current script. Revision checks prevent an older editor from overwriting a newer change.

A source can also own a dataset with declared dimensions and numeric fields. The responsible source author can create and update its definition. Its Project must match the source Project. A successful run replaces each supplied dataset and date with retained rows, the captured output declaration, query bounds, and provenance. It retains other dates. Goal charts read those retained results and category labels without another provider request. Missing or unavailable results remain explicit. The reader does not invent zeroes.

Pass this definition as JSON on stdin to `panels dataset create`. Use `name` for each dimension and field, and include the source's Project:

```json
{
  "project_id": "PROJECT_ID",
  "source_id": "SOURCE_ID",
  "name": "Daily events by team",
  "timezone": "UTC",
  "dimensions": [{"name": "team", "label": "Team"}],
  "fields": [{"name": "events", "label": "Events", "unit": "events", "kind": "count"}]
}
```

The source script receives the captured declarations in `context["dataset_outputs"]`. This example records one date with query bounds in Unix seconds and an explicit provider limit:

```python
from datetime import datetime, timezone

output = context["dataset_outputs"][0]
day = output["required_date"]
record_dataset(
    output["dataset_id"], day,
    [{"dimensions": {"team": "north"}, "values": {"events": 7}}],
    query_start_at=datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp(),
    query_end_at=context["frozen_at"],
    labels={"team": {"north": "North"}},
    limitation="Provider returned a sample.",
)
```

Replace the sample row, interval, and limitation with the provider's result. `query_end_at` is required. `query_start_at` and `limitation` are optional. A successful result with `rows: []` replaces that date with an empty result. Omitting a date preserves its prior result, or leaves it missing when no result exists. A test run saves dataset candidates in its receipt but does not publish them. A collection run publishes accepted date results.

## Write a script

The runtime supplies `context`, `request`, `record`, `record_dataset`, and `latest`. It resolves named credentials only when the script requests them. Ordinary runtime code can access resolved values. Key indirection avoids embedded secrets; it is not a security sandbox.

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

## Scalar coverage facts

A source can add optional `query_start_at`, `query_end_at`, and `limitation` keywords to `record`. Bounds use Unix seconds. The existing positional arguments keep their meaning. These facts describe the returned number, not the time that the run executes.

For each dated subtotal, supply the interval that contributes to that date. Do not repeat a multi-day query's global bounds for every daily result. A provider limitation remains explicit through test receipts, collection, corrections, and reads.

Coverage affects comparison eligibility, not intake acceptance. Partial periods and changed definition revisions remain valid inputs. The system checks finite timestamp representation, interval order, and limitation text. It adds no numeric restrictions.

A period count is complete only when both supplied bounds match its captured local calendar period and no limitation exists. A mismatched interval or explicit limitation means partial coverage. Missing evidence means unknown coverage. The captured timezone defines local day, ISO week, or calendar month boundaries, including daylight-saving changes.

Older period counts keep their numeric values. Their coverage is unknown, and their complete-period verdicts are neutral. Collection time, zero, and a historical date do not establish completeness. A later correction replaces the value and coverage together. Omitted coverage clears prior facts for that corrected date.

Names, units, and revision-only edits do not change coverage. A changed kind, timezone, or period cannot turn an old observation into a newly complete period. Ordinary readings and running totals keep their snapshot comparisons unless an explicit limitation applies. Manual period counts without coverage stay numeric and unknown. Manual controls gain no coverage inputs.

Scalar reads expose `coverage_status` and `coverage_reasons` separately from numeric status. The states are `complete`, `partial`, `unknown`, and `not_applicable`. A partial zero still has numeric status `value`. Direct values and input provenance retain the supplied query facts.

Expressions preserve coverage through evaluated references, arithmetic, fixed dates, selected branches, and explicit daily sums. Partial or unknown inputs do not become complete through arithmetic. Bare constants and elapsed-day expectations need no period evidence. An assembled period count without period evidence remains unknown.

## Read and assemble numbers

Kinds (`reading`, `running_total`, `period_count`), units, timezone, and period are descriptive metadata. They do not constrain compatible arithmetic or whether values can rise or fall. Definitions can change after collection.

Assembled expressions support constants, references with integer day offsets, arithmetic, real powers, minimums, maximums, elapsed calendar days, fixed-date references, and date branches. The explicit `sum_daily` operation selects the requested date's day, ISO week, or calendar month. It does not require the current period to finish. If an input is absent, the result identifies the missing input. Division by zero and invalid arithmetic also remain explicit.

Date branches evaluate only the selected phase. The boundary date belongs to the new phase. Elapsed days count calendar dates across daylight-saving changes. A fixed-date reference follows corrections to that retained observation; a literal constant does not. Calendar-month growth and repeating completed-period steps are not supported. Do not substitute thirty-day months or fractional weeks for those meanings.

Goal expectations use this same evaluator without creating another measure. Their date projections have separate operational bounds described in [Goals](goals.md).

The system prevents dependency cycles and unknown input references. Formula reads use retained values and report the input provenance. They do not call providers or models. Cross-Project formulas are supported. Formula reads do not resolve credentials.

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

A manual measure has `origin: "manual"`, no source, and no formula. Owner controls use the existing owner/Chief authority. A goal agent can write a manual measure bound in its own primary chart or page without another approval. This includes a shared measure bound in that goal. The permission does not extend to unbound measures or another goal's charts.

`GET /api/measures/{id}/manual-state?date=YYYY-MM-DD` returns the selected observation, latest snapshot through that date, timezone, current local date, definition revision, and write revision. Without a date, the snapshot selects the measure-local current date from the server clock. An absent observation remains missing. Every snapshot carries the measure-wide write revision, including absent dates.

`POST /api/measures/{id}/manual-writes` accepts an explicit date, request ID, definition revision, and `date` or `latest` basis. A `replace` request supplies `value` and `expected_write_revision`. An `increment` request supplies `delta`. The server checks replacements against the measure-wide write revision. It calculates increments from the accepted base within one write transaction.

Daily increments use the selected date. Cumulative increments use the latest snapshot on or before the explicit action date. Only an explicit increment starts an absent base at zero. Cumulative reads never sum snapshots. Individual readings use date-specific replacements. Signed numbers, fractions, decreases, and values above a chart target remain valid. Targets stay separate from observations.

Each accepted request stores its authenticated actor, payload fingerprint, accepted date/value/revision, and timestamp. The record supports attribution and safe replay after a lost response. The same request ID and payload return the original acknowledgement, even after later edits. A reused ID with another payload or actor returns a conflict. No history browser, prior-value stream, undo, or automatic retry system exists.

Each numeric observation references exactly one real source run or manual receipt. Formula inputs retain that provenance. Manual writes do not fabricate source runs or change provider gaps. HTTP 409 responses for stale definition or value edits include the current snapshot in `error.detail.state`.

_Last verified: October 29, 2026._
