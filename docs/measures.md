# Automatic project measures

Measures put dated numbers next to a Project's goals. A source collects numbers from a provider. A formula derives another measure from recorded numbers. Sources share one collection and repair system, so another supported provider needs configuration and a script rather than a deployment.

```
provider <-- HTTPS broker <-- bounded pipe <-- isolated source script
                                                |
                                         candidate observations
                                                |
                                      complete batch validation
                                                |
                                  dated values --> derived measures
                                                |
                                      source health and repair
```

A provider supplies the meaning of its figure. For example, a PostHog query can return a provider-computed figure. Panels records that query output. It does not infer revenue recognition, customer accounting, or subscription policy. No Stripe MRR source is selected or supported by this build.

Code paths: `src/planner/measures/`, `src/planner/measure_sources/`.

## Dates, kinds, and formulas

A **taken** measure belongs to one source. An **assembled** measure has a formula and no source. Each measure has a Project, name, unit label, kind, and timezone.

| Kind | Meaning | Required date in a collection |
| --- | --- | --- |
| `reading` | A finite number at a date, such as an account balance | The frozen collection date in the measure's timezone |
| `running_total` | A nonnegative cumulative number that never decreases across stored dates | The frozen collection date in the measure's timezone |
| `period_count` | A nonnegative exact integer for a complete day, ISO week, or calendar month | The last date of the latest complete period |

Counts cannot exceed 9,007,199,254,740,991. Other values use finite binary64 arithmetic. Unit labels do not perform conversions. Once observations use a definition, its kind, timezone, and count period cannot change. Project, origin, and source ownership stay fixed. A unit edit cannot invalidate a dependent count formula.

A daily count uses local midnight boundaries. An ISO week runs Monday through Sunday. A monthly count covers the calendar month. The stored date names the last included date. Stored boundaries include the start date and exclude the following date. A day can span 23 or 25 hours during a daylight-saving transition. A run on Tuesday, October 27, 2026 therefore requires Monday's daily count, the week ending October 25, or the month ending September 30.

Formulas support constants, references, addition, subtraction, multiplication, division, and complete daily sums. A reference's `day_offset` is between -366 and 0. Zero means the requested date. Negative values refer to earlier calendar dates. There are no future references or implicit conversions between snapshots and counters.

Reading formulas can use the general arithmetic operators. A running-total formula accepts cumulative inputs and nonnegative constant weights. Differences and ratios belong in a reading. A count formula requires the same unit and aligned period, with zero offsets. Counts support addition, subtraction, and nonnegative integer scaling. A `sum_daily` expression sums every daily count in the output's complete day, week, or month. Missing days remain missing. Results must satisfy the output kind.

For example, growth from 100 to 120 is `(120 - 100) / 100 * 100 = 20`. Correct the later value to 130, and the same formula returns 30. A missing earlier value is not zero. Division by zero, missing input, invalid period, and arithmetic limits produce explicit unavailable results.

Inputs can belong to other Projects, but their timezones must match. Cycles are refused, including cycles through dated references. Formula bounds are 128 expression nodes, depth 16, and 64 reachable measures. A coherent read returns at most 366 measure/date pairs. Evaluation and returned direct provenance each have a 50,000-pair/reference bound. Each input reference identifies the value and revision used. Follow the input definition for deeper provenance.

Code paths: `src/planner/measures/contracts.py`, `src/planner/measures/logic/`, `src/planner/measures/data.py`.

## Configure a source

The owner or Chief creates a source and its taken measures. They control destinations, activation, schedules, responsibility, and canonical definitions. A source names its responsible Manager or Chief explicitly.

For authenticated requests, the owner must first create the credential through the owner credential form. Select the Project, account, reference name, authentication kind, and allowed origins. Then attach that reference to a source destination. A new source can start with no destinations while the credential awaits entry.

