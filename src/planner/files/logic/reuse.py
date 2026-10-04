"""Whether a browser may reuse the copy of a managed file it already holds.

Framework-free. The route hands in what the request asked with and the file on disk, and
gets back a representation, a validator, and a yes or no.

**The validator is the bytes that get sent, not the file they came from.** Starlette
builds one from modification time and size. That cannot tell two same-size writes apart:
this machine's filesystem moves a modification time in 4 ms ticks, and a managed artifact
is written by a worker rather than a person. Measured, the stat-based validator gave the
same answer for 198 of 200 same-size rewrites.

Hashing the file and then letting the framework send it separately fixes that and
introduces a worse one: the two are different reads, so an artifact rewritten between
them is sent under a tag describing bytes nobody was given. A browser that later sees the
artifact written back to its earlier bytes is told its copy is current when it is not.
So the representation is read once, and the tag is taken from that same read. The bytes
the tag describes are the bytes that travel, always.

**Bounded per answer, and the aggregate is worth knowing.** The read stops one byte past
the bound, so one answer never costs more than the bound whatever the file's size claims,
and a larger artifact is simply not eligible — the route falls back to streaming it, where
the framework's own validator and range handling apply. Several answers at once each cost
their own, up to the thread limiter's width. The bound is set with that in mind rather
than to cover every file.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# 2 MiB. Measured against the live managed tree: of the 3,611 files a preview reads whole
# — Markdown, HTML, text and pictures — it covers all but 24. Doubling it would cover 17
# more of them and double both the memory an answer holds and the size of the single
# block the response compressor then has to work through. Media is excluded before this
# is ever reached, because media is played with byte ranges rather than read whole.
REUSE_MEMORY_BOUND_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Representation:
    """Some exact bytes, the entity tag those bytes decide, and when the file said it
    was last written.

    The date is carried so the answer keeps the header it has always had. Nothing
    decides anything from it — see ``holds_the_current_copy``.
    """

    body: bytes
    etag: str
    last_modified_epoch: float


def read_representation_within_bound(path: Path) -> Representation | None:
    """Read the file once, or refuse it for being larger than the bound.

    Never holds more than the bound plus one byte, whatever the file's size claims.
    Call this off the event loop: it reads from disk.
    """
    with path.open("rb") as handle:
        # Taken before the read, so the date can only describe the file as it was at or
        # before the bytes below. Nothing decides anything from it, but a date that
        # claims to be newer than what it is attached to is simply wrong.
        written_at = os.fstat(handle.fileno()).st_mtime
        body = handle.read(REUSE_MEMORY_BOUND_BYTES + 1)
    if len(body) > REUSE_MEMORY_BOUND_BYTES:
        return None
    return Representation(
        body=body,
        etag=f'"{hashlib.sha256(body).hexdigest()}"',
        last_modified_epoch=written_at,
    )


def is_played_with_ranges(media_type: str) -> bool:
    """Sound and video are seeked, so they keep the framework's range handling."""
    return media_type.startswith(("audio/", "video/"))


def holds_the_current_copy(if_none_match: str | None, etag: str) -> bool:
    """True when the copy the browser names is the one this file would send.

    Only the entity tag decides. A browser sends ``If-Modified-Since`` as well, and
    answering from it would let a stale copy be confirmed because a timestamp accurate
    only to the second still matched — the very thing a byte-derived tag is here to
    prevent. Every answer that carries a tag needs nothing else, and one that carries no
    tag is not eligible for reuse at all.
    """
    if if_none_match is None:
        return False
    named = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    return "*" in named or etag in named
