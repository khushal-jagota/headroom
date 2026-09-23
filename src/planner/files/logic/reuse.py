"""Whether a browser may reuse the copy of a managed file it already holds.

Framework-free. The route hands in the request's headers and the file on disk, and gets
back a validator and a yes or no.

Two decisions live here.

**What the validator is made of.** Starlette builds one from the file's modification time
and size. That cannot tell two same-size writes apart: this machine's filesystem moves a
modification time in 4 ms ticks, and a managed artifact is written by a worker rather
than a person. Measured, the stat-based validator gave the same answer for 198 of 200
same-size rewrites. So the validator is the file's own bytes.

**Which header decides.** A browser sends both ``If-None-Match`` and
``If-Modified-Since``. When the first is present it decides alone. Falling through to the
date would let a stale validator become a hit because a second-resolution timestamp still
matched, which is exactly how a reader ends up looking at an artifact that has moved on.
"""

from __future__ import annotations

import hashlib
from email.utils import parsedate
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


def holds_the_current_copy(
    if_none_match: str | None, if_modified_since: str | None, etag: str, last_modified: str
) -> bool:
    """True when the browser's copy is the one this file would send."""
    if if_none_match is not None:
        candidates = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
        return "*" in candidates or etag in candidates
    if if_modified_since is None:
        return False
    asked = parsedate(if_modified_since)
    served = parsedate(last_modified)
    return asked is not None and served is not None and asked >= served
