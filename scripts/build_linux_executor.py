"""Build the Linux executor with the Linux home's native archive toolchain."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from planner.environments.app import (  # noqa: E402
    build_exported_app,
    digest_app_artifact,
    validate_app_manifest,
)
from planner.environments.linux_executor_runtime import extract_runtime  # noqa: E402
from planner.environments.runtime_admission import admit_runtime  # noqa: E402
from planner.environments.update_artifacts import create_app_archive  # noqa: E402
from planner.executor.update_configuration import MAC_RUNTIME_MANIFEST_NAME  # noqa: E402
from planner.platform_runtime.targets import LINUX_EXECUTOR  # noqa: E402


def build(source: Path, revision: str, output: Path) -> dict[str, object]:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Linux executor builds require Linux x86_64")
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    manifest = build_exported_app(
        source,
        requested_sha=revision,
        candidate_app=runtime,
        install_dependencies=True,
    )
    (runtime / MAC_RUNTIME_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "application_revision": revision,
                "platform": "linux",
                "architecture": "x86_64",
                "role": "executor",
            },
            sort_keys=True,
        )
        + "\n"
    )
    manifest = replace(manifest, artifact_digest=digest_app_artifact(runtime))
    (runtime / "manifest.json").write_text(json.dumps(manifest.as_dict(), sort_keys=True) + "\n")
    archive = output / "panels-linux-executor.tar.gz"
    digest = create_app_archive(runtime, archive, expected_target=LINUX_EXECUTOR)
    with tempfile.TemporaryDirectory(prefix="linux-executor-proof-", dir=output) as temporary:
        proved = extract_runtime(
            archive,
            Path(temporary),
            expected_sha256=digest,
            expected_size=archive.stat().st_size,
            expected_revision=revision,
        )
        original = output / "inaccessible-build"
        runtime.rename(original)
        try:
            admitted = validate_app_manifest(
                proved / "manifest.json",
                require_runtime=True,
                expected_target=LINUX_EXECUTOR,
            )
            admit_runtime(proved, admitted)
        finally:
            original.rename(runtime)
    receipt: dict[str, object] = {
        "schema_version": 1,
        "proof": {"native_startup": "passed", "relocated_archive": "passed"},
        "application_revision": revision,
        "archive_sha256": digest,
        "archive_size": archive.stat().st_size,
        "platform": "linux",
        "architecture": "x86_64",
        "role": "executor",
    }
    (output / "linux-executor-publication.json").write_text(
        json.dumps(receipt, sort_keys=True) + "\n"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.revision, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
