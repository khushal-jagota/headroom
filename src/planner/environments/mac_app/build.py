"""Construct an unsigned candidate outside the installed app. Never sign or install."""

from __future__ import annotations

import os
import platform
import plistlib
import subprocess
import tempfile
from pathlib import Path

from planner.environments.mac_app.configuration import (
    ARCHITECTURE,
    BUNDLE_ID,
    DISPLAY_NAME,
    MINIMUM_MACOS,
    validate_identity,
)


def bundle_metadata(revision: str, bundle_id: str = BUNDLE_ID) -> dict[str, object]:
    validate_identity(bundle_id)
    if not revision or any(ord(char) < 32 for char in revision):
        raise ValueError("an explicit application revision is required")
    return {
        "CFBundleIdentifier": bundle_id,
        "CFBundleName": DISPLAY_NAME,
        "CFBundleDisplayName": DISPLAY_NAME,
        "CFBundleExecutable": DISPLAY_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "PanelsApplicationRevision": revision,
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "LSUIElement": True,
        "NSDesktopFolderUsageDescription": "Panels accesses your selected files on Desktop.",
        "NSDocumentsFolderUsageDescription": "Panels accesses your selected files in Documents.",
        "NSDownloadsFolderUsageDescription": "Panels accesses your selected files in Downloads.",
    }


def runtime_definition(python: Path) -> str:
    encoded = "".join(f"\\{byte:03o}" for byte in os.fsencode(python))
    return '#define PANELS_PYTHON "' + encoded + '"\n'


def build_app(
    home: Path,
    output: Path,
    revision: str,
    *,
    python: Path | None = None,
    installed_app: Path | None = None,
    bundle_id: str = BUNDLE_ID,
) -> None:
    if platform.system() != "Darwin" or platform.machine() != ARCHITECTURE:
        raise ValueError("build requires the authorized native arm64 Mac")
    if not home.is_absolute() or not output.is_absolute() or output.suffix != ".app":
        raise ValueError("home and candidate app must be absolute paths")
    installed = home / "Applications/Panels.app"
    python = python or home / "Deployments/PanelsExecutor/venv/bin/python"
    protected = (installed, installed_app or installed)
    if any(not path.is_absolute() or "\0" in str(path) for path in (*protected, python)):
        raise ValueError("installed app and Python paths must be absolute")
    if any(path.suffix != ".app" for path in protected):
        raise ValueError("installed app path must name an .app bundle")
    if any(
        output.resolve() == path.resolve() or path.resolve() in output.resolve().parents
        for path in protected
    ):
        raise ValueError("build outside the installed app")
    metadata = bundle_metadata(revision, bundle_id)
    if not python.is_file():
        raise ValueError("the selected native Python environment does not exist")
    # Exclusive creation prevents replacement of an existing candidate or installation.
    output.mkdir()
    contents = output / "Contents"
    executable = contents / "MacOS" / DISPLAY_NAME
    executable.parent.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
    with tempfile.TemporaryDirectory(prefix="panels-native-build-") as temporary:
        header = Path(temporary) / "runtime_path.h"
        header.write_text(runtime_definition(python))
        subprocess.run(
            [
                "/usr/bin/xcrun",
                "clang",
                "-arch",
                ARCHITECTURE,
                f"-mmacosx-version-min={MINIMUM_MACOS}",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                temporary,
                str(Path(__file__).with_name("launcher.c")),
                "-o",
                str(executable),
            ],
            check=True,
        )
    subprocess.run(["/usr/bin/lipo", str(executable), "-verify_arch", ARCHITECTURE], check=True)
