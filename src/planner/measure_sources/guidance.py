"""Canonical source request and execution guidance, inspectable without a server."""

from typing import Any

from planner.core.request_guidance import RequestGuidance
from planner.measure_sources.contracts import RunRequestBody, SourceCreateBody, SourceScriptBody
from planner.measures.contracts import HistoryWindow

CREATE = RequestGuidance(
    SourceCreateBody,
    examples={
        "goal_source": {
            "name": "Daily activity source",
            "project_id": "p_example",
            "account": "example-account",
            "responsible": {"kind": "goal", "id": "goal_example"},
        }
    },
    notes=(
        "Replace placeholder IDs with existing records. Send this flat object to "
        "panels measure-source create on stdin. account is source metadata, not a credential.",
        "responsible uses kind and id (not goal_id). Chief uses {kind: chief, id: chief}; "
        "a manager or goal uses its existing ID. Responsibility and Project authorization "
        "are checked by the existing writer.",
        "Creation stores configuration only. Script submission, credentials, collection "
        "scheduling and runs are separate operations. Before writing a source script, read "
        "panels measure-source runtime --json, then panels measure-source schema script --json "
        "and panels measure-source schema run --json.",
    ),
)


SCALAR_EXAMPLE = """response = request(
    "https://api.example.com/value",
    credential_ref="example-service",
)
response.raise_for_status()
output = context["required_outputs"][0]
record(output["measure_id"], output["required_date"], response.json()["value"])
"""

_RUNTIME_NOTES = (
    "Source scripts execute ordinary top-level Python with context, request, record, latest "
    "and record_dataset supplied as globals. Printing JSON does not emit dated outputs. "
    "A script must call record or record_dataset for each result it contributes.",
    "Named credentials use a reusable name: credential_ref='example-service', or the local "
    "reference object {'scheme': 'local', 'name': 'example-service'}. Lookup uses the scheme "
    "and reusable name only. Source Project/account are data metadata and do not scope "
    "credential lookup. A saved matching credential name needs no extra permission card. "
    "Multiple legacy matches report ambiguity. Never embed secrets in scripts or output.",
    "The named scalar_request example is executable top-level Python. Replace only the "
    "generic URL and credential name for your provider, then adapt its response parsing "
    "to the actual response. It requires at least one configured taken measure owned by "
    "the source and reads that output's identity and suggested date from context. "
    "The example supplies no coverage facts and makes no complete-period claim.",
    "required_outputs and required_date identify owned scalar outputs and suggest local "
    "collection dates. They do not require every output or that particular date. Scripts "
    "may record any valid ISO calendar date. A history request supplies explicit inclusive "
    "first_date/last_date bounds; the script must fetch and emit the desired dates itself.",
    "record accepts a finite numeric value, or None with unavailable_reason equal to "
    "provider_has_no_history or provider_has_no_value. Use no reason with a numeric value. "
    "Do not fabricate zeroes for unavailable values. Output ownership, date syntax and "
    "duplicate measure/date entries are checked; kinds, units, signs and trends do not "
    "add numeric intake restrictions.",
    "Optional scalar query_start_at and query_end_at use finite Unix seconds and describe "
    "the interval contributing to that dated value, not the run's execution time. When "
    "both are supplied start must precede end. Supply each date's contributed bounds; "
    "do not repeat a multi-day query's global bounds for every dated subtotal. limitation "
    "is optional nonempty text describing a provider restriction. Collection time, a zero "
    "or a historical date does not establish completeness. Missing facts remain unknown "
    "for period counts; explicit partial bounds or limitations remain partial.",
    "Successful collection atomically replaces each supplied measure/date and its coverage "
    "facts, including past corrections; other dates are retained. Omitted coverage facts "
    "clear prior facts for the corrected date. Test runs return candidates in their receipt "
    "without changing retained observations or collection health. Inspect request-run's "
    "returned run ID with panels measure-source run-show RUN_ID --json until finished.",
    "Script submission uses expected_revision from the source's source_revision. Run "
    "submission uses expected_script_revision from its active_script.revision, not the "
    "source revision. Read panels measure-source show SOURCE_ID --json before either "
    "operation. Script submission saves immediately and advances both revisions. Read "
    "panels measure-source schema script --json and schema run --json for request envelopes.",
    "The runtime uses ordinary HTTP and Python behavior. request returns httpx.Response; "
    "call raise_for_status() when an unsuccessful status should fail the run. Named-key "
    "indirection avoids embedded secrets; trusted scripts can access resolved values and "
    "are not a security sandbox. No automatic retry is added.",
)

