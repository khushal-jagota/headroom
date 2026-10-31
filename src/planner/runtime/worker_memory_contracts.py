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


class MemoryNoticeEligibility(StrEnum):
    eligible = "eligible"
    ineligible = "ineligible"
    held = "held"
    consumed = "consumed"


class MemoryNoticeAcquisition(BaseModel):
    model_config = ConfigDict(frozen=True)
    eligibility: MemoryNoticeEligibility
    reservation: MemoryNoticeReservation | None = None
