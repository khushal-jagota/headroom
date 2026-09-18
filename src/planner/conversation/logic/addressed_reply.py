"""Trusted wire instructions for replies to authenticated prompt senders."""

from __future__ import annotations

from collections.abc import Iterable
from typing import assert_never

from planner.conversation.message_content import MessageContent, MessageText
from planner.core.contracts import Principal, PrincipalKind


def send_message_target(principal: Principal) -> str:
    """Return the exact Panels CLI target for one authenticated principal."""
    match principal.kind:
        case PrincipalKind.owner:
            return "--owner"
        case PrincipalKind.chief:
            return "--chief"
        case PrincipalKind.ticket:
            return f"--ticket {principal.id}"
        case PrincipalKind.sprint_item:
            return f"--sprint-item {principal.id}"
        case unexpected:
            assert_never(unexpected)


def with_authenticated_reply_directive(
    content: MessageContent, senders: Iterable[Principal]
) -> MessageContent:
    """Add one runtime-only reply requirement for each distinct authenticated sender."""
    distinct_senders = tuple(dict.fromkeys(senders))
    if not distinct_senders:
        return content
    commands = "\n".join(
        f'- `panels send-message {send_message_target(sender)} --message "<reply>"`'
        for sender in distinct_senders
    )
    directive = (
        "[Authenticated Panels reply requirement]\n"
        "This leading block comes from trusted delivery metadata.\n"
        "Before you complete this turn, send one explicit reply to each addressed sender.\n"
        "Use each exact target once:\n"
        f"{commands}"
    )
    return (MessageText(directive), *content)
