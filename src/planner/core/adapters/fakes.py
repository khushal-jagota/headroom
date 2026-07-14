"""In-memory fakes for gateway adapters. Tests use these; they never touch the
OS or the network. Each records its calls and behaves deterministically."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from planner.chat.contracts import (
    CommandCatalog,
    CommandCategory,
    GatewayStatus,
    HumanChatCompletion,
    HumanChatObservation,
    HumanChatOutputDelta,
)
from planner.core.adapters.base import HumanSessionKeyBinder
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import EmployeeSessionHistory, EmployeeSessionHistoryMessage

# One canned catalog for the whole test suite — two grouped categories plus a
# Skills group. Shape mirrors the live gateway (skills are absent from categories;
# canon keys are lowercased). No child is ever spawned to build it.
CANNED_CATALOG: CommandCatalog = CommandCatalog(
    categories=(
        CommandCategory(
            "Session",
            (("/status", "Show session status"), ("/model", "Pick the model")),
        ),
        CommandCategory("Info", (("/help", "List the commands"),)),
        CommandCategory("Exit", (("/quit", "Exit the session"),)),
    ),
    skills=(("/writing-plans", "Draft a plan"), ("/xurl", "Fetch a URL as markdown")),
    canon={"/st": "/status", "/wp": "/writing-plans"},
    sub={"/model": ["list", "set"]},
)

_CANNED_SKILL_NAMES = frozenset(name for name, _ in CANNED_CATALOG.skills)


@dataclass
class EchoGatewayAdapter:
    calls: list[tuple[str | None, str, str]] = field(default_factory=list)
    command_calls: list[tuple[str | None, str, str]] = field(default_factory=list)
    interrupt_calls: list[tuple[str, str]] = field(default_factory=list)
    histories: dict[str, list[EmployeeSessionHistoryMessage]] = field(default_factory=dict)
    catalog_calls: int = 0                # counts real catalog() work (cache-miss proof)
    next_session: int = 1
    busy: bool = False
    stream_delay_seconds: float = 0.0

    def status(self) -> GatewayStatus:
        return GatewayStatus(available=True)

    def _next_message_time(self, session_key: str) -> int:
        return len(self.histories.setdefault(session_key, [])) + 1

    def _append_turn(
        self, session_key: str, user_text: str, reply_text: str, reply_role: str
    ) -> None:
        history = self.histories.setdefault(session_key, [])
        history.append(
            EmployeeSessionHistoryMessage(
                role="user",
                text=user_text,
                created_at=self._next_message_time(session_key),
            )
        )
        history.append(
            EmployeeSessionHistoryMessage(
                role=reply_role,
                text=reply_text,
                created_at=self._next_message_time(session_key),
            )
        )

    def read_employee_session_history(
        self,
        employee_session_id: str,
        ticket_id: str,
    ) -> EmployeeSessionHistory:
        return EmployeeSessionHistory(
            messages=tuple(self.histories.get(employee_session_id, ())),
            employee_session_id=employee_session_id,
        )

    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
        *,
        require_existing_session: bool = False,
    ) -> Iterator[HumanChatObservation]:
        if self.busy:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id, "session_key": session_key},
            )
        if require_existing_session and session_key is None:
            raise PlannerError(ErrorCode.gateway_offline, "existing session is required")
        if mode == "command" and text == "/new":
            if require_existing_session:
                raise PlannerError(ErrorCode.gateway_offline, "existing session is required")
            candidate_session_key = f"fake-sess-{self.next_session}"
            self.next_session += 1
        elif session_key is None:
            candidate_session_key = f"fake-sess-{self.next_session}"
            self.next_session += 1
        else:
            candidate_session_key = session_key
        session_key = bind_session_key(candidate_session_key)
        if mode == "command":
            self.command_calls.append((session_key, entity_id, text))
        else:
            self.calls.append((session_key, entity_id, text))

        kind: Literal["assistant", "system"] = "assistant"
        reply_text = f"echo: {text}"
        if mode == "command":
            if text == "/new":
                reply_text = "New session started."
                kind = "system"
            else:
                parts = text.strip().split(maxsplit=1)
                token = parts[0].lower() if parts else ""
                name = CANNED_CATALOG.canon.get(token, token)
                if name in _CANNED_SKILL_NAMES:
                    reply_text = f"skill {name} loaded"
                elif name == "/compress":
                    reply_text = "(no output)\ncompressed 40 → 8 messages"
                    kind = "system"
                else:
                    reply_text = f"exec: {text.strip()}"
                    kind = "system"
        visible_text = text.strip() if mode == "command" else text
        self._append_turn(session_key, visible_text, reply_text, kind)

        midpoint = max(1, len(reply_text) // 2)
        for token in (reply_text[:midpoint], reply_text[midpoint:]):
            if token:
                yield HumanChatOutputDelta(token)
        if self.stream_delay_seconds > 0:
            time.sleep(self.stream_delay_seconds)
        yield HumanChatCompletion(reply_text, kind)

    def interrupt(self, session_key: str, entity_id: str) -> None:
        self.interrupt_calls.append((session_key, entity_id))

    def catalog(self) -> CommandCatalog:
        self.catalog_calls += 1
        return CANNED_CATALOG

@dataclass
class OfflineGatewayAdapter:
    def status(self) -> GatewayStatus:
        return GatewayStatus(available=False, detail="gateway offline")

    def read_employee_session_history(
        self,
        employee_session_id: str,
        ticket_id: str,
    ) -> EmployeeSessionHistory:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def run_human_turn(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        bind_session_key: HumanSessionKeyBinder,
        image_paths: tuple[Path, ...] = (),
        *,
        require_existing_session: bool = False,
    ) -> Iterator[HumanChatObservation]:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def interrupt(self, session_key: str, entity_id: str) -> None:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def catalog(self) -> CommandCatalog:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")
