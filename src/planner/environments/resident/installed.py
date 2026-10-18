"""Read-only artifact identity checks; no installed Python code is executed."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath


def installed_identity(runtime: Path) -> str:
    """Validate immutable installed wheel and RECORD bytes, returning receipt SHA256.

    Keep this function self-contained: remote preflight transports its source and
    uses only the standard library, never imports the installed application.
    """

    def regular(path: Path) -> bytes:
        if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError("installed artifact must be a regular file without symlink parents")
        return path.read_bytes()

    receipt = regular(runtime / "resident-installed.json")
    value = json.loads(receipt)
    if (
        not isinstance(value, dict)
        or value.get("distribution") != "planner"
        or value.get("entrypoint_module") != "planner.executor.entrypoint"
        or not isinstance(value.get("version"), str)
    ):
        raise ValueError("installed distribution identity is invalid")
    wheel_name, wheel_hash = value.get("wheel_filename"), value.get("wheel_sha256")
    if (
        not isinstance(wheel_name, str)
        or PurePosixPath(wheel_name).name != wheel_name
        or not wheel_name.endswith(".whl")
        or not isinstance(wheel_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", wheel_hash) is None
        or hashlib.sha256(regular(runtime / "wheels" / wheel_name)).hexdigest() != wheel_hash
    ):
        raise ValueError("installed wheel artifact identity changed")
    files = value.get("files")
    if not isinstance(files, dict) or not files or "bin/panels" not in files:
        raise ValueError("installed distribution files are absent")
    for name, digest in files.items():
        if (
            not isinstance(name, str)
            or not name
            or PurePosixPath(name).is_absolute()
            or str(PurePosixPath(name)) != name
            or any(part in {".", ".."} for part in PurePosixPath(name).parts)
            or "\\" in name
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or hashlib.sha256(regular(runtime / name)).hexdigest() != digest
        ):
            raise ValueError("installed artifact file identity changed")
    # A second import candidate, executable .pth, or unrecorded module could
    # bypass checked RECORD members. Refuse additions throughout site-packages.
    for site in (runtime / "lib").glob("python*/site-packages"):
        for path in site.rglob("*"):
            if path.is_symlink():
                raise ValueError("installed site-packages contains a symlink")
            if path.is_file():
                if path.relative_to(runtime).as_posix() not in files:
                    raise ValueError("installed site-packages contains an unrecorded file")
    return hashlib.sha256(receipt).hexdigest()
