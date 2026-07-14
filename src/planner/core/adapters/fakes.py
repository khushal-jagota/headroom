"""In-memory fakes for gateway adapters. Tests use these; they never touch the
OS or the network. Each records its calls and behaves deterministically."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from planner.chat.contracts import (
    ChatHistory,
    ChatMessage,
    ChatStreamChunk,
    CommandCatalog,
    CommandCategory,
    GatewayStatus,
)
from planner.core.errors import ErrorCode, PlannerError

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
    histories: dict[str, list[ChatMessage]] = field(default_factory=dict)
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
            ChatMessage(
                role="user",
                text=user_text,
                created_at=self._next_message_time(session_key),
            )
        )
        history.append(
            ChatMessage(
                role=reply_role,
                text=reply_text,
                created_at=self._next_message_time(session_key),
            )
        )

    def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
        if session_key is None:
            return ChatHistory(messages=(), session_key=None)
        return ChatHistory(
            messages=tuple(self.histories.get(session_key, ())), session_key=session_key
        )

    def stream(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        on_session_key: Callable[[str], None] | None = None,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[ChatStreamChunk]:
        if mode == "command":
            self.command_calls.append((session_key, entity_id, text))
        else:
            self.calls.append((session_key, entity_id, text))
        if self.busy:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id, "session_key": session_key},
            )
        if session_key is None:
            session_key = f"fake-sess-{self.next_session}"
            self.next_session += 1
        if on_session_key is not None:
            on_session_key(session_key)

        kind = "assistant"
        reply_text = f"echo: {text}"
        if mode == "command":
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

        yield ChatStreamChunk(type="session", session_key=session_key)
        midpoint = max(1, len(reply_text) // 2)
        for token in (reply_text[:midpoint], reply_text[midpoint:]):
            if token:
                yield ChatStreamChunk(type="token", text=token)
        if self.stream_delay_seconds > 0:
            time.sleep(self.stream_delay_seconds)
        yield ChatStreamChunk(
            type="done",
            reply_text=reply_text,
            session_key=session_key,
            kind=kind,
        )

    def interrupt(self, session_key: str, entity_id: str) -> None:
        self.interrupt_calls.append((session_key, entity_id))

    def catalog(self) -> CommandCatalog:
        self.catalog_calls += 1
        return CANNED_CATALOG

@dataclass
class OfflineGatewayAdapter:
    def status(self) -> GatewayStatus:
        return GatewayStatus(available=False, detail="gateway offline")

    def history(self, session_key: str | None, entity_id: str) -> ChatHistory:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def stream(
        self,
        session_key: str | None,
        entity_id: str,
        text: str,
        mode: str,
        on_session_key: Callable[[str], None] | None = None,
        image_paths: tuple[Path, ...] = (),
    ) -> Iterator[ChatStreamChunk]:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def interrupt(self, session_key: str, entity_id: str) -> None:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def catalog(self) -> CommandCatalog:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")
