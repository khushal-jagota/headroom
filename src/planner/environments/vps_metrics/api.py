"""Explicit reads from retained VPS evidence. GET routes never inspect the host."""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from .contracts import (
    HistoryResponse,
    LatestResponse,
    ProcessPage,
    QueryLimitError,
    Resolution,
    SnapshotExpiredError,
)
from .runtime import MetricsRuntime

router = APIRouter(prefix="/vps-metrics", tags=["vps-metrics"])


def _runtime(request: Request) -> MetricsRuntime:
    runtime: MetricsRuntime | None = getattr(request.app.state, "vps_metrics", None)
    if runtime is None:
        raise HTTPException(503, "VPS metrics runtime is unavailable")
    return runtime


async def _read[T](runtime: MetricsRuntime, operation: Callable[[sqlite3.Connection], T]) -> T:
    deadline = time.monotonic() + runtime.config.read_seconds

    def bounded(conn: sqlite3.Connection) -> T:
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            return operation(conn)
        finally:
            conn.set_progress_handler(None, 0)

    try:
        return await runtime.reads.run(bounded)
    except SnapshotExpiredError as exc:
        raise HTTPException(410, str(exc)) from exc
    except QueryLimitError as exc:
        raise HTTPException(422, str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, "VPS metrics storage is unavailable") from exc


@router.get("/latest")
async def latest(request: Request) -> LatestResponse:
    runtime = _runtime(request)
    result = await _read(runtime, lambda conn: runtime.store.latest(conn, runtime.wall_clock()))
    if runtime.failure is not None:
        # Keep the last stored timestamp when the latest failure could not be written.
        result["collection"] = {
            **runtime.failure,
            "last_sample_at": result["collection"]["last_sample_at"],
        }
    return result


@router.get("/processes")
async def processes(
    request: Request,
    sequence: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1)] = 250,
) -> ProcessPage:
    runtime = _runtime(request)
    if limit > runtime.config.process_page_rows:
        raise HTTPException(422, "Process page exceeds configured row limit")
    return await _read(
        runtime,
        lambda conn: runtime.store.processes(
            conn,
            runtime.wall_clock(),
            sequence=sequence,
            offset=offset,
            limit=limit,
        ),
    )


@router.get("/history")
async def history(
    request: Request,
    signal: Annotated[str, Query(min_length=1, max_length=200)],
    start: float,
    end: float,
    resolution: Resolution = "minute",
    entity: Annotated[str | None, Query(max_length=500)] = None,
) -> HistoryResponse:
    runtime = _runtime(request)
    if (
        not math.isfinite(start)
        or not math.isfinite(end)
        or end <= start
        or end - start > runtime.config.coarse_seconds
    ):
        raise HTTPException(422, "History range exceeds configured retention or is invalid")
    return await _read(
        runtime,
        lambda conn: runtime.store.history(
            conn,
            signal=signal,
            entity=entity,
            start=start,
            end=end,
            resolution=resolution,
        ),
    )


@router.post("/activity")
async def activity(request: Request) -> dict[str, float]:
    runtime = _runtime(request)
    import asyncio

    try:
        expiry = await asyncio.to_thread(runtime.store.renew_activity, runtime.wall_clock())
    except sqlite3.Error as exc:
        raise HTTPException(503, "VPS metrics storage is unavailable") from exc
    return {"active_until": expiry}
