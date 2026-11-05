"""Shared canonical page-document writes for Chief and Managers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, cast

from planner.core import authority
from planner.core.contracts import ErrorCode, PlannerError, Principal, PrincipalKind
from planner.core.ids import new_id
from planner.goals.contracts import PageDocument, PageReadingSubject
from planner.goals.logic.documents import prepare_document
from planner.measures.data import transaction


def read_page(conn: sqlite3.Connection, manager_id: str | None) -> dict[str, Any]:
    if manager_id is None:
        row = conn.execute("SELECT page,page_revision FROM chief_page WHERE singleton=1").fetchone()
    else:
        row = conn.execute(
            "SELECT page,page_revision FROM managers WHERE id=?", (manager_id,)
        ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "holder not found")
    return {"page": json.loads(row[0]) if row[0] else None, "page_revision": row[1]}


def write_page(
    conn: sqlite3.Connection, manager_id: str | None, body: dict[str, Any], *, principal: Principal
) -> dict[str, Any]:
    if manager_id is None:
        if principal.kind not in (PrincipalKind.owner, PrincipalKind.chief):
            raise PlannerError(
                ErrorCode.agent_forbidden, "only the owner or Chief can edit Chief's page"
            )
    else:
        authority.require_above_or_self(conn, principal, authority.manager(manager_id))
    with transaction(conn):
        current = read_page(conn, manager_id)
        if (
            type(body.get("expected_revision")) is not int
            or body["expected_revision"] != current["page_revision"]
        ):
            raise PlannerError(
                ErrorCode.stale_claim,
                "holder page changed; read the accepted document before editing again",
            )
        try:
            page = (
                None
                if body.get("page") is None
                else prepare_document(cast(PageDocument, body["page"]), lambda: new_id("block"))
            )
        except (ValueError, TypeError) as exc:
            raise PlannerError(ErrorCode.validation, str(exc)) from exc
        if page is not None:
            validate_page_bindings(conn, page)
        saved = json.dumps(page, ensure_ascii=False) if page is not None else None
        if manager_id is None:
            conn.execute(
                "UPDATE chief_page SET page=?,page_revision=page_revision+1 WHERE singleton=1",
                (saved,),
            )
        else:
            conn.execute(
                "UPDATE managers SET page=?,page_revision=page_revision+1 WHERE id=?",
                (saved, manager_id),
            )
    return read_page(conn, manager_id)


def validate_page_bindings(conn: sqlite3.Connection, page: PageDocument) -> None:
    """Use the same bound-reference checks as saved goal documents."""
    from planner.datasets.data import read_dataset
    from planner.goals.logic.categories import validate_category_references
    from planner.goals.logic.documents import (
        charts,
        dataset_ids,
        expectation_measure_ids,
        measure_ids,
    )
    from planner.measures.contracts import MeasureOrigin
    from planner.measures.data import read_definitions, read_measure
    from planner.measures.logic.formulas import validate_graph

    try:
        identifiers = set(measure_ids(None, page)) | set(expectation_measure_ids(None, page))
        definitions = read_definitions(conn, tuple(identifiers))
        datasets = {
            identifier: read_dataset(conn, identifier) for identifier in dataset_ids(None, page)
        }
        for identifier in identifiers:
            validate_graph(identifier, definitions)
        for chart in charts(None, page):
            if "binding" in chart:
                validate_category_references(chart, datasets=datasets, measures=definitions)
            if (
                "manual" in chart
                and read_measure(conn, chart["measure_id"]).origin is not MeasureOrigin.manual
            ):
                raise ValueError("manual controls require a manual-origin measure")
    except (ValueError, TypeError) as exc:
        raise PlannerError(ErrorCode.validation, str(exc)) from exc


def page_readings(
    conn: sqlite3.Connection, manager_id: str | None, now: datetime
) -> dict[str, Any]:
    from planner.goals.views import document_readings_json

    with transaction(conn, read_only=True):
        saved = read_page(conn, manager_id)
        page = saved["page"] or {
            "kind": "headroom.page",
            "version": 1,
            "title": "",
            "summary": "",
            "blocks": [],
        }
        subject = PageReadingSubject(
            id="chief" if manager_id is None else manager_id,
            page=cast(PageDocument, page),
            revision=saved["page_revision"],
        )
        return document_readings_json(conn, subject, now)
