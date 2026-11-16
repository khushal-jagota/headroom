"""The one place a machine's join command is written.

The command carries the machine id and revision but never the credential: the new
machine asks for the credential at a private prompt.
"""

from __future__ import annotations

import re
import shlex
from uuid import UUID


def require_released_revision(revision: object) -> str:
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Install a released home before you issue a join command")
    return revision


def join_command(*, home_url: str, revision: str, machine_id: str, credential: str) -> str:
    require_released_revision(revision)
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", credential) is None:
        raise ValueError("The home returned an invalid machine credential")
    arguments = [
        "join", "--home", home_url, "--machine-id", str(UUID(machine_id)), "--revision", revision,
    ]
    route = f"repos/khushal-jagota/headroom/contents/scripts/panels_install.py?ref={revision}"
    return (
        "panels_join_script=$(mktemp) && gh api "
        "-H 'Accept: application/vnd.github.raw+json' "
        + shlex.quote(route)
        + ' > "$panels_join_script" && python3 "$panels_join_script" '
        + shlex.join(arguments)
    )
