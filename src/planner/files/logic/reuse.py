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

**Bounded by construction.** The read stops one byte past the bound, so a large artifact
never costs more than the bound in memory and simply is not eligible — the route falls
back to streaming it, where the framework's own validator and range handling apply.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# 4 MiB. Measured against the live managed tree: it holds every kind of file a preview
# reads whole — Markdown, HTML, text and pictures — for all but 24 of its 5,432
# non-media files. Media is excluded before this is ever reached, because media is played
# with byte ranges rather than read whole.
REUSE_MEMORY_BOUND_BYTES = 4 * 1024 * 1024


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
        body = handle.read(REUSE_MEMORY_BOUND_BYTES + 1)
        written_at = os.fstat(handle.fileno()).st_mtime
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
