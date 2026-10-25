"""Pure validation of peer transfer paths within the negotiated generation root."""

import re
from pathlib import PurePosixPath


def validate_transfer_path(path: str, root: str) -> None:
    def safe(value: str) -> bool:
        return (
            value.startswith("/")
            and not value.startswith("//")
            and "\\" not in value
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and not re.search(r"%(25|2e|2f|5c)", value, re.I)
            and all(part not in ("", ".", "..") for part in value.split("/")[1:])
        )

    if not safe(root) or not safe(path) or PurePosixPath(path).parent != PurePosixPath(root):
        raise ValueError("unsafe generation transfer path")
