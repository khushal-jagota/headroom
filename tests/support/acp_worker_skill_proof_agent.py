"""ACP subprocess that proves the installed coding Worker skill is readable."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import acp
from acp.schema import AgentMessageChunk, PromptResponse, TextContentBlock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support.acp_scripted_agent import ScriptedAcpAgent

ROLE_DIRECTIVE = (
    "Start with the `panels` skill. It explains the system and is necessary, "
    "then drill through to your identity through the skills layers. "
    "You are a ticket worker."
)
WORKTREE_GUIDANCE = "Always do your work on a worktree and a branch."
WORKTREE_ACKNOWLEDGEMENT = "Acknowledged installed worktree and branch guidance."


class WorkerSkillProofAgent(ScriptedAcpAgent):
    """Read the provisioned specialist skill only after the real ACP prompt arrives."""

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        delivered_text = [item.text for item in prompt if isinstance(item, TextContentBlock)]
        if not delivered_text or delivered_text[0] != ROLE_DIRECTIVE:
            raise RuntimeError("ticket role kickoff was not delivered through ACP")

        hermes_home = Path(os.environ["HERMES_HOME"])
        installed_skill = hermes_home / "skills" / "panels-worker-coding" / "SKILL.md"
        skill_text = installed_skill.read_text(encoding="utf-8")
        if WORKTREE_GUIDANCE not in skill_text:
            raise RuntimeError("installed coding Worker skill lacks worktree and branch guidance")

        await self._emit(  # noqa: SLF001 - this fixture specializes the scripted ACP agent.
            session_id,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="worker-skill-proof",
                content=TextContentBlock(type="text", text=WORKTREE_ACKNOWLEDGEMENT),
            ),
        )
        return PromptResponse(stop_reason="end_turn")


async def _main() -> None:
    await acp.run_agent(WorkerSkillProofAgent(), use_unstable_protocol=True)


if __name__ == "__main__":
    asyncio.run(_main())