Credentials use the `local` resolver scheme. Authentication kinds are bearer token, a named header, or HTTP Basic. Project and account scope must match the source exactly. Secret values never appear in CLI discovery. Credential creation and replacement have no CLI command or secret argument.

Each destination specifies a descriptive local grant name, HTTPS origin, GET or POST method, exact path or segment-aware path prefix, and optional credential reference. For example:

```json
{
  "id": "provider-report",
  "origin": "https://api.example.com",
  "method": "GET",
  "path": "/reports",
  "path_match": "prefix",
  "credential": {"scheme": "local", "name": "report-reader"}
}
```

This grants `/reports` and descendants such as `/reports/daily`, but not `/reports-private`. Query parameters can carry pagination or provider query options. A POST needs its own explicitly granted method and a JSON body. There are no arbitrary request headers, shell transports, database connectors, OAuth refresh, streaming, or file uploads. Redirect responses are returned without a follow-up request.

Another provider that fits these HTTPS and authentication capabilities needs only stored grants and a script. A new transport or authentication capability requires trusted host code. Sources do not install packages or extend the broker.

Code paths: `src/planner/credentials/`, `src/planner/measure_sources/actions.py`, `src/planner/measure_sources/logic/transport.py`.

## Script interface

Scripts execute with Python 3.13 and its standard library. The host supplies `context`, `request`, `latest`, and `record`. Ordinary stdout is the host protocol, so do not use `print` for diagnostics. Raw stdout, stderr, and exception text do not become run diagnostics.

`context` contains the run ID, frozen Unix timestamp, required outputs, optional history window, and grant names. Each output includes its measure ID, definition revision, kind, timezone, count period, required date, and optional period boundaries. Use these frozen dates instead of the current wall clock.

```python
response = request("provider-report", "https://api.example.com/reports/daily")
# response contains status, headers, and json_body. Headers are pairs.
value = response["json_body"]["value"]
output = context["required_outputs"][0]
record(output["measure_id"], output["required_date"], value)
```

The complete call forms are:

```python
request(grant_id, url, method="GET", json_body=None)
latest(measure_id)
record(measure_id, date, value, unavailable_reason=None)
```

`latest` returns the frozen previous observation for an owned output, or `None`. It does not expose unrelated measures or records from the current candidate batch. `record` submits a candidate and returns no value. It cannot write another source's measure.

Every run must return every configured output at its required date. Zero outputs, incomplete output, or an unavailable required value fails the complete batch. Duplicate measure/date pairs also fail. No candidate reaches canonical observations unless the entire collection passes validation.

A manual run can request an inclusive history window of at most 366 dates. Record historical values when the provider can supply them. An explicit historical gap uses one of two reasons:

```python
record("OWNED_MEASURE_ID", "2026-10-20", None, "provider_has_no_history")
record("OWNED_MEASURE_ID", "2026-10-21", None, "provider_has_no_value")
```

A gap does not invent zero or erase an existing observation. A later valid value clears the gap. Repeated collection of a measure/date overwrites that observation. Running-total corrections must also fit their stored neighbors. A failed collection preserves every previous value.

Provider jobs can finish asynchronously only within the same bounded run. A script can submit a granted POST and poll granted GET URLs. There is no job continuation after the run's wall deadline.

Code paths: `src/planner/measure_sources/bootstrap.py`, `src/planner/measure_sources/admission.py`, `src/planner/measure_sources/run_actions.py`, `src/planner/measures/data.py`.

## A synthetic source and the CLI

The CLI uses the current caller identity. It cannot grant itself owner or Manager authority. Structured commands read a JSON object from stdin. Add `--json` for the complete canonical response. Lists report their page facts and accept `--limit` and `--offset`.

The following source has no provider access and returns exactly 120. Replace `PROJECT_ID`, `SOURCE_ID`, and `MEASURE_ID` with the IDs from your records. Use current revisions from `show` after each definition change.

