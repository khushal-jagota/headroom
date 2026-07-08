"""In-memory fakes for gateway adapters. Tests use these; they never touch the
OS or the network. Each records its calls and behaves deterministically."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from planner.chat.contracts import (
    ChatSendResult,
    ChatStreamChunk,
    CommandCatalog,
    CommandCategory,
    CommandRunResult,
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
    catalog_calls: int = 0                # counts real catalog() work (cache-miss proof)
    next_session: int = 1
    busy: bool = False

    def status(self) -> GatewayStatus:
        return GatewayStatus(available=True)

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
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
        return ChatSendResult(reply_text=f"echo: {text}", session_key=session_key)

    def stream(
        self, session_key: str | None, entity_id: str, text: str, mode: str
    ) -> Iterator[ChatStreamChunk]:
        if mode == "command":
            result = self.run_command(session_key, entity_id, text)
        else:
            send_result = self.send(session_key, entity_id, text)
            result = CommandRunResult(
                reply_text=send_result.reply_text,
                session_key=send_result.session_key,
                kind="assistant",
            )
        midpoint = max(1, len(result.reply_text) // 2)
        for token in (result.reply_text[:midpoint], result.reply_text[midpoint:]):
            if token:
                yield ChatStreamChunk(type="token", text=token)
        yield ChatStreamChunk(
            type="done",
            reply_text=result.reply_text,
            session_key=result.session_key,
            kind=result.kind,
        )

    def catalog(self) -> CommandCatalog:
        self.catalog_calls += 1
        return CANNED_CATALOG

    def run_command(
        self, session_key: str | None, entity_id: str, command: str
    ) -> CommandRunResult:
        self.command_calls.append((session_key, entity_id, command))
        if self.busy:
            raise PlannerError(
                ErrorCode.already_running,
                "an agent is already running on this ticket",
                {"entity_id": entity_id, "session_key": session_key},
            )
        if session_key is None:
            session_key = f"fake-sess-{self.next_session}"
            self.next_session += 1
        parts = command.strip().split(maxsplit=1)
        token = parts[0].lower() if parts else ""
        name = CANNED_CATALOG.canon.get(token, token)
        if name in _CANNED_SKILL_NAMES:  # skill -> command.dispatch -> prompt.submit (a model turn)
            return CommandRunResult(
                reply_text=f"skill {name} loaded", session_key=session_key, kind="assistant"
            )
        if name == "/compress":
            # /compress's real feedback rides in slash.exec's `warning`; the real adapter
            # combines output + warning (real.py). Model that combined system line here.
            output, warning = "(no output)", "compressed 40 → 8 messages"
            reply = (output + "\n" + warning).strip() if warning else output
            return CommandRunResult(reply_text=reply, session_key=session_key, kind="system")
        # everything else -> slash.exec display output (no model turn)
        return CommandRunResult(
            reply_text=f"exec: {command.strip()}", session_key=session_key, kind="system"
        )


@dataclass
class OfflineGatewayAdapter:
    def status(self) -> GatewayStatus:
        return GatewayStatus(available=False, detail="gateway offline")

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def stream(
        self, session_key: str | None, entity_id: str, text: str, mode: str
    ) -> Iterator[ChatStreamChunk]:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def catalog(self) -> CommandCatalog:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")

    def run_command(
        self, session_key: str | None, entity_id: str, command: str
    ) -> CommandRunResult:
        raise PlannerError(ErrorCode.gateway_offline, "gateway offline")
