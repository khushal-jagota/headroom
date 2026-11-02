"""The claude adapter against the claude that is actually installed on this machine.

The scripted tests prove the adapter builds the input the SDK documents. These prove the
documentation is what claude does. They cost real model calls, so they are opt-in: set
``PANELS_REAL_CLAUDE_TESTS=1`` with claude installed and logged in.

There is one question here a script cannot answer, and it is the reason this file exists.
A message that is only words goes to the SDK as a string, which is how it has always gone.
A message with a picture in it goes the SDK's other way — one user message carrying content
blocks — and whether the CLI on the other end really accepts that, and really shows the
model the picture, is a fact about claude rather than about this code. A picture path that
works against a fake and not against claude is exactly the kind of thing that would
otherwise be found weeks later.

So the exercise sends a picture whose content the model can only report if it actually saw
it, and reads the answer.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp

import pytest
from tests.support.conversation_claude_agent_sdk_bench import _RecordingSink

from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.backends.contracts import BackendSteerAccepted, TurnToken
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.message_content import (
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles

REAL_CLAUDE_TESTS_ENVIRONMENT_NAME = "PANELS_REAL_CLAUDE_TESTS"
CLAUDE_EXECUTABLE = shutil.which("claude")

# The cheapest model this account can reach, so an exercise costs as little as one can.
CHEAP_MODEL = "haiku"

real_claude_only = pytest.mark.skipif(
    os.environ.get(REAL_CLAUDE_TESTS_ENVIRONMENT_NAME) != "1" or CLAUDE_EXECUTABLE is None,
    reason=f"set {REAL_CLAUDE_TESTS_ENVIRONMENT_NAME}=1 with claude installed to run this",
)


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 300.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


def _resolved_start(workspace: Path) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id="real-claude",
        backend_key=ConversationBackendKey.claude,
        model=CHEAP_MODEL,
        reasoning_effort=None,
        role_materials=None,
        workspace_folder=workspace,
        access=ConversationAccess.full,
    )


def _real_child(
    workspace: Path, sink: _RecordingSink, message_files: ConversationMessageFiles
) -> ClaudeAgentSdkBackendChild:
    assert CLAUDE_EXECUTABLE is not None
    return ClaudeAgentSdkBackendChild(
        launch=ClaudeAgentSdkChildLaunch(claude_executable=Path(CLAUDE_EXECUTABLE)),
        resolved_start=_resolved_start(workspace),
        event_sink=sink,
        message_files=message_files,
    )




@pytest.mark.real_provider_homes
@real_claude_only
def test_a_steer_the_real_claude_folds_into_its_turn_still_lets_it_end(
    tmp_path: Path,
) -> None:
    """The hang itself, against the claude that produced it.

    The turn runs a shell command slow enough to steer into. Claude takes the steer into
    the turn already running and answers both in one reply, so no second turn is started
    and no result ever names the steer. The steer's own lifecycle receipt is the only
    thing that says it is over.

    The assertion is the wait. A turn that never ends fails this by timing out, which is
    exactly what a person saw.
    """

    async def exercise() -> None:
        sink = _RecordingSink()
        message_files = ConversationMessageFiles(str(Path(mkdtemp()) / "planner.db"))
        child = _real_child(tmp_path, sink, message_files)
        turn_token = TurnToken(conversation_id="real-claude", turn_number=1)
        await child.start(_resolved_start(tmp_path), vendor_session_cursor=None)
        try:
            content = text_message_content(
                "Run the bash command: sleep 12. Then reply with exactly FIRST."
            )
            await child.write_prompt(
                turn_token,
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
                model_change=None,
                reasoning_effort_change=None,
            )
            while not sink.tools_started:
                await asyncio.sleep(0.1)

            steered = text_message_content(
                "Also append the word STEERED to that reply."
            )
            outcome = await child.steer(
                turn_token, steered, sender_label="owner"
            )
            assert isinstance(outcome, BackendSteerAccepted)

            await sink.wait_for_the_turn_to_end()

            assert [ending["turn"] for ending in sink.endings] == [turn_token]
        finally:
            await child.stop()

    _run(exercise)
