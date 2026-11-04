"""File-type and size admission for conversation pictures."""

from __future__ import annotations

# Three raw MiB becomes four MiB in base64, within the message and draft envelope.
MAX_CONVERSATION_MESSAGE_IMAGE_BYTES = 3 * 1024 * 1024
MAX_CONVERSATION_IMAGE_BYTES = MAX_CONVERSATION_MESSAGE_IMAGE_BYTES


def validated_image_media_type(payload: bytes) -> str:
    """Identify supported file signatures without decoding the image."""
    if len(payload) > MAX_CONVERSATION_IMAGE_BYTES:
        raise ValueError("a conversation image is too large")
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported or invalid conversation image")
