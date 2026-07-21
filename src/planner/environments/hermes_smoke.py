"""Opt-in real-Hermes smoke checks for prepared non-production environments."""

from __future__ import annotations

import concurrent.futures
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError
from planner.environments.logic.credentials import parse_environment_file
from planner.minds.config import hermes_src_root

_STORED_SESSION_RE = re.compile(r"\bstored(?:_session_id)?=(?P<id>[^\s]+)")
_AMBIENT_ALLOWLIST = {"LANG", "PATH", "TERM", "TMPDIR"}


@dataclass(frozen=True)
class HermesSmokeInvocation:
    label: str
    home: Path
    argv: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class HermesSmokeReport:
    staging_stored_session_id: str
    preview_stored_session_id: str
    staging_home: Path
    preview_home: Path


HermesSmokeRunner = Callable[[HermesSmokeInvocation], str]


def smoke_prepared_nonproduction_instances(
    staging: EnvironmentManifest,
    preview: EnvironmentManifest,
    *,
    hermes_python: Path,
    ambient_env: Mapping[str, str] | None = None,
    runner: HermesSmokeRunner | None = None,
    prompt: str = "Reply with exactly: ok",
) -> HermesSmokeReport:
    """Create one temporary real session in staging and one in preview, concurrently."""
    _validate_smoke_manifest(staging, expected_kind="staging")
    _validate_smoke_manifest(preview, expected_kind="preview")
    if staging.hermes_home == preview.hermes_home:
        raise EnvironmentValidationError("Hermes smoke requires distinct Hermes homes")

    source_env = ambient_env if ambient_env is not None else os.environ
    invocations = (
        _invocation_for(
            "staging",
            staging,
            hermes_python=hermes_python,
            ambient_env=source_env,
            prompt=prompt,
        ),
        _invocation_for(
            "preview",
            preview,
            hermes_python=hermes_python,
            ambient_env=source_env,
            prompt=prompt,
        ),
    )
    effective_runner = runner if runner is not None else _subprocess_smoke_runner
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(effective_runner, invocation): invocation
            for invocation in invocations
        }
        outputs = {
            futures[future].label: future.result()
            for future in concurrent.futures.as_completed(futures)
        }

    staging_stored = _parse_stored_session_id(outputs["staging"], label="staging")
    preview_stored = _parse_stored_session_id(outputs["preview"], label="preview")
    if staging_stored == preview_stored:
        raise EnvironmentValidationError(
            "Hermes smoke requires distinct stored session ids"
        )
    return HermesSmokeReport(
        staging_stored_session_id=staging_stored,
        preview_stored_session_id=preview_stored,
        staging_home=staging.hermes_home,
        preview_home=preview.hermes_home,
    )


def _validate_smoke_manifest(
    manifest: EnvironmentManifest,
    *,
    expected_kind: str,
) -> None:
    if manifest.kind != expected_kind:
        raise EnvironmentValidationError(
            "Hermes smoke accepts prepared staging plus one preview non-production instance"
        )
    if manifest.prepared_at is None:
        raise EnvironmentValidationError("Hermes smoke requires prepared manifests")
    if manifest.expected_linux_account != "panels-worker":
        raise EnvironmentValidationError("Hermes smoke is non-production only")


def _invocation_for(
    label: str,
    manifest: EnvironmentManifest,
    *,
    hermes_python: Path,
    ambient_env: Mapping[str, str],
    prompt: str,
) -> HermesSmokeInvocation:
    env = _smoke_env(manifest, hermes_python=hermes_python, ambient_env=ambient_env)
    argv = [
        sys.executable,
        "-m",
        "planner.minds.smoke",
        "--home",
        str(manifest.hermes_home),
        "--hermes-python",
        str(hermes_python),
        "--prompt",
        prompt,
        "--resume",
    ]
    return HermesSmokeInvocation(
        label=label,
        home=manifest.hermes_home,
        argv=argv,
        env=env,
    )


def _smoke_env(
    manifest: EnvironmentManifest,
    *,
    hermes_python: Path,
    ambient_env: Mapping[str, str],
) -> dict[str, str]:
    env = {
        key: value
        for key, value in ambient_env.items()
        if key in _AMBIENT_ALLOWLIST or key.startswith("LC_")
    }
    if manifest.credentials_env_file is not None:
        env.update(parse_environment_file(manifest.credentials_env_file, kind=manifest.kind))
    env["HERMES_HOME"] = str(manifest.hermes_home)
    env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(hermes_python))
    return env


def _subprocess_smoke_runner(invocation: HermesSmokeInvocation) -> str:
    result = subprocess.run(
        invocation.argv,
        env=invocation.env,
        capture_output=True,
        text=True,
        timeout=240.0,
        check=False,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise EnvironmentValidationError(
            f"{invocation.label} Hermes smoke failed with exit {result.returncode}:\n{output}"
        )
    return output


def _parse_stored_session_id(output: str, *, label: str) -> str:
    match = _STORED_SESSION_RE.search(output)
    if match is None:
        raise EnvironmentValidationError(f"{label} Hermes smoke printed no stored session id")
    return match.group("id")
