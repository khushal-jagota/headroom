"""The byte formats admitted at the conversation-message boundary."""

from __future__ import annotations

import base64

import pytest

from planner.conversation.image_validation import (
    MAX_CONVERSATION_IMAGE_BYTES,
    validated_image_media_type,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG = (
    b"\xff\xd8"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    b"\x01"
    b"\xff\xd9"
)
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
WEBP = base64.b64decode("UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEAAUAmJaQAA3AA/vuU")


@pytest.mark.parametrize(
    "payload, media_type",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
def test_supported_file_types(payload: bytes, media_type: str) -> None:
    assert validated_image_media_type(payload) == media_type


def test_file_type_and_size_admission() -> None:
    # Admission identifies the file. It does not decode or validate its contents.
    assert validated_image_media_type(PNG[:8] + b"not decoded") == "image/png"
    with pytest.raises(ValueError, match="unsupported"):
        validated_image_media_type(b"not an image")
    with pytest.raises(ValueError, match="too large"):
        validated_image_media_type(PNG + b"x" * MAX_CONVERSATION_IMAGE_BYTES)
