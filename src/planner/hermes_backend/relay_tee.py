"""The tee-observer seam. S1 ships the protocol + test observers only; no product
consumer is registered anywhere in `src/` (plan §1, §10)."""

from __future__ import annotations

import enum
from typing import Protocol

from planner.minds.gateway import JsonDict


class RelayFrameDirection(enum.Enum):
    FROM_DOWNSTREAM_TO_CHILD = "from_downstream_to_child"
    FROM_CHILD_TO_DOWNSTREAM = "from_child_to_downstream"


class RelayTeeObserver(Protocol):
    """Every observed frame is labeled with the employee (contract line 81). The relay
    wraps each `observe` call in try/except so a broken observer never kills a reader
    or a forward."""

    def observe(
        self, *, employee_entity_id: str, direction: RelayFrameDirection, frame: JsonDict
    ) -> None: ...
