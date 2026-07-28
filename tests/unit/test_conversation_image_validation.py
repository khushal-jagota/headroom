"""The byte formats admitted at the conversation-message boundary."""

from __future__ import annotations

import base64
import struct
import zlib

import pytest

from planner.conversation.image_validation import (
    MAX_CONVERSATION_IMAGE_DIMENSION,
    MAX_CONVERSATION_IMAGE_PIXELS,
    sniff_image_format,
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
    ("payload", "media_type"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF, "image/gif"),
        (WEBP, "image/webp"),
    ],
)
def test_the_four_established_image_formats_have_canonical_media_types(
    payload: bytes, media_type: str
) -> None:
    assert validated_image_media_type(payload) == media_type


def _png(width: int, height: int, decompressed: bytes, *, interlace: int = 0) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + kind
            + data
            + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, interlace)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(decompressed))
        + chunk(b"IEND", b"")
    )


def test_png_inflation_is_bounded_and_must_match_the_ihdr_scanlines() -> None:
    assert sniff_image_format(_png(1, 1, b"\x00" * 1_000_000)) is None
    assert sniff_image_format(_png(1, 1, b"\x00" * 4)) is None
    assert sniff_image_format(_png(1, 1, b"\x05" + b"\x00" * 4)) is None
    assert sniff_image_format(_png(1, 1, b"\x00" * 5)) == "png"


def _adam7_rgba8(width: int, height: int) -> tuple[bytes, list[int]]:
    raw = bytearray()
    filter_offsets: list[int] = []
    for start_x, start_y, step_x, step_y in (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    ):
        pass_width = 0 if width <= start_x else (width - start_x + step_x - 1) // step_x
        pass_height = 0 if height <= start_y else (height - start_y + step_y - 1) // step_y
        for _ in range(pass_height):
            filter_offsets.append(len(raw))
            raw.extend(b"\x00" * (1 + pass_width * 4))
    return bytes(raw), filter_offsets


def test_a_normal_adam7_png_has_all_seven_bounded_scanline_runs() -> None:
    raw, filter_offsets = _adam7_rgba8(8, 8)
    assert len(filter_offsets) > 7
    assert sniff_image_format(_png(8, 8, raw, interlace=1)) == "png"
    assert sniff_image_format(_png(8, 8, raw[:-1], interlace=1)) is None
    bad_filter = bytearray(raw)
    bad_filter[filter_offsets[3]] = 5
    assert sniff_image_format(_png(8, 8, bytes(bad_filter), interlace=1)) is None


def _jpeg(width: int, height: int) -> bytes:
    return (
        b"\xff\xd8"
        + b"\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00"
        + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
        + b"\x01\xff\xd9"
    )


def _gif(width: int, height: int) -> bytes:
    return (
        b"GIF89a"
        + struct.pack("<HH", width, height)
        + b"\x00\x00\x00"
        + b"\x2c\x00\x00\x00\x00"
        + struct.pack("<HH", width, height)
        + b"\x00\x02\x01\x01\x00\x3b"
    )


def _webp(width: int, height: int) -> bytes:
    frame = (
        b"\x00\x00\x00\x9d\x01\x2a"
        + width.to_bytes(2, "little")
        + height.to_bytes(2, "little")
    )
    body = b"WEBPVP8 " + len(frame).to_bytes(4, "little") + frame
    return b"RIFF" + len(body).to_bytes(4, "little") + body


@pytest.mark.parametrize(
    "payload",
    [
        _png(MAX_CONVERSATION_IMAGE_DIMENSION + 1, 1, b""),
        _jpeg(MAX_CONVERSATION_IMAGE_DIMENSION + 1, 1),
        _gif(MAX_CONVERSATION_IMAGE_DIMENSION + 1, 1),
        _webp(MAX_CONVERSATION_IMAGE_DIMENSION + 1, 1),
        _png(5_001, 5_000, b""),
    ],
)
def test_every_format_rejects_backend_incompatible_dimensions(payload: bytes) -> None:
    assert 5_001 * 5_000 > MAX_CONVERSATION_IMAGE_PIXELS
    assert sniff_image_format(payload) is None