SCRIPT = RequestGuidance(
    SourceScriptBody,
    examples={"scalar_script": {"expected_revision": 1, "text": SCALAR_EXAMPLE}},
    notes=(
        "Read the source first. Replace expected_revision with its current source_revision; "
        "send the object to panels measure-source submit-script SOURCE_ID on stdin. The "
        "text is nonempty top-level Python, saved immediately as a new active script revision.",
        "Before authoring or adapting the example, read panels measure-source runtime --json "
        "for request(url, credential_ref=None, method='GET', json_body=None, headers=None), "
        "context declarations, dated output helpers and coverage. Printing JSON is not output.",
        "The scalar example requires an existing owned taken measure, a matching named "
        "credential and a real provider URL. It intentionally asserts no complete-period "
        "coverage. Run it with a test request before collecting retained values.",
    ),
)
RUN = RequestGuidance(
    RunRequestBody,
    examples={
        "test_run": {"mode": "test", "expected_script_revision": 1},
        "collection_run": {
            "mode": "collection",
            "expected_script_revision": 1,
            "history": {"first_date": "2026-01-01", "last_date": "2026-01-02"},
        },
    },
    notes=(
        "Read the source first. Replace expected_script_revision with active_script.revision, "
        "not source_revision. Send the object to panels measure-source request-run SOURCE_ID "
        "on stdin, then inspect its run ID with panels measure-source run-show RUN_ID --json.",
        "history may be omitted or null; both default to None. A supplied object requires "
        "first_date and last_date as inclusive ISO calendar dates, with first_date not after "
        "last_date. The script receives context['history']; the runtime does not fetch or "
        "fill history automatically. Replace example dates with the desired date window.",
        "test returns candidates without publishing observations or collection health. "
        "collection atomically publishes the accepted supplied dates. Both modes execute the "
        "stored script and may request its named credentials. Read panels measure-source "
        "runtime --json for script helpers, context, output declarations and coverage facts.",
    ),
    optional_fields={RunRequestBody: frozenset({"history"})},
    closed_objects=frozenset({HistoryWindow}),
)


def runtime_guide() -> dict[str, Any]:
    """Publish existing runtime behavior, without importing or starting the runtime."""
    return {
        "helpers": {
            "request": {
                "signature": (
                    "request(url, credential_ref=None, method='GET', json_body=None, "
                    "headers=None) -> httpx.Response"
                ),
                "notes": (
                    "Uses the named local credential when supplied; returns a standard "
                    "httpx.Response. Parse response.json(), response.text or response.content "
                    "as appropriate. HTTP status is not raised automatically. method, "
                    "json_body and headers support ordinary API requests and pagination."
                ),
            },
            "record": {
                "signature": (
                    "record(measure_id, date, value, unavailable_reason=None, *, "
                    "query_start_at=None, query_end_at=None, limitation=None) -> None"
                ),
                "notes": (
                    "Contributes a scalar candidate for an owned output and ISO date. "
                    "A None value requires provider_has_no_history or provider_has_no_value; "
                    "a numeric value requires no unavailable reason. Coverage bounds and "
                    "limitation are optional and describe only that dated value."
                ),
            },
            "latest": {
                "signature": "latest(measure_id)",
                "notes": (
                    "Returns None or a dictionary for the latest retained numeric observation "
                    "of an owned scalar output, captured at run admission. Fields: measure_id, "
                    "date, value, run_id, observed_at, definition_revision, period_interval, "
                    "manual_write_id, query_start_at, query_end_at, limitation, coverage_status "
                    "and coverage_reasons. period_interval is null or an object with start_date "
                    "and end_date_exclusive. A current run's candidates are not retained yet."
                ),
            },
            "record_dataset": {
                "signature": (
                    "record_dataset(dataset_id, date, rows, *, query_end_at, "
                    "query_start_at=None, labels=None, unavailable_reason=None, "
                    "limitation=None) -> None"
                ),
                "notes": (
                    "Contributes an existing declared dataset's date result. Each row has "
                    "dimensions {dimension: code} and values {field: number} matching its "
                    "captured declaration. query_end_at is required Unix seconds; "
                    "query_start_at and limitation are optional. labels maps dimension names "
                    "to {code: label}. Unavailable results have no rows. Successful rows [] "
                    "replaces a date with an empty result; omitted dates retain prior results "
                    "or remain missing. Test receipts retain dataset candidates; collection "
                    "publishes accepted date results."
                ),
            },
        },
        "context": {
            "run_id": "Current source run ID.",
            "frozen_at": "Captured run time as UTC Unix seconds, not proof of query coverage.",
            "required_outputs": (
                "List of owned scalar output declarations: measure_id, definition_revision, "
                "kind, timezone, period, required_date and nullable period_interval. The "
                "interval has start_date and end_date_exclusive when present."
            ),
            "dataset_outputs": (
                "List of owned dataset declarations: dataset_id, definition_revision, timezone, "
                "dimensions, fields and required_date. Each dimension has name/label; each "
                "field has name/label/unit/kind."
            ),
            "history": (
                "None or {first_date, last_date}, the requested inclusive ISO date window. "
                "No provider request or historical output is produced automatically."
            ),
        },
        "examples": {"scalar_request": SCALAR_EXAMPLE},
        "notes": list(_RUNTIME_NOTES),
    }
