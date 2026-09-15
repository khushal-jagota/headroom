"""How the messages waiting for a busy agent become the next prompt.

Both conversation systems answer this the same way, so the rules live here rather than
in either of them. Nothing here touches storage, a backend or a clock: it takes the
messages that are waiting and says which of them go in together and what the agent is
given.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from planner.conversation.message_content import (
    MessageContent,
    MessagePiece,
    message_content_starts_with_slash_token,
    sender_labeled_message_content,
)


class WaitingMessage(Protocol):
    """What the rules here need to know about a message that is waiting."""

    @property
    def content(self) -> MessageContent: ...

    @property
    def sender_label(self) -> str: ...

    @property
    def model_change(self) -> str | None: ...

    @property
    def reasoning_effort_change(self) -> str | None: ...


def leading_run_that_can_share_a_turn[MessageT: WaitingMessage](
    waiting: Iterable[MessageT],
) -> tuple[MessageT, ...]:
    """The messages at the front of the line that can go to the agent as one prompt.

    A turn runs on one model, so a message that asks to run on something else starts the
    next turn and carries its change there. Taking it into this one would run the messages
    in front of it on a model their senders never named.
    """
    run: list[MessageT] = []
    for message in waiting:
        command_shaped = message_content_starts_with_slash_token(message.content)
        carries_a_change = (
            message.model_change is not None or message.reasoning_effort_change is not None
        )
        if run and (carries_a_change or command_shaped):
            break
        run.append(message)
        if command_shaped:
            break
    return tuple(run)


def one_prompt_from(run: Sequence[WaitingMessage]) -> MessageContent:
    """Several waiting messages as the single prompt the agent is given.

    The adapter adds the first message's sender name to the complete prompt. This combiner
    adds each later sender name before that message, so every message carries one name.
    Nothing is summarised or reworded, and one message on its own stays unchanged here.
    """
    if len(run) == 1:
        return run[0].content
    pieces: list[MessagePiece] = []
    pieces.extend(run[0].content)
    for message in run[1:]:
        pieces.extend(sender_labeled_message_content(message.content, message.sender_label))
    return tuple(pieces)