```sh
panels measure-source create --json <<'JSON'
{"name":"Synthetic daily reading","project_id":"PROJECT_ID","account":"synthetic","responsible":{"kind":"chief","id":"chief"},"destinations":[]}
JSON

panels measure create --json <<'JSON'
{"project_id":"PROJECT_ID","name":"Synthetic reading","unit":"items","origin":"taken","kind":"reading","timezone":"Europe/Berlin","source_id":"SOURCE_ID"}
JSON

panels measure-source show SOURCE_ID --json
```

Submit a script with the current source revision. Set `expected_pending_revision` to null when no pending script exists, or to its current revision when it exists.

```sh
panels measure-source submit-script SOURCE_ID --json <<'JSON'
{"expected_revision":2,"expected_pending_revision":null,"text":"output = context['required_outputs'][0]\nrecord(output['measure_id'], output['required_date'], 120)\n"}
JSON

panels measure-source request-run SOURCE_ID --json <<'JSON'
{"mode":"test","expected_script_revision":1}
JSON

panels measure-source run-show RUN_ID --json
```

The request returns an enqueued run. Inspect its explicit run ID for completion. A successful test returns the validated candidate value 120, but it writes no observation and restores no collection health.

The responsible Manager, Chief, or owner can publish the pending revision. The owner or Chief then enables the source with a daily server-local `HH:MM` schedule. Activation requires a published script, schedule, and output measure. A collection request uses `mode: "collection"` and the active script revision. It requires an enabled source. After success, a dated read returns 120:

```sh
panels measure read --json <<'JSON'
{"measure_ids":["MEASURE_ID"],"first_date":"2026-10-27","last_date":"2026-10-27"}
JSON
```

Use the actual required date from the run. The example date does not change the script's frozen context. `measure-source --help`, `measure --help`, and `credential --help` describe each command's canonical body. Commands submit one request and never wait in a polling loop.

Code paths: `src/planner/measure_sources/cli.py`, `src/planner/measures/cli.py`, `src/planner/credentials/cli.py`, `src/planner/cli/http.py`.

## Daily collection and repair

Schedules use the server's local calendar and the existing machine poll lock. A durable receipt identifies each source, local date, and scheduled time. Repeated polls or restarts do not duplicate that slot. A missed minute is not backfilled automatically. The spring clock gap has no matching minute. The repeated autumn minute shares one receipt.

Pending runs survive restart. Runs active during restart become interrupted. At most one run executes per source, with at most two source runs across the dispatcher. Each run freezes its source, script, output definitions, and dates. A definition or grant change invalidates an older admission before canonical commit.

Source health distinguishes disabled, never collected, healthy, stale, and failed. A scheduled value becomes stale 90 seconds after its expected slot unless a successful collection covers it. Current activity and the latest test are separate from the unresolved collection failure. The status response carries that failure as `collection_failure`, even after a successful draft test.

A failed collection opens a durable failure episode and sends a safe notice into the responsible Manager or Chief conversation. Repeated failures update the episode without repeated notices. Reassignment notifies the new responsible principal. A successful collection of the published revision closes the episode. Draft tests, script publication, and credential edits do not close it.

The responsible principal can designate a nonterminal repair Ticket under their responsibility. That Worker can submit and test pending scripts. It cannot publish, create a source or credential, add destinations, change output ownership, or alter schedules. The responsible Manager, Chief, or owner publishes. The owner replaces secrets through the credential form. Unresolved repair uses existing messages or the repair Ticket's help route.

Credential origin edits invalidate referencing source revisions in the same transaction. Admitted results cannot commit under the old origin authorization. This does not recall requests already sent. Secret replacement keeps the reference stable and affects the next admission. It does not claim immediate revocation of a credential copy already in a run.

Code paths: `src/planner/measure_sources/scheduling.py`, `src/planner/measure_sources/dispatcher.py`, `src/planner/measure_sources/notices.py`, `src/planner/manager_wakes/`.

## Host operations and limits

