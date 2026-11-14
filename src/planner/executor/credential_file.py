"""The executor's private machine credential, outside versioned runtimes and journals."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{43}")


def credential_path(root: Path) -> Path:
    return root / "machine-credential.json"


def read_credential(path: Path) -> str | None:
    try:
        with path.open() as stream:
            text = stream.read(4097)
    except FileNotFoundError:
        return None
    if len(text) > 4096:
        raise ValueError("The machine credential file is invalid")
    value = json.loads(text)
    credential = value.get("credential") if isinstance(value, dict) else None
    if not isinstance(credential, str) or _CREDENTIAL.fullmatch(credential) is None:
        raise ValueError("The machine credential file is invalid")
    return credential


def write_credential(path: Path, credential: str) -> None:
    if _CREDENTIAL.fullmatch(credential) is None:
        raise ValueError("The machine credential is invalid")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"credential": credential}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
