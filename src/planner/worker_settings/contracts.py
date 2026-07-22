"""Contracts for editable Worker management settings.

The Worker registry remains the immutable source for identity, labels, Stage order,
fields, terminality, and specialist skill names. These contracts describe the small
managed overlay that can be edited outside installed Python code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import WorkerTypeManifest


@dataclass(frozen=True, slots=True)
class ManagedSkill:
    name: str
    description: str
    markdown_body: str
    source_text: str


class SpecialistSkillPatch(TypedDict, total=False):
    description: str
    markdown_body: str


@dataclass(frozen=True, slots=True)
class ManagedWorkerSettings:
    worker_type: str
    stage_ownership_defaults: dict[str, StageOwnershipMode]
    specialist_skill: ManagedSkill
    candidate_specialist_skill: ManagedSkill | None = None


@dataclass(frozen=True, slots=True)
class WorkerManagementSummary:
    worker_type: str
    label: str
    specialist_skill_name: str
    stage_ownership_defaults: dict[str, StageOwnershipMode]


@dataclass(frozen=True, slots=True)
class WorkerManagementDetail:
    manifest: WorkerTypeManifest
    settings: ManagedWorkerSettings
