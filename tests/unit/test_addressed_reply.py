from __future__ import annotations

import pytest

from planner.conversation.logic.addressed_reply import (
    send_message_target,
    with_authenticated_reply_directive,
)
from planner.conversation.message_content import message_content_text, text_message_content
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind


@pytest.mark.parametrize(
    ("principal", "target"),
    (
        (OWNER_PRINCIPAL, "--owner"),
        (CHIEF_PRINCIPAL, "--chief"),
        (Principal(PrincipalKind.ticket, "t_worker"), "--ticket t_worker"),
        (Principal(PrincipalKind.sprint_item, "si_goal"), "--sprint-item si_goal"),
    ),
)
def test_each_authenticated_principal_has_one_exact_send_message_target(
    principal: Principal, target: str
) -> None:
    assert send_message_target(principal) == target
    wire = message_content_text(
        with_authenticated_reply_directive(text_message_content("hello"), (principal,))
    )
    assert f'`panels send-message {target} --message "<reply>"`' in wire


def test_unaddressed_content_is_not_changed() -> None:
    content = text_message_content("maintenance")
    assert with_authenticated_reply_directive(content, ()) is content


def test_a_batch_names_each_distinct_sender_once_in_first_seen_order() -> None:
    ticket = Principal(PrincipalKind.ticket, "t_sender")
    wire = message_content_text(
        with_authenticated_reply_directive(
            text_message_content("batched"),
            (OWNER_PRINCIPAL, ticket, OWNER_PRINCIPAL),
        )
    )
    assert wire.count("panels send-message --owner") == 1
    assert wire.count("panels send-message --ticket t_sender") == 1
    assert wire.index("--owner") < wire.index("--ticket t_sender")
