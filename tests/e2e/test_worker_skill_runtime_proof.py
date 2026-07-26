"""Proof that a Worker actually reads the guidance Panels installed for it.

Two things have to be true for a Worker to know its job, and neither is provable from a
database row. The role Panels gives the conversation has to arrive as text in the agent's
own prompt, and the specialist skill Panels provisions has to be on disk, under the home
the agent was launched with, in a form the agent can open. Both are proved here the only
honest way: a real agent process on a real wire, reading a real file, answering with what
it found.

The agent is the assertion. It refuses the turn if the role is missing from its prompt, and
refuses it again if the installed skill does not carry the guidance — so the turn ending as
a failure is the test failing, and the acknowledgement coming back is the claim made.
"""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.support.worker_skill_proof_acp_agent import (
    ROLE_DIRECTIVE,
    WORKTREE_ACKNOWLEDGEMENT,
    WORKTREE_GUIDANCE,
)

from planner.conversation2.backends.hermes_acp import (
    AcpChildLaunch,
    HermesAcpBackendChildFactory,
)
from planner.conversation2.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
)
from planner.conversation2.events import ConversationEventKind
from planner.conversation2.storage import ConversationStore
from planner.conversation2.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema
from planner.environments.hermes_home import provision_planner_home_skills

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROOF_AGENT = REPOSITORY_ROOT / "tests" / "support" / "worker_skill_proof_acp_agent.py"
CONVERSATION_ID = "conversation-worker-skill-proof"


def test_ticket_worker_reads_provisioned_worktree_guidance_through_a_real_prompt(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        runtime_root = tmp_path / "runtime"
        hermes_home = runtime_root / "hermes-home"
        database_parent = runtime_root / "state"
        provision_planner_home_skills(
            hermes_home,
            configured_database_parent=database_parent,
            panels_skills_source_root=REPOSITORY_ROOT / "src" / "planner" / "skills",
        )

        # What Panels installed, before any agent is asked to read it: the skill is a link
        # into the folder beside the database, and it carries the guidance.
        installed_skill = hermes_home / "skills" / "panels-worker-coding" / "SKILL.md"
        installed_package = installed_skill.parent
        assert installed_package.is_symlink()
        assert installed_package.resolve() == (
            database_parent / "skills" / "panels-worker-coding"
        ).resolve()
        assert WORKTREE_GUIDANCE in installed_skill.read_text(encoding="utf-8")

        database_path = runtime_root / "conversations.db"
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = connect(str(database_path), 5000)
        try:
            create_schema(connection)
        finally:
            connection.close()

        # The real hermes adapter, pointed at an agent this test can hold to account. The
        # launch is a value for exactly this reason, so nothing about the path from Panels
        # to the child is stubbed.
        launch = AcpChildLaunch(
            argv=(sys.executable, str(PROOF_AGENT)),
            environment_overrides=(("HERMES_HOME", str(hermes_home)),),
        )
        factory = HermesAcpBackendChildFactory(launch)
        store = ConversationStore(str(database_path))
        system = SqliteProcessConversationSystem(
            store=store,
            backend_child_factories={key: factory for key in ConversationBackendKey},
        )
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    conversation_id=CONVERSATION_ID,
                    backend_key=ConversationBackendKey.hermes,
                    role_materials=ConversationRoleMaterials(role_text=ROLE_DIRECTIVE),
                    workspace_folder=REPOSITORY_ROOT,
                )
            )
            await system.send(
                CONVERSATION_ID,
                "Read and acknowledge the installed coding Worker guidance.",
                sender_label="loop",
            )
            await _waited_for_the_turn_to_end(store)
        finally:
            await system.shutdown()

        events = await store.read_events_after(CONVERSATION_ID, 0)
        endings = [
            event for event in events if event.kind is ConversationEventKind.turn_ended
        ]
        assert len(endings) == 1, events
        # The agent refuses the turn when either half is missing, so a completed turn is
        # the claim and its text is the evidence.
        assert str(endings[0].payload.ending) == "completed", endings[0].payload
        assert [
            event.payload.text
            for event in events
            if event.kind is ConversationEventKind.agent_message
        ] == [WORKTREE_ACKNOWLEDGEMENT]

    # Playwright's session fixture may already own an event loop when the complete E2E
    # suite reaches this synchronous test. Keep the ACP proof isolated from that loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, exercise()).result()


async def _waited_for_the_turn_to_end(store: ConversationStore, timeout: float = 30.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        events = await store.read_events_after(CONVERSATION_ID, 0)
        if any(event.kind is ConversationEventKind.turn_ended for event in events):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the proof agent's turn never ended")
