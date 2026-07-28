"""Authoritative validation for pictures arriving in conversation messages."""

from __future__ import annotations

import struct
import zlib
from typing import Protocol, cast

# Three raw MiB becomes four MiB in base64. That is a conservative common envelope for
# direct adapter payloads and leaves headroom in a typical five-MB sessionStorage quota
# for the outgoing message's JSON and text.
MAX_CONVERSATION_MESSAGE_IMAGE_BYTES = 3 * 1024 * 1024
MAX_CONVERSATION_IMAGE_BYTES = MAX_CONVERSATION_MESSAGE_IMAGE_BYTES
MAX_CONVERSATION_IMAGE_DIMENSION = 8_000
MAX_CONVERSATION_IMAGE_PIXELS = 25_000_000

_MEDIA_TYPES_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


class _PngDecompressor(Protocol):
    eof: bool
    unconsumed_tail: bytes
    unused_data: bytes

    def decompress(self, data: bytes, max_length: int = 0) -> bytes: ...


def validated_image_media_type(payload: bytes) -> str:
    """Return the type proved by ``payload``, never the type its sender claimed."""
    if len(payload) > MAX_CONVERSATION_IMAGE_BYTES:
        raise ValueError("a conversation image is too large")
    image_format = sniff_image_format(payload)
    if image_format is None:
        raise ValueError("unsupported or invalid conversation image")
    return _MEDIA_TYPES_BY_FORMAT[image_format]


def sniff_image_format(payload: bytes) -> str | None:
    """Recognise the four image formats conversation messages accept."""
    if _valid_png(payload):
        return "png"
    if _valid_jpeg(payload):
        return "jpeg"
    if _valid_gif(payload):
        return "gif"
    if _valid_webp(payload):
        return "webp"
    return None


def _valid_png(payload: bytes) -> bool:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    saw_header = False
    decompressor: _PngDecompressor | None = None
    expected_decompressed_bytes: int | None = None
    png_scanline_bytes: int | None = None
    decompressed_bytes = 0
    saw_image_data = False
    while offset + 12 <= len(payload):
        length = int.from_bytes(payload[offset : offset + 4], "big")
        kind = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        chunk_end = data_end + 4
        if chunk_end > len(payload):
            return False
        data = payload[data_start:data_end]
        expected_crc = int.from_bytes(payload[data_end:chunk_end], "big")
        if zlib.crc32(kind + data) & 0xFFFFFFFF != expected_crc:
            return False
        if not saw_header:
            if kind != b"IHDR" or length != 13:
                return False
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            if (
                not _valid_dimensions(width, height)
                or bit_depth not in _PNG_BIT_DEPTHS_BY_COLOR_TYPE.get(color_type, ())
                or compression != 0
                or filtering != 0
                # Adam7 has seven differently-sized scanline runs. Rejecting it keeps the
                # exact decompressed-size proof simple and intentional.
                or interlace != 0
            ):
                return False
            channels = _PNG_CHANNELS_BY_COLOR_TYPE[color_type]
            scanline_bytes = (width * channels * bit_depth + 7) // 8
            png_scanline_bytes = 1 + scanline_bytes
            expected_decompressed_bytes = height * (1 + scanline_bytes)
            decompressor = cast(_PngDecompressor, zlib.decompressobj())
            saw_header = True
        elif kind == b"IHDR":
            return False
        if kind == b"IDAT":
            if (
                decompressor is None
                or expected_decompressed_bytes is None
                or png_scanline_bytes is None
            ):
                return False
            decompressed_bytes = _consume_png_image_data(
                decompressor,
                data,
                decompressed_bytes=decompressed_bytes,
                expected_decompressed_bytes=expected_decompressed_bytes,
                scanline_bytes=png_scanline_bytes,
            )
            if decompressed_bytes < 0:
                return False
            saw_image_data = True
        if kind == b"IEND":
            if (
                length != 0
                or chunk_end != len(payload)
                or not saw_image_data
                or decompressor is None
                or expected_decompressed_bytes is None
            ):
                return False
            return (
                decompressed_bytes == expected_decompressed_bytes
                and decompressor.eof
                and not decompressor.unused_data
                and not decompressor.unconsumed_tail
            )
        offset = chunk_end
    return False