The runtime requires Linux x86-64, `/usr/bin/bwrap`, libseccomp, Python 3.13, the configured runtime library paths, timezone data, and kernel namespace support. Bubblewrap 0.11.0 is the verified host version. A host policy that refuses the required boundary makes the run fail closed. There is no ordinary-process fallback.

The script runs with network, process, mount, user, and IPC isolation. It receives only three intended pipes. The launch environment is empty. Host application files, the database, credentials, and key are absent. Runtime resources and every visible mount are readonly, including nested device mounts. Scripts have no writable filesystem. The host broker remains outside the script network namespace and receives only source-scoped credentials through its private bounded pipe.

The seccomp policy denies sockets, process creation, namespace changes, mounts, ptrace/process-memory access, kernel keys, BPF, perf, userfaultfd, io_uring, memfd, SysV IPC, POSIX queues, legacy AIO, and pidfd access. This is an enumerated denylist. It does not promise protection against unknown kernel interfaces or kernel vulnerabilities.

| Resource | Host limit |
| --- | --- |
| Whole run, including broker startup, DNS, TLS, and parsing | 30 seconds |
| Script CPU / address space / open descriptors | 2 seconds / 256 MiB / 32 |
| Script writable file size | 0 bytes |
| Broker CPU / address space / open descriptors | 10 seconds / 512 MiB / 64 |
| Provider requests | 64 |
| Source request frame | 64 KiB |
| One response / aggregate responses | 2 MiB / 16 MiB |
| Source stdout plus stderr / final candidate result | 256 KiB / 256 KiB |
| Candidate observations / JSON depth | 10,000 / 32 |
| Configured script text / destinations / owned output measures | 64 KiB / 64 / 64 |

The runtime's lower-level script envelope permits 256 KiB, but the canonical source writer enforces the tighter 64 KiB limit. All limits apply together. The host drains pipes without blocking, applies one absolute deadline, and kills and reaps owned processes on cancellation or failure. On the verified host, a pidfd pins the namespace child for cleanup. The fallback targets the owned process group and uses bubblewrap's parent-death behavior.

The broker accepts only public HTTPS destinations. It validates all DNS answers, pins a validated address, verifies the original TLS hostname, and ignores proxy environment settings. It returns JSON with exact known credential forms redacted from values, keys, and headers. Diagnostics contain fixed failure codes and safe metrics. Arbitrary provider transformations can defeat exact redaction. A malicious script can encode received information in numeric output. These are limits of the interface, not guarantees of universal secret detection.

Credentials use authenticated encryption with a separate host key. By default the key lives beside the database in `credential-secrets/key`. `PLAN_CREDENTIAL_KEY_PATH` can select another private path. The key must remain outside mounted runtime resources. Symlinks, exposed paths, wrong owners, loose permissions, invalid files, and an absent key for existing encrypted rows are refused. The key file uses mode 0600 and its private directory uses 0700.

Back up the database and key together through the host's private backup process. A database backup alone cannot decrypt credentials. Preserve the original key when you restore the database. Do not replace a missing key with a new random key. Recover the original private key first. A wrong key fails authentication rather than resetting existing credentials. Key rotation is not an operator command in this build.

Actual fixture evidence covers the kernel boundary, inherited descriptor identity and direction, CPU accounting, cancellation cleanup, and mediated TLS requests. One local fixture fetched three delayed pages and parsed 1,800,000 padding characters in 669 milliseconds. It used 54 milliseconds of source CPU and 232 milliseconds of broker CPU. Larger response and trickle tests failed at their intended budgets. These figures describe local fixtures, not a maximum capacity or provider-latency promise.

Code paths: `src/planner/measure_sources/configuration.py`, `src/planner/measure_sources/sandbox.py`, `src/planner/measure_sources/runtime/`, `src/planner/credentials/store.py`, `tests/integration/test_measure_source_runtime.py`, `tests/integration/test_measure_source_https.py`.

_Last verified: 2026-10-27._
