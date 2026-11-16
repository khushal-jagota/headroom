"""The authenticated identity of one Panels agent conversation."""
from dataclasses import dataclass
from typing import Final

from planner.core.contracts import Principal

LAUNCH_TOKEN_ENVIRONMENT_NAME: Final = "PLAN_LAUNCH_TOKEN"
UNIDENTIFIED_LAUNCH_TOKEN: Final = "unidentified"


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    principal: Principal
    conversation_id: str
