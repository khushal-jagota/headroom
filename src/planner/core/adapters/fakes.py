"""In-memory fakes for the boundary and gateway adapters. Tests use these; they
never touch the OS or the network. Each records its calls and behaves
deterministically."""

from __future__ import annotations

from dataclasses import dataclass, field

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.base import BoundaryInputs, BoundaryJudgment
from planner.core.errors import ErrorCode, PlannerError


@dataclass
class FakeBoundaryAdapter:
    judgment_result: BoundaryJudgment | None = None
    fail: bool = False
    calls: list[str] = field(default_factory=list)  # method names, in order

    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
        self.calls.append("judgment")
        if self.fail:
            raise RuntimeError("fake boundary failure")
        if self.judgment_result is not None:
            return self.judgment_result
        return BoundaryJudgment(
            focus=f"Focus for {inputs.planning_date}",
            brief_take=f"Brief take for {inputs.planning_date}",
            watchout=f"Watchout for {inputs.planning_date}",
            if_today_lands=f"If today lands for {inputs.planning_date}",
        )


@dataclass
class EchoGatewayAdapter:
    calls: list[tuple[str | None, str, str]] = field(default_factory=list)
    next_session: int = 1

    def status(self) -> GatewayStatus:
        return GatewayStatus(available=True)

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        self.calls.append((session_key, entity_id, text))
        if session_key is None:
            session_key = f"fake-sess-{self.next_session}"
            self.next_session += 1
        return ChatSendResult(reply_text=f"echo: {text}", session_key=session_key)


@dataclass
class OfflineGatewayAdapter:
    def status(self) -> GatewayStatus:
        return GatewayStatus(available=False, detail="gateway offline")

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")
