"""Light proof that a real Worker ACP child sees installed repository guidance."""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    NewSessionRequest,
    PromptRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
)
from acp.transports import default_environment

from planner.conversation import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ConversationEmployee,
    ProtocolUpdateRejectedPayload,
    ReverseServiceCapabilities,
    SdkAcpEmployeeChildFactory,
    build_panels_initialize_request,
)
from planner.conversation.hermes_backend_configuration import provision_planner_home_skills
from planner.conversation.role_skill_kickoff import RoleSkillKickoffAcpEmployeeChildFactory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_AGENT = REPOSITORY_ROOT / "tests" / "support" / "acp_worker_skill_proof_agent.py"
WORKTREE_ACKNOWLEDGEMENT = "Acknowledged installed Worktree lifecycle guidance."


class _UnusedTurnStrategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def test_ticket_worker_reads_provisioned_worktree_guidance_through_acp(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        runtime_root = tmp_path / "runtime"
        hermes_home = runtime_root / "hermes-home"
        database_parent = runtime_root / "state"
        panels_skills_source_root = REPOSITORY_ROOT / "src" / "planner" / "skills"
        provision_planner_home_skills(
            hermes_home,
            configured_database_parent=database_parent,
            panels_skills_source_root=panels_skills_source_root,
        )

        installed_skill = hermes_home / "skills" / "panels-worker-coding" / "SKILL.md"
        installed_package = installed_skill.parent
        assert installed_package.is_symlink()
        assert installed_package.resolve() == (
            panels_skills_source_root / "panels-worker-coding"
        ).resolve()
        assert "## Worktree lifecycle" in installed_skill.read_text(encoding="utf-8")

        definition = AgentBackendDefinition(
            backend_key="worker-skill-proof",
            argv=(sys.executable, str(SCRIPTED_AGENT)),
            inherited_environment_names=tuple(default_environment()),
            environment_overrides=(("HERMES_HOME", str(hermes_home)),),
            expected_agent_name="panels-scripted-agent",
            expected_agent_version="1.0.0",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=False,
                observes_compaction=False,
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False,
                terminal=False,
                permission=True,
            ),
            working_directory_resolver=lambda employee: employee.workspace_roots[0],
            turn_strategy=_UnusedTurnStrategy(),
        )
        employee = ConversationEmployee(
            employee_id="employee-worker-skill-proof",
            entity_kind="ticket",
            entity_id="ticket-worker-skill-proof",
            workspace_roots=(REPOSITORY_ROOT,),
            backend_key=definition.backend_key,
        )
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

        async def ingress(
            item: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            received.append(item)

        async def permission(
            request: RequestPermissionRequest,
        ) -> RequestPermissionResponse:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(
                    outcome="selected",
                    option_id=request.options[0].option_id,
                )
            )

        async def death(cause: BaseException | None) -> None:
            if cause is not None:
                raise cause

        factory = RoleSkillKickoffAcpEmployeeChildFactory(SdkAcpEmployeeChildFactory(definition))
        child = await factory.create(employee, 1, ingress, permission, death)
        try:
            await child.initialize(build_panels_initialize_request(definition))
            created = await child.new_session(
                NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
            )
            response = await child.prompt(
                PromptRequest(
                    session_id=created.session_id,
                    prompt=[
                        TextContentBlock(
                            type="text",
                            text="Read and acknowledge the installed coding Worker guidance.",
                        )
                    ],
                )
            )
            assert response.stop_reason == "end_turn"
            acknowledgements = [
                item.update.content.text
                for item in received
                if isinstance(item, SessionNotification)
                and isinstance(item.update, AgentMessageChunk)
                and isinstance(item.update.content, TextContentBlock)
            ]
            assert acknowledgements == [WORKTREE_ACKNOWLEDGEMENT]
        finally:
            await child.close()

    # Playwright's session fixture may already own an event loop when the complete E2E
    # suite reaches this synchronous test. Keep the ACP proof isolated from that loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, exercise()).result()
