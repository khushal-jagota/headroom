"""Durable evidence and actual delivery state for deployment attention."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AttentionStatus = Literal[
    "pending", "queued", "started", "delivery_uncertain", "resolved", "superseded"
]


@dataclass(frozen=True)
class DeploymentAttention:
    evidence_identity: str
    deployment_id: str | None
    sender_message_id: str
    message: str
    status: AttentionStatus = "pending"
    conversation_id: str | None = None
    detail: str | None = None
