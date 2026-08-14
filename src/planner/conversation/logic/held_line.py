"""How the messages waiting for a busy agent become the next prompt.

Both conversation systems answer this the same way, so the rules live here rather than
in either of them. Nothing here touches storage, a backend or a clock: it takes the
messages that are waiting and says which of them go in together and what the agent is
given.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from planner.conversation.message_content import MessageContent, MessagePiece, MessageText


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
        carries_a_change = (
            message.model_change is not None or message.reasoning_effort_change is not None
        )
        if carries_a_change and run:
            break
        run.append(message)
    return tuple(run)


def one_prompt_from(run: Sequence[WaitingMessage]) -> MessageContent:
    """Several waiting messages as the single prompt the agent is given.

    Each message keeps its own words and its sender's name, because an agent handed one
    run of text still has to be able to tell who said what. Nothing is summarised or
    reworded, and one message on its own is given exactly as it was sent — the shape only
    appears once there is more than one message to tell apart.
    """
    if len(run) == 1:
        return run[0].content
    pieces: list[MessagePiece] = []
    for message in run:
        first = message.content[0]
        if isinstance(first, MessageText):
            # The name is folded into the message's own first words rather than put beside
            # them as a piece of its own. Every reader of a message already separates one
            # piece from the next, so a piece of its own would be spaced twice.
            pieces.append(MessageText(text=f"{message.sender_label}:\n{first.text}"))
            pieces.extend(message.content[1:])
            continue
        pieces.append(MessageText(text=f"{message.sender_label}:"))
        pieces.extend(message.content)
    return tuple(pieces)