def _consume_png_image_data(
    decompressor: _PngDecompressor,
    compressed: bytes,
    *,
    decompressed_bytes: int,
    expected_decompressed_bytes: int,
    scanline_bytes: int,
) -> int:
    """Inflate one IDAT chunk in bounded pieces and validate every scanline filter."""
    pending = compressed
    while pending:
        remaining = expected_decompressed_bytes - decompressed_bytes
        output_limit = min(64 * 1024, remaining + 1)
        try:
            inflated = decompressor.decompress(pending, output_limit)
        except zlib.error:
            return -1
        for index, value in enumerate(inflated):
            if (decompressed_bytes + index) % scanline_bytes == 0 and value > 4:
                return -1
        decompressed_bytes += len(inflated)
        if decompressed_bytes > expected_decompressed_bytes or decompressor.unused_data:
            return -1
        unconsumed = decompressor.unconsumed_tail
        if not unconsumed:
            break
        if not inflated:
            return -1
        pending = unconsumed
    return decompressed_bytes


def _valid_jpeg(payload: bytes) -> bool:
    if (
        len(payload) < 12
        or not payload.startswith(b"\xff\xd8")
        or not payload.endswith(b"\xff\xd9")
    ):
        return False
    start_of_frame_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    offset = 2
    saw_frame = False
    while offset < len(payload) - 2:
        if payload[offset] != 0xFF:
            return False
        while offset < len(payload) - 2 and payload[offset] == 0xFF:
            offset += 1
        marker = payload[offset]
        offset += 1
        if marker in (0x00, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            return False
        if offset + 2 > len(payload) - 2:
            return False
        segment_length = int.from_bytes(payload[offset : offset + 2], "big")
        if segment_length < 2:
            return False
        segment_end = offset + segment_length
        if segment_end > len(payload) - 2:
            return False
        data = payload[offset + 2 : segment_end]
        if marker in start_of_frame_markers:
            if len(data) < 6:
                return False
            height = int.from_bytes(data[1:3], "big")
            width = int.from_bytes(data[3:5], "big")
            component_count = data[5]
            if (
                not _valid_dimensions(width, height)
                or component_count <= 0
                or len(data) < 6 + 3 * component_count
            ):
                return False
            saw_frame = True
        if marker == 0xDA:
            if not saw_frame or len(data) < 4:
                return False
            component_count = data[0]
            if component_count <= 0 or len(data) != 4 + 2 * component_count:
                return False
            return bool(payload[segment_end:-2])
        offset = segment_end
    return False


def _valid_gif(payload: bytes) -> bool:
    if len(payload) < 14 or payload[:6] not in (b"GIF87a", b"GIF89a"):
        return False
    width, height = struct.unpack_from("<HH", payload, 6)
    if not _valid_dimensions(width, height):
        return False
    packed = payload[10]
    offset = 13 + (3 * (2 ** ((packed & 0x07) + 1)) if packed & 0x80 else 0)
    saw_image = False
    while offset < len(payload):
        block_type = payload[offset]
        if block_type == 0x3B:
            return saw_image and offset + 1 == len(payload)
        if block_type == 0x21:
            offset += 2
            if offset > len(payload):
                return False
        elif block_type == 0x2C:
            if offset + 10 > len(payload):
                return False
            image_width, image_height = struct.unpack_from("<HH", payload, offset + 5)
            if not _valid_dimensions(image_width, image_height):
                return False
            image_packed = payload[offset + 9]
            offset += 10
            if image_packed & 0x80:
                offset += 3 * (2 ** ((image_packed & 0x07) + 1))
            if offset >= len(payload) or not 2 <= payload[offset] <= 8:
                return False
            offset += 1
            saw_image = True
        else:
            return False
        saw_sub_block_data = False
        while offset < len(payload):
            block_length = payload[offset]
            offset += 1
            if block_length == 0:
                break
            saw_sub_block_data = True
            offset += block_length
            if offset > len(payload):
                return False
        else:
            return False
        if block_type == 0x2C and not saw_sub_block_data:
            return False
    return False


def _valid_webp(payload: bytes) -> bool:
    if len(payload) < 20 or payload[:4] != b"RIFF" or payload[8:12] != b"WEBP":
        return False
    if int.from_bytes(payload[4:8], "little") + 8 != len(payload):
        return False
    offset = 12
    saw_image = False
    allows_animation = False
    while offset + 8 <= len(payload):
        kind = payload[offset : offset + 4]
        length = int.from_bytes(payload[offset + 4 : offset + 8], "little")
        data_start = offset + 8
        data_end = data_start + length
        chunk_end = data_end + (length % 2)
        if chunk_end > len(payload):
            return False
        data = payload[data_start:data_end]
        if kind in (b"VP8 ", b"VP8L"):
            if not _valid_webp_frame(kind, data):
                return False
            saw_image = True
        elif kind == b"VP8X":
            if len(data) != 10 or data[1:4] != b"\x00\x00\x00":
                return False
            width = int.from_bytes(data[4:7], "little") + 1
            height = int.from_bytes(data[7:10], "little") + 1
            if not _valid_dimensions(width, height):
                return False
            allows_animation = bool(data[0] & 0x02)
        elif kind == b"ANIM":
            if not allows_animation or len(data) != 6:
                return False
        elif kind == b"ANMF":
            if not allows_animation or len(data) < 24:
                return False
            frame_width = int.from_bytes(data[6:9], "little") + 1
            frame_height = int.from_bytes(data[9:12], "little") + 1
            if not _valid_dimensions(frame_width, frame_height):
                return False
            frame_offset = 16
            frame_has_image = False
            while frame_offset + 8 <= len(data):
                frame_kind = data[frame_offset : frame_offset + 4]
                frame_length = int.from_bytes(data[frame_offset + 4 : frame_offset + 8], "little")
                frame_data_start = frame_offset + 8
                frame_data_end = frame_data_start + frame_length
                frame_chunk_end = frame_data_end + (frame_length % 2)
                if frame_chunk_end > len(data):
                    return False
                if frame_kind in (b"VP8 ", b"VP8L"):
                    if not _valid_webp_frame(frame_kind, data[frame_data_start:frame_data_end]):
                        return False
                    frame_has_image = True
                elif frame_kind != b"ALPH":
                    return False
                frame_offset = frame_chunk_end
            if not frame_has_image or frame_offset != len(data):
                return False
            saw_image = True
        offset = chunk_end
    return saw_image and offset == len(payload)


def _valid_webp_frame(kind: bytes, data: bytes) -> bool:
    if kind == b"VP8 ":
        if len(data) < 10 or data[0] & 1 or data[3:6] != b"\x9d\x01\x2a":
            return False
        width = int.from_bytes(data[6:8], "little") & 0x3FFF
        height = int.from_bytes(data[8:10], "little") & 0x3FFF
        return _valid_dimensions(width, height)
    if kind == b"VP8L":
        if len(data) < 5 or data[0] != 0x2F:
            return False
        dimensions = int.from_bytes(data[1:5], "little")
        width = (dimensions & 0x3FFF) + 1
        height = ((dimensions >> 14) & 0x3FFF) + 1
        return _valid_dimensions(width, height)
    return False


_PNG_BIT_DEPTHS_BY_COLOR_TYPE = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}
_PNG_CHANNELS_BY_COLOR_TYPE = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _valid_dimensions(width: int, height: int) -> bool:
    return (
        0 < width <= MAX_CONVERSATION_IMAGE_DIMENSION
        and 0 < height <= MAX_CONVERSATION_IMAGE_DIMENSION
        and width * height <= MAX_CONVERSATION_IMAGE_PIXELS
    )
