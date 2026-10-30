"""Running-turn intent and exact prewrite checks use the existing core harness."""

import asyncio
import time
from pathlib import Path

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_conversation_system import _FakeBackendChild, _Harness
from tests.unit.test_current_model_resolution import (
    ScopedCatalogue,
    context,
    observation,
    row,
    setup,
)

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.admission.service import current_admission_expectation
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    BackendEmissionInputs,
    BackendSteerOutcome,
    TurnToken,
    current_emission_check,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    HeldPromptPromotionMode,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
    WorkspacePathPolicy,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.model_resolution.contracts import (
    CatalogWorkspaceBinding,
    SelectionKind,
)
from planner.core.db import connect
from planner.runtime import conversation_start
from planner.tickets import data as tickets


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("reject_final", [False, True])
def test_steer_uses_running_intent_and_preserves_prewrite_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: bool, reject_final: bool
) -> None:
    stamp = int(time.time())
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(
        backend_key=ConversationBackendKey.claude,
        workspace=binding,
        localized_workspace_folder=tmp_path,
        account_generation=None,
    )

    class Source(ScopedCatalogue):
        def verify_context(self, expected: object) -> bool:
            return expected == self.current_context

    source = Source(
        observation(
            (row("haiku", kind=SelectionKind.provider_alias, target="claude-haiku", efforts=()),),
            launch=launch,
            stamp=stamp,
        )
    )
    service, _, path = setup(tmp_path, source)
    service._now = time.time
    harness = _Harness(path)
    harness.clock.now = float(stamp)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    original_steer = _FakeBackendChild.steer
    requirements: list[AdmissionExpectation] = []

    async def guarded_steer(
        self: _FakeBackendChild,
        turn_token: TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent | None = None,
        sender_label: str,
    ) -> BackendSteerOutcome:
        requirement = current_admission_expectation()
        assert requirement is not None
        requirements.append(requirement)
        assert requirement.launch_token == "haiku"
        assert requirement.turn == turn_token
        check = current_emission_check()
        assert check is not None
        prepared = BackendEmissionInputs("haiku", None, tmp_path)
        with pytest.raises(AdmissionRefused):
            check(BackendEmissionInputs("sonnet", None, tmp_path))
        check(prepared)
        if reject_final:
            raise AdmissionRefused("known refusal before user bytes")
        return await original_steer(
            self, turn_token, content, sender_content=sender_content, sender_label=sender_label
        )

    monkeypatch.setattr(_FakeBackendChild, "steer", guarded_steer)

    async def run() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                worker_type="coding",
                title="Running Claude selection",
                principal=OWNER_PRINCIPAL,
                now=stamp,
                title_max_chars=200,
                employee_backend="claude",
                employee_launch_model="haiku",
                selection_kind=SelectionKind.provider_alias,
                employee_launch_reasoning_effort=None,
            )
            conn.execute(
                "UPDATE tickets SET employee_launch_reasoning_effort=NULL WHERE id=?", (ticket.id,)
            )
            conn.commit()
            async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                sent = await conversation_start.send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("start"),
                    conversation_id=None,
                    sender_label="Owner",
                    now=stamp,
                )
            conversation_id = sent.conversation_id
            assert conversation_id is not None
            state = await harness.system._conversation_state(conversation_id)
            assert state is not None and state.running_turn is not None
            basis = state.running_turn.launch_validation
            assert basis is not None and basis.selection.submitted_token == "haiku"
            assert basis.launch_token == "haiku"
            # This later authored choice belongs to the next turn, not to the steer.
            conn.execute(
                "UPDATE tickets SET employee_launch_model='sonnet' WHERE id=?", (ticket.id,)
            )
            conn.commit()
            if held:
                queued = await harness.system.send(
                    conversation_id,
                    text_message_content("steer"),
                    sender_label="Owner",
                )
                assert isinstance(queued, PromptDeliveryQueued)
                pending = (await harness.system.held_prompts(conversation_id))[0]
                fate = await harness.system.promote_held_prompt(
                    conversation_id,
                    pending.held_prompt_id,
                    HeldPromptPromotionMode.steer,
                )
            else:
                await harness.system.send(
                    conversation_id,
                    text_message_content("steer"),
                    sender_label="Owner",
                    mode=PromptDeliveryMode.steer,
                )
            assert len(requirements) == 1
            assert requirements[0].selection == basis.selection
            assert (
                conn.execute(
                    "SELECT employee_launch_model FROM tickets WHERE id=?", (ticket.id,)
                ).fetchone()[0]
                == "sonnet"
            )
            assert state.running_turn is not None and state.running_turn.launch_validation == basis
            assert len(harness.backend(conversation_id).steer_tokens) == (0 if reject_final else 1)
            if reject_final and held:
                assert isinstance(fate, PromptDeliveryRefused)
            elif reject_final:
                assert await harness.system.held_prompts(conversation_id)
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(run())
