"""Whether a browser may reuse the copy of a managed file it already holds.

Framework-free. The route hands in what the request asked with and the file on disk, and
gets back a validator and a yes or no.

**What the validator is made of.** Starlette builds one from the file's modification time
and size. That cannot tell two same-size writes apart: this machine's filesystem moves a
modification time in 4 ms ticks, and a managed artifact is written by a worker rather
than a person. Measured, the stat-based validator gave the same answer for 198 of 200
same-size rewrites. So the validator is the file's own bytes.

**Only the entity tag decides.** A browser sends ``If-Modified-Since`` as well, and
answering from it would let a stale copy be confirmed because a timestamp that is only
accurate to the second still matched — the very thing the byte-derived tag is here to
prevent. Every answer from these routes carries an entity tag, so nothing needs the date
and the date is never consulted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_READ_CHUNK_BYTES = 1 << 20


def content_validator(path: Path) -> str:
    """An entity tag the file's own bytes decide.

    Read in chunks, so a large artifact costs one chunk of memory rather than its own
    size. Call this off the event loop: it reads the whole file.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            digest.update(chunk)
    return f'"{digest.hexdigest()}"'


def holds_the_current_copy(if_none_match: str | None, etag: str) -> bool:
    """True when the copy the browser names is the one this file would send."""
    if if_none_match is None:
        return False
    named = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    return "*" in named or etag in named
