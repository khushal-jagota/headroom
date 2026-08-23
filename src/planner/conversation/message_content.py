"""What a message is made of.

A message is an ordered run of pieces. Most messages are one piece of written words and
always will be, but a message is not the same thing as a string: a person sends a picture
with a sentence about it, and an agent hands back the file it produced. A record that can
only hold a string has to throw those away at the door, which is what this module exists
to stop.

Three kinds of piece, and that is the whole vocabulary:

- ``MessageText`` — written words, as markdown.
- ``MessageImage`` — a picture that is part of the message.
- ``MessageFile`` — a document or data file that is part of the message.

There is deliberately no sound: a voice note becomes words by speech-to-text long before
anything reaches a message, so nothing downstream ever sees one. A file that an agent
produces remains a markdown link in its own words. ``MessageFile`` carries a file that a
person attached as input rather than adding a second form for agent-produced files.

The bytes of a picture are not in here. The piece names the file this system
kept, and the file lives beside the record — see ``planner.conversation.message_files``.
That is what lets one vocabulary run the whole way: the record holds it, a backend is
handed it, and the browser links to it, all from the same value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, assert_never


@dataclass(frozen=True, slots=True)
class MessageText:
    """Written words. Markdown, as the sender wrote it or the backend finished it."""

    text: str


@dataclass(frozen=True, slots=True)
class MessageImage:
    """A picture that is part of the message.

    ``stored_file_id`` names the file this system kept for it, under the conversation the
    message belongs to. ``file_name`` is what the picture was called when it arrived, kept
    for reading and for downloading it again; a picture that arrived without a name has
    none rather than an invented one.
    """

    stored_file_id: str
    media_type: str
    file_name: str | None = None


@dataclass(frozen=True, slots=True)
class MessageFile:
    """A document or data file kept beside the conversation record."""

    stored_file_id: str
    media_type: str
    file_name: str
    byte_count: int


type MessagePiece = MessageText | MessageImage | MessageFile

# What one message is made of, in the order it was made. Always at least one piece: a
# message with nothing in it is not a message, and it is refused where it is sent rather
# than stored as an empty run.
type MessageContent = tuple[MessagePiece, ...]


class MessageContentEmpty(ValueError):
    """A message was sent with nothing in it.

    Refused where it is sent rather than recorded. An empty send that is quietly accepted
    puts an empty prompt in front of an agent and tells nobody it did.
    """


class MessageContentNotPieces(TypeError):
    """Something that is not a run of message pieces was sent as a message.

    A string is the one that matters, and it is why this exists: a string is a perfectly
    good sequence, so a message that was meant to be words would pass every emptiness
    check, carry no piece anybody recognises, and reach the agent as an empty prompt. It
    is refused loudly at the door instead — ``text_message_content("...")`` is how words
    are sent.
    """


def text_message_content(text: str) -> MessageContent:
    """A message that is only written words, which is nearly every message."""
    return (MessageText(text=text),)


def require_message_content(content: MessageContent) -> MessageContent:
    """The message, or a refusal saying which way it was not a message."""
    if isinstance(content, str) or not isinstance(content, (tuple, list)):
        raise MessageContentNotPieces(
            "a message is a run of pieces; use text_message_content(...) to send words"
        )
    if not all(isinstance(piece, (MessageText, MessageImage, MessageFile)) for piece in content):
        raise MessageContentNotPieces("a message may only hold message pieces")
    if not content:
        raise MessageContentEmpty("a message must have something in it")
    return tuple(content)


def message_content_text(content: MessageContent) -> str:
    """Everything the message says in words, for the places that can only hold words.

    The pieces that are not words are left out rather than described, because a stand-in
    sentence would read as something the sender wrote. A caller that needs to know a
    picture was there asks the content, not this.
    """
    return "\n\n".join(piece.text for piece in content if isinstance(piece, MessageText))


def joined_runs_of_text(content: MessageContent) -> MessageContent:
    """The same message with each run of words joined into one piece.

    Backends stream a sentence in fragments, and a message is not fifty pieces of one word
    each. Only pieces that were next to each other are joined, so words either side of a
    picture stay either side of it.
    """
    joined: list[MessagePiece] = []
    for piece in content:
        last = joined[-1] if joined else None
        if isinstance(piece, MessageText) and isinstance(last, MessageText):
            joined[-1] = MessageText(text=f"{last.text}{piece.text}")
            continue
        joined.append(piece)
    return tuple(joined)


def prefix_message_content_text(
    content: MessageContent, prefix: str, separator: str
) -> MessageContent:
    """Put text in front of the message, the way the message begins.

    A message that opens with words takes the prefix onto those words, which is exactly
    what joining two strings used to do and leaves an ordinary message byte for byte what
    it was. A message that opens with a picture takes the prefix as a piece of its own,
    because there is no run of words at the front to join it to.
    """
    first = content[0] if content else None
    if isinstance(first, MessageText):
        return (MessageText(text=f"{prefix}{separator}{first.text}"), *content[1:])
    return (MessageText(text=prefix), *content)


# --- the one JSON form ----------------------------------------------------------------------

# What a stored message looks like when it is only written words, and what it has always
# looked like. Kept for the common case on purpose: an ordinary message's row is the same
# text it was before this module existed, so nothing already recorded needs rewriting and
# nothing ordinary grows.
STORED_TEXT_ONLY_FIELD = "text"

# And the general case, for a message that is more than one run of words.
STORED_CONTENT_FIELD = "content"


def message_content_json_entries(content: MessageContent) -> dict[str, Any]:
    """The entries a message contributes to its row, in the one form it is stored as.

    One rule, and it decides on the value rather than on the caller: a message that is
    exactly one piece of written words is stored under ``text``, and everything else is
    stored under ``content``. So one value still has one canonical text, and the row of an
    ordinary message is unchanged.
    """
    if len(content) == 1 and isinstance(content[0], MessageText):
        return {STORED_TEXT_ONLY_FIELD: content[0].text}
    return {STORED_CONTENT_FIELD: [_piece_json_object(piece) for piece in content]}


def message_content_from_stored(stored: dict[str, Any]) -> MessageContent:
    """The message a stored row holds, in either of the two forms a row can be in.

    ``content`` is read when it is there and ``text`` when it is not, which is what makes
    every row written before this module reads exactly as it always did — a row carrying
    only ``text`` is one piece of written words, because that is what it always was.
    """
    pieces = stored.get(STORED_CONTENT_FIELD)
    if pieces is None:
        return (MessageText(text=_text(stored, STORED_TEXT_ONLY_FIELD)),)
    if not isinstance(pieces, list) or not pieces:
        raise ValueError("content must be a non-empty list of pieces")
    return tuple(_piece_from_json_object(piece) for piece in pieces)


# The name a piece says what it is under. It is not ``kind``: a row already has a kind, and
# a reader holding a row should never have to work out which of the two a ``kind`` meant.
PIECE_FIELD = "piece"

PIECE_NAME_TEXT = "text"
PIECE_NAME_IMAGE = "image"
PIECE_NAME_FILE = "file"


def _piece_json_object(piece: MessagePiece) -> dict[str, Any]:
    match piece:
        case MessageText():
            return {PIECE_FIELD: PIECE_NAME_TEXT, "text": piece.text}
        case MessageImage():
            return {
                PIECE_FIELD: PIECE_NAME_IMAGE,
                "stored_file_id": piece.stored_file_id,
                "media_type": piece.media_type,
                **({} if piece.file_name is None else {"file_name": piece.file_name}),
            }
        case MessageFile():
            return {
                PIECE_FIELD: PIECE_NAME_FILE,
                "stored_file_id": piece.stored_file_id,
                "media_type": piece.media_type,
                "file_name": piece.file_name,
                "byte_count": piece.byte_count,
            }
        case _:  # pragma: no cover - the piece type is closed
            assert_never(piece)


def _piece_from_json_object(stored: object) -> MessagePiece:
    if not isinstance(stored, dict):
        raise ValueError("a message piece is not a JSON object")
    match _text(stored, PIECE_FIELD):
        case "text":
            return MessageText(text=_text(stored, "text"))
        case "image":
            return MessageImage(
                stored_file_id=_text(stored, "stored_file_id"),
                media_type=_text(stored, "media_type"),
                file_name=_optional_text(stored, "file_name"),
            )
        case "file":
            return MessageFile(
                stored_file_id=_text(stored, "stored_file_id"),
                media_type=_text(stored, "media_type"),
                file_name=_text(stored, "file_name"),
                byte_count=_non_negative_integer(stored, "byte_count"),
            )
        case unknown:
            raise ValueError(f"unknown message piece {unknown}")


def _text(stored: dict[str, Any], field_name: str) -> str:
    value = stored[field_name]
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_text(stored: dict[str, Any], field_name: str) -> str | None:
    value = stored.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text or absent")
    return value


def _non_negative_integer(stored: dict[str, Any], field_name: str) -> int:
    value = stored[field_name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value
