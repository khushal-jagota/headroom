"""Synthetic admitted scripts and named credential fixtures."""

from __future__ import annotations

import hashlib
import time

from planner.credentials.contracts import (
    CredentialReference,
    ResolvedCredential,
)
from planner.datasets.contracts import OutputDataset
from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import (
    RunAdmission,
    RunMode,
    RunStatus,
    RuntimeLimits,
    RunTrigger,
    SourceRun,
)
from planner.measures.contracts import MeasureKind, OutputMeasure


def admission(
    script: str,
    *,
    outputs: tuple[str, ...] = ("m",),
    limits: RuntimeLimits = DEFAULT_LIMITS,
    run_id: str = "synthetic-run",
    dataset_outputs: tuple[OutputDataset, ...] = (),
) -> RunAdmission:
    run = SourceRun(
        id=run_id,
        source_id="synthetic-source",
        mode=RunMode.test,
        trigger=RunTrigger.test,
        status=RunStatus.running,
        source_revision=1,
        script_revision=1,
        script_sha256=hashlib.sha256(script.encode()).hexdigest(),
        frozen_at=1793059200,
        required_outputs=tuple(
            OutputMeasure(value, 1, MeasureKind.reading, "Europe/Berlin", None, "2026-10-27", None)
            for value in outputs
        ),
        history=None,
        schedule_date=None,
        schedule_time=None,
        created_at=1793059200,
        started_at=1793059200,
        finished_at=None,
        failure=None,
        metrics=None,
        observation_count=0,
        dataset_outputs=dataset_outputs,
    )
    return RunAdmission(
        run,
        script,
        (),
        limits,
        time.monotonic() + limits.wall_seconds,
    )


class MemoryCredentials:
    def __init__(self, *credentials: ResolvedCredential) -> None:
        self.credentials = credentials
        self.calls: list[CredentialReference] = []

    def resolve(self, reference: CredentialReference) -> ResolvedCredential:
        self.calls.append(reference)
        return next(item for item in self.credentials if item.metadata.reference == reference)
