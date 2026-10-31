"""Validate a separately signed local candidate. Never repair or sign it."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
from pathlib import Path

from planner.environments.mac_app.configuration import (
    ARCHITECTURE,
    BUNDLE_ID,
    DISPLAY_NAME,
    validate_identity,
)


def _codesign(*arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/codesign", *arguments], check=True, capture_output=True, text=True
    )
    return result.stdout + result.stderr


def verify_app(
    app: Path,
    identity: str,
    previous: Path | None = None,
    *,
    bundle_id: str = BUNDLE_ID,
    python: Path | None = None,
) -> str:
    validate_identity(bundle_id)
    if python is not None and (not python.is_absolute() or "\0" in str(python)):
        raise ValueError("Python path must be absolute")
    if re.fullmatch(r"[0-9A-Fa-f]{40}", identity) is None:
        raise ValueError("an explicit certificate SHA-1 fingerprint is required")
    metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (
        metadata.get("CFBundleIdentifier") != bundle_id
        or metadata.get("CFBundleExecutable") != DISPLAY_NAME
        or metadata.get("CFBundleDisplayName") != DISPLAY_NAME
    ):
        raise ValueError("candidate has a different Panels bundle identity")
    if python is not None:
        binary = (app / "Contents/MacOS" / DISPLAY_NAME).read_bytes()
        if b"\0" + os.fsencode(python) + b"\0" not in binary:
            raise ValueError("candidate does not embed the selected Python interpreter")
    subprocess.run(
        ["/usr/bin/lipo", str(app / "Contents/MacOS" / DISPLAY_NAME), "-verify_arch", ARCHITECTURE],
        check=True,
        capture_output=True,
    )
    _codesign("--verify", "--strict", "--verbose=2", str(app))
    _codesign("--verify", "--strict", "-R", '=certificate leaf = H"' + identity + '"', str(app))
    details = _codesign("--display", "--verbose=4", str(app))
    if not any(line.startswith("Authority=Apple Development:") for line in details.splitlines()):
        raise ValueError("candidate requires the reviewed Apple Development signing route")
    requirement = _codesign("--display", "-r-", str(app))
    if previous is not None:
        verify_app(previous, identity, bundle_id=bundle_id)
        old_requirement = _codesign("--display", "-r-", str(previous))
        for target, text in ((app, old_requirement), (previous, requirement)):
            expressions = [
                line.removeprefix("designated => ")
                for line in text.splitlines()
                if line.startswith("designated => ")
            ]
            if len(expressions) != 1:
                raise ValueError("missing designated requirement")
            _codesign("--verify", "--strict", "-R", "=" + expressions[0], str(target))
    if "designated => " not in requirement:
        raise ValueError("missing designated requirement")
    return details + requirement
