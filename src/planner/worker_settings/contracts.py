"""Contracts for editable Worker management settings.

The Worker registry remains the immutable source for identity, labels, Stage order,
fields, terminality, and specialist skill names. These contracts describe the small
managed overlay that can be edited outside installed Python code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from planner.worker_types.contracts import WorkerTypeManifest


@dataclass(frozen=True, slots=True)
class ManagedSkill:
    name: str
    description: str
    markdown_body: str
    source_text: str


@dataclass(frozen=True, slots=True)
class SkillsHome:
    skills: tuple[ManagedSkill, ...]


class SpecialistSkillPatch(TypedDict, total=False):
    description: str
    markdown_body: str


@dataclass(frozen=True, slots=True)
class ManagedWorkerLaunchDefaults:
    """What this Worker or the Chief launches on, as the owner last saved it.

    The backend and the model are both named, always: a saved setting that named no model
    would launch on whatever the backend picked for itself, which is nobody's choice and
    nothing the settings screen can show. The reasoning effort may be absent, because some
    models take none.
    """

    employee_backend: str
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class ManagedWorkerSettings:
    worker_type: str
    specialist_skill: ManagedSkill
    launch_defaults: ManagedWorkerLaunchDefaults
    candidate_specialist_skill: ManagedSkill | None = None


@dataclass(frozen=True, slots=True)
class ManagedChiefSettings:
    employee_id: str
    label: str
    skill: ManagedSkill
    launch_defaults: ManagedWorkerLaunchDefaults


@dataclass(frozen=True, slots=True)
class WorkerManagementSummary:
    worker_type: str
    label: str
    specialist_skill_name: str
    launch_defaults: ManagedWorkerLaunchDefaults


@dataclass(frozen=True, slots=True)
class WorkerManagementDetail:
    manifest: WorkerTypeManifest
    settings: ManagedWorkerSettings
