"""Caller-owned permission facts. Consumption never establishes delivery."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MemoryNoticeEventKey(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticket_id: str
    conversation_id: str
    context_compacted_sequence: int = Field(gt=0)


class MemoryNoticeReservation(BaseModel):
    model_config = ConfigDict(frozen=True)
    key: MemoryNoticeEventKey
    sender_message_id: str
    consumed_at: int


class MemoryNoticeHold(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticket_id: str
    conversation_id: str
    context_compacted_sequence: int | None
    created_at: int
    reason: str


class MemoryNoticeEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)
    conversation_id: str
    sequence: int
    kind: str
    sender_message_id: str


class MemoryNoticeInspection(BaseModel):
    model_config = ConfigDict(frozen=True)
    reservations: tuple[MemoryNoticeReservation, ...] = ()
    holds: tuple[MemoryNoticeHold, ...] = ()
    evidence: tuple[MemoryNoticeEvidence, ...] = ()


class MemoryNoticeEligibility(StrEnum):
    eligible = "eligible"
    ineligible = "ineligible"
    held = "held"
    consumed = "consumed"


class MemoryNoticeAcquisition(BaseModel):
    model_config = ConfigDict(frozen=True)
    eligibility: MemoryNoticeEligibility
    reservation: MemoryNoticeReservation | None = None
