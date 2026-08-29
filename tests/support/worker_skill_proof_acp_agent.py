"""An ACP agent that proves a worker can read the skill Panels installed for it.

This is a real process on a real ACP wire, because that is the only way the claim can be
made honestly. A worker's guidance is not a database row and not a UI line: it is a file
on disk that a spawned agent opens, under the HERMES_HOME Panels launched it with. An
agent that read it from anywhere else would prove nothing.

It does two things and refuses to do them out of order. It checks that the role Panels
gave the conversation actually arrived as prompt text — not as configuration the agent was
never told — and only then reads the installed specialist skill and answers with what it
found. Either check failing ends the turn as a failure, which the record carries, so a
break says which half broke.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, cast

import acp
from acp.interfaces import Agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionMode,
    SessionModeState,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    TextContentBlock,
)

ROLE_DIRECTIVE = (
    "Start with the `panels` skill. It explains the system and is necessary, "
    "then drill through to your identity through the skills layers. "
    "You are a ticket worker."
)
WORKTREE_GUIDANCE = "Always do your work on a worktree and a branch."
WORKTREE_ACKNOWLEDGEMENT = "Acknowledged installed worktree and branch guidance."

SESSION_ID = "worker-skill-proof-session"

# Every conversation names the model it runs on, so this agent has to be able to be put on
# one. It offers the model as an ordinary session config option, which is how the protocol
# says a model is chosen now.
MODEL_CONFIGURATION_OPTION_ID = "model"
MODEL = "proof-model"
FULL_ACCESS_MODE_ID = "dont_ask"


def _diagnostic(message: str) -> None:
    print(f"worker-skill-proof-agent: {message}", file=sys.stderr, flush=True)


class WorkerSkillProofAgent:
    """The smallest agent that can make the claim, and nothing else."""

    def __init__(self) -> None:
        self._client: Any = None
        self._mode: str | None = None

    def on_connect(self, client: Any) -> None:
        self._client = client

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        del kwargs
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(
                name="panels-worker-skill-proof-agent", version="1.0.0"
            ),
        )

    async def new_session(self, cwd: str, **kwargs: Any) -> NewSessionResponse:
        del cwd, kwargs
        return NewSessionResponse(
            session_id=SESSION_ID,
            config_options=[self._model_option()],
            modes=SessionModeState(
                current_mode_id="default",
                available_modes=[
                    SessionMode(id="default", name="Default"),
                    SessionMode(id=FULL_ACCESS_MODE_ID, name="Don't Ask"),
                ],
            ),
        )

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse:
        del session_id, kwargs
        self._mode = mode_id
        return SetSessionModeResponse()

    async def set_config_option(
        self, config_id: str, session_id: str, value: Any, **kwargs: Any
    ) -> SetSessionConfigOptionResponse:
        del config_id, session_id, value, kwargs
        return SetSessionConfigOptionResponse(config_options=[self._model_option()])

    def _model_option(self) -> SessionConfigOptionSelect:
        return SessionConfigOptionSelect(
            type="select",
            id=MODEL_CONFIGURATION_OPTION_ID,
            name="Model",
            category="model",
            current_value=MODEL,
            options=[SessionConfigSelectOption(value=MODEL, name=MODEL)],
        )

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        del kwargs
        if self._mode != FULL_ACCESS_MODE_ID:
            _diagnostic("the conversation's full-access mode never reached the agent")
            raise RuntimeError("the conversation's full-access mode never reached the agent")
        delivered = "\n".join(
            block.text for block in prompt if isinstance(block, TextContentBlock)
        )
        if ROLE_DIRECTIVE not in delivered:
            _diagnostic("the ticket role never reached the agent's prompt")
            raise RuntimeError("the ticket role never reached the agent's prompt")

        hermes_home = Path(os.environ["HERMES_HOME"])
        installed_skill = hermes_home / "skills" / "panels-worker-coding" / "SKILL.md"
        skill_text = installed_skill.read_text(encoding="utf-8")
        if WORKTREE_GUIDANCE not in skill_text:
            _diagnostic("installed coding Worker skill lacks worktree and branch guidance")
            raise RuntimeError("installed coding Worker skill lacks worktree guidance")

        await self._client.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=WORKTREE_ACKNOWLEDGEMENT),
            ),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, **kwargs: Any) -> None:
        del kwargs
        return None


async def _main() -> None:
    await acp.run_agent(cast(Agent, WorkerSkillProofAgent()), use_unstable_protocol=True)


if __name__ == "__main__":
    asyncio.run(_main())
