"""A real PNG of one flat colour, for asking a real model what it can see.

A colour is something a model can only answer from having looked, so the picture is the
question. It is built here rather than checked in because a test fixture that is a binary
blob says nothing about what it is.

Both real-provider exercises ask the same question of their own backend, so the picture
they ask it about is written once.
"""

from __future__ import annotations

import struct
import zlib


def solid_png(red: int, green: int, blue: int) -> bytes:
    """An 8x8 PNG filled with one colour."""
    width = height = 8
    raw = b"".join(b"\x00" + bytes([red, green, blue]) * width for _ in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
