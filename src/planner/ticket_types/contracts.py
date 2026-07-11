"""Ticket-type registry shapes: the workflow definition and its parts, plus the
serialized manifest TypedDicts.

All shapes are frozen dataclasses (definition side) and TypedDicts (manifest
side). Types are plain strings at the boundary so the registry is genuinely
N-ary across ticket types; the shipped ``coding`` definition sources its strings
from the ``tickets/contracts`` leaf enums so parity is checkable against the live
lifecycle constants.

Stdlib only. Imports nothing from another planner module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class Stage:
    id: str                       # a TicketState value, e.g. "needs_success"
    label: str                    # display label, e.g. "Success"
    gating_field: str | None      # a FieldName value; None iff terminal (done / dropped)
    is_terminal: bool             # True for done and dropped


@dataclass(frozen=True)
class FieldDef:
    id: str                       # a FieldName value, e.g. "kickoff"
    label: str                    # display label, e.g. "Kickoff"


@dataclass(frozen=True)
class WorkerProfile:              # declared, INERT this ticket — validated for reference only
    specialist_skill: str         # e.g. "panels-worker" (must be in the injected known skills)
    model: str | None             # None = home default (planner passes no per-session model)
    reasoning_effort: str | None  # None = home default
    toolset_profile: str          # a toolset-profile id (must be in the injected known toolsets)


@dataclass(frozen=True)
class TransitionHook:             # declared, INERT this ticket — plan_handoff_status is NOT changed
    old_state: str                # "needs_plan"           (must be a known stage id)
    new_state: str                # "needs_implementation" (must be a known stage id)
    implementer: str              # "khushal"              (must be a known Implementer value)
    effect: str                   # "user_takeover"        (must be a known TicketStatus value)


@dataclass(frozen=True)
class WorkflowDefinition:
    type_id: str                  # "coding"
    label: str                    # "Coding"
    stages: tuple[Stage, ...]     # FULL linear order: leading needs_kickoff … trailing done
    dropped_stage: Stage          # the reserved exceptional terminal, outside the linear order
    fields: tuple[FieldDef, ...]  # ordered field set, led by kickoff
    worker_profile: WorkerProfile
    transition_hooks: tuple[TransitionHook, ...]
    supports_prefix_reconciliation: bool   # coding = True; read by t_tt03, NOT serialized


# --- serialized manifest (the one shape CLI + web later consume) ----------------


class ManifestStage(TypedDict):
    id: str
    label: str
    gating_field: str | None
    is_terminal: bool


class ManifestField(TypedDict):
    id: str
    label: str


class ManifestDict(TypedDict):
    type_id: str
    label: str
    stages: list[ManifestStage]        # full order incl. needs_kickoff … done (NOT dropped)
    dropped: ManifestStage             # the exceptional terminal
    advance: dict[str, str]            # non-terminal state -> next state
    fields: list[ManifestField]        # ordered, kickoff first
    ceiling_range: list[str]           # stage order minus leading needs_kickoff
    default_ceiling: str               # first entry of ceiling_range
    worker_profile_id: str             # the specialist_skill id (the profile's stable id in v1)
