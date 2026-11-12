"""Retain historical attention through the existing trusted source runtime."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from planner.attention.actions import read_summary
from planner.attention.configuration import (
    ATTENTION_SOURCE_ACCOUNT,
    COLLECTION_LOCAL_TIME,
    COLLECTOR_PAGE_LIMIT,
    DAILY_METRICS,
    TIMEZONE,
)
from planner.attention.contracts import GroupBy, SummaryRequest
from planner.core.contracts import JsonDict


def _script(database_path: Path, dataset_ids: dict[str, str], measure_ids: dict[str, str]) -> str:
    return (
        "from pathlib import Path\n"
        "from planner.attention.collection import collect_daily\n"
        f"collect_daily(Path({str(database_path)!r}), {dataset_ids!r}, {measure_ids!r}, "
        "context=context, record=record, record_dataset=record_dataset)\n"
    )


def setup_collection(
    send: Callable[..., Any],
    *,
    project_id: str,
    database_path: Path,
    local_time: str = COLLECTION_LOCAL_TIME,
) -> JsonDict:
    """Configure through existing HTTP writes and their ordinary authority checks."""
    if not database_path.is_absolute():
        raise ValueError("attention collection needs an absolute server database path")

    def listing(path: str, key: str, **filters: str) -> list[dict[str, Any]]:
        found = []
        offset = 0
        while True:
            page = send(
                "GET", path, params={**filters, "limit": COLLECTOR_PAGE_LIMIT, "offset": offset}
            )
            found.extend(page[key])
            following = page["page"]["next_offset"]
            if following is None:
                return found
            offset = following

    matches = [
        entry["source"]
        for entry in listing("/api/measure-sources", "sources", project_id=project_id)
        if (item := entry["source"])["scope"]["account"] == ATTENTION_SOURCE_ACCOUNT
        and item["responsible"] == {"kind": "chief", "id": "chief"}
    ]
    if len(matches) > 1:
        raise ValueError("multiple attention sources need explicit repair")
    if matches:
        source = send("GET", "/api/measure-sources/" + matches[0]["id"])
    else:
        source = send(
            "POST",
            "/api/measure-sources",
            json_body={
                "name": "Historical owner attention",
                "project_id": project_id,
                "account": ATTENTION_SOURCE_ACCOUNT,
                "responsible": {"kind": "chief", "id": "chief"},
            },
        )
    source_path = "/api/measure-sources/" + source["id"]
    existing_datasets = listing("/api/datasets", "datasets", source_id=source["id"])
    existing_measures = listing("/api/measures", "measures", source_id=source["id"])
    dataset_ids: dict[str, str] = {}
    measure_ids: dict[str, str] = {}
    for metric in DAILY_METRICS:
        name = "Historical attention: " + metric
        old_datasets = [item for item in existing_datasets if item["name"] == name]
        old_measures = [item for item in existing_measures if item["name"] == name]
        if len(old_datasets) > 1 or len(old_measures) > 1:
            raise ValueError("duplicate attention outputs need explicit repair")
        seconds = metric.endswith("seconds")
        unit = "seconds" if seconds else "events"
        if old_datasets:
            dataset_ids[metric] = old_datasets[0]["id"]
        else:
            item = send(
                "POST",
                "/api/datasets",
                json_body={
                    "project_id": project_id,
                    "source_id": source["id"],
                    "name": name,
                    "timezone": TIMEZONE,
                    "dimensions": [
                        {"name": key, "label": label}
                        for key, label in (
                            ("destination_kind", "Destination kind"),
                            ("destination_id", "Destination"),
                            ("project_id", "Project"),
                            ("manager_id", "Manager"),
                            ("attribution_basis", "Attribution basis"),
                            ("coverage_status", "Coverage"),
                        )
                    ],
                    "fields": [
                        {
                            "name": "value",
                            "label": metric,
                            "unit": unit,
                            "kind": "amount" if seconds else "count",
                        }
                    ],
                },
            )
            dataset_ids[metric] = item["id"]
        if old_measures:
            measure_ids[metric] = old_measures[0]["id"]
        else:
            item = send(
                "POST",
                "/api/measures",
                json_body={
                    "project_id": project_id,
                    "source_id": source["id"],
                    "name": name,
                    "timezone": TIMEZONE,
                    "unit": unit,
                    "origin": "taken",
                    "kind": "period_count",
                    "period": "day",
                    "formula": None,
                },
            )
            measure_ids[metric] = item["id"]
    source = send("GET", source_path)
    script = _script(database_path, dataset_ids, measure_ids)
    if source["active_script"] is None or source["active_script"]["text"] != script:
        source = send(
            "PUT",
            source_path + "/script",
            json_body={
                "expected_revision": source["source_revision"],
                "text": script,
            },
        )
    if not source["enabled"] or source["local_time"] != local_time:
        source = send(
            "PUT",
            source_path + "/schedule",
            json_body={
                "expected_revision": source["source_revision"],
                "enabled": True,
                "local_time": local_time,
            },
        )
    return {"source_id": source["id"], "dataset_ids": dataset_ids, "measure_ids": measure_ids}


def collect_daily(
    database_path: Path,
    dataset_ids: dict[str, str],
    measure_ids: dict[str, str],
    *,
    context: dict[str, Any],
    record: Callable[..., Any],
    record_dataset: Callable[..., Any],
) -> None:
    """Read one coherent private snapshot and emit owned dated source outputs."""
    now = int(context["frozen_at"])
    today = datetime.fromtimestamp(now, ZoneInfo(TIMEZONE)).date()
    history = context.get("history")
    first = date.fromisoformat(history["first_date"]) if history else today - timedelta(days=1)
    last = date.fromisoformat(history["last_date"]) if history else today
    if first > last or last > today:
        raise ValueError("attention history dates are invalid")
    uri = database_path.resolve(strict=True).as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        day = first
        while day <= last:
            report = read_summary(
                conn,
                SummaryRequest(
                    first_date=day,
                    last_date=day,
                    group_by=GroupBy.ticket,
                    limit=COLLECTOR_PAGE_LIMIT,
                ),
                now=now,
            )
            all_rows = list(report["rows"])
            while report["page"]["next_offset"] is not None:
                page = read_summary(
                    conn,
                    SummaryRequest(
                        first_date=day,
                        last_date=day,
                        group_by=GroupBy.ticket,
                        limit=COLLECTOR_PAGE_LIMIT,
                        offset=report["page"]["next_offset"],
                    ),
                    now=now,
                )
                all_rows.extend(page["rows"])
                report["page"] = page["page"]
            report["rows"] = all_rows
            _emit_day(report, day.isoformat(), dataset_ids, measure_ids, record, record_dataset)
            day += timedelta(days=1)


def _emit_day(
    report: JsonDict,
    day: str,
    dataset_ids: dict[str, str],
    measure_ids: dict[str, str],
    record: Callable[..., Any],
    record_dataset: Callable[..., Any],
) -> None:
    period = report["period"]
    start = period["start_at"]
    end = period["end_at"]
    for metric in DAILY_METRICS:
        total = report["totals"]["current"][metric]
        limitations = list(total.get("limitations", []))
        rows = []
        labels: dict[str, dict[str, str]] = {"destination_id": {}}
        missing = 0
        for group in report["rows"]:
            observation = group["current"][metric]
            if observation["value"] is None:
                missing += 1
                continue
            dimensions = {
                "destination_kind": group["kind"],
                "destination_id": group["id"],
                "project_id": group.get("project_id") or "",
                "manager_id": group.get("manager_id") or "",
                "attribution_basis": json.dumps(group["attribution_basis"], separators=(",", ":")),
                "coverage_status": observation["coverage_status"],
            }
            rows.append({"dimensions": dimensions, "values": {"value": observation["value"]}})
            labels["destination_id"][group["id"]] = group["title"]
        if missing:
            limitations.append(
                f"{missing} destination values are unknown and absent from numeric rows."
            )
        limitation = " ".join(dict.fromkeys(limitations)) or None
        record_dataset(
            dataset_ids[metric],
            day,
            rows,
            query_start_at=start,
            query_end_at=end,
            labels=labels,
            unavailable_reason="provider_has_no_history"
            if total["value"] is None and not rows
            else None,
            limitation=limitation,
        )
        record(
            measure_ids[metric],
            day,
            total["value"],
            "provider_has_no_history" if total["value"] is None else None,
            query_start_at=start,
            query_end_at=end,
            limitation=limitation,
        )
