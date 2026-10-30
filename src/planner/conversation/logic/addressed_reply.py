"""Trusted wire instructions for replies to authenticated prompt senders."""

from __future__ import annotations

from collections.abc import Iterable
from typing import assert_never

from planner.conversation.message_content import (
    MessageContent,
    MessageText,
    prefix_message_content_text,
)
from planner.core.contracts import Principal, PrincipalKind

_AUTHENTICATED_REPLY_HEADER = "[Authenticated Panels reply requirement]\n"


def with_runtime_prompt_context(content: MessageContent, context: str) -> MessageContent:
    """Add host instructions while preserving the leading authenticated directive.

    That directive is a separate first text piece on the composed wire. The test
    only determines where to insert context; it grants no sender authority.
    """
    if (
        content
        and isinstance(content[0], MessageText)
        and content[0].text.startswith(_AUTHENTICATED_REPLY_HEADER)
    ):
        return (content[0], *prefix_message_content_text(content[1:], context, "\n\n"))
    return prefix_message_content_text(content, context, "\n\n")


def send_message_target(principal: Principal) -> str:
    """Return the exact Panels CLI target for one authenticated principal."""
    match principal.kind:
        case PrincipalKind.owner:
            return "--owner"
        case PrincipalKind.chief:
            return "--chief"
        case PrincipalKind.ticket:
            return f"--ticket {principal.id}"
        case PrincipalKind.manager:
            return f"--manager {principal.id}"
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
        _AUTHENTICATED_REPLY_HEADER
        + "When this requirement exists, it starts the entire prompt with nothing before it, "
        "and every sender-authored byte follows its authenticated sender label.\n"
        "Before you complete this turn, send one explicit reply to each addressed sender.\n"
        "Use each exact target once:\n"
        f"{commands}"
    )
    return (MessageText(directive), *content)
