"""In-memory fakes for the three adapters. Tests use these; they never touch the
OS or the network. Each records its calls and behaves deterministically."""

from __future__ import annotations

from dataclasses import dataclass, field

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.base import (
    BoundaryInputs,
    BoundaryJudgment,
    SpawnRequest,
    SpawnResult,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.days.contracts import NodeStatus, PlanNode, PlanRoot, PlanTree

_FIRST_FAKE_PID = 90001


@dataclass
class FakeSpawnAdapter:
    calls: list[SpawnRequest] = field(default_factory=list)
    results: list[SpawnResult] = field(default_factory=list)  # scripted, popped FIFO
    next_pid: int = _FIRST_FAKE_PID

    def spawn(self, request: SpawnRequest) -> SpawnResult:
        self.calls.append(request)
        if self.results:
            return self.results.pop(0)
        pid = self.next_pid
        self.next_pid += 1
        return SpawnResult(ok=True, pid=pid)

    def is_pid_alive(self, _pid: int) -> bool:
        # Fakes never spawn a real process, so a fake pid is always "alive". The
        # dispatcher's test-mode path uses _pid_alive_always regardless (§7.4); this
        # only exists so the fake satisfies the SpawnAdapter protocol.
        return True

    def reap_finished_children(self) -> None:
        # Nothing was ever really spawned, so there is nothing to reap.
        return None


@dataclass
class FakeBoundaryAdapter:
    judgment_result: BoundaryJudgment | None = None
    replan_tree: PlanTree | None = None
    replan_child_node: PlanNode | None = None
    fail: bool = False
    calls: list[str] = field(default_factory=list)  # method names, in order

    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
        self.calls.append("judgment")
        if self.fail:
            raise RuntimeError("fake boundary failure")
        if self.judgment_result is not None:
            return self.judgment_result
        children = [
            PlanNode(
                ticket_id=entry.get("id"),
                note=str(entry.get("title", "")),
                status=NodeStatus.proposed,
                position=index,
            )
            for index, entry in enumerate(inputs.carryover)
        ]
        tree = PlanTree(root=PlanRoot(focus="Fake focus"), children=children)
        return BoundaryJudgment(
            brief_markdown=f"# Brief for {inputs.planning_date}", plan_tree=tree
        )

    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
        self.calls.append("replan_root")
        if self.fail:
            raise RuntimeError("fake boundary failure")
        if self.replan_tree is not None:
            return self.replan_tree
        return PlanTree(root=PlanRoot(focus="Fake replanned focus"), children=[])

    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
        self.calls.append("replan_child")
        if self.fail:
            raise RuntimeError("fake boundary failure")
        if self.replan_child_node is not None:
            return self.replan_child_node
        return PlanNode(
            ticket_id=child.ticket_id,
            note="Fake replanned child",
            status=NodeStatus.proposed,
            position=child.position,
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
