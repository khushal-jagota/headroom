"""Contracts for generic pending context delivered to worker model turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PendingWorkerContext:
    context_key: str
    text: str
    revision: int


@dataclass(frozen=True)
class WorkerContextReceipt:
    context_key: str
    revision: int


@dataclass(frozen=True)
class WorkerContextSnapshot:
    items: tuple[PendingWorkerContext, ...]

    @property
    def receipts(self) -> tuple[WorkerContextReceipt, ...]:
        return tuple(WorkerContextReceipt(item.context_key, item.revision) for item in self.items)


@dataclass(frozen=True)
class PreparedWorkerPrompt:
    model_text: str
    receipts: tuple[WorkerContextReceipt, ...]


class WorkerContextService(Protocol):
    def prepare(self, worker_entity_id: str, prompt_text: str) -> PreparedWorkerPrompt: ...

    def acknowledge(
        self, worker_entity_id: str, receipts: tuple[WorkerContextReceipt, ...]
    ) -> None: ...
