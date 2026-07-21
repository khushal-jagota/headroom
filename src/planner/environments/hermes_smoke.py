"""Opt-in real-Hermes smoke checks for prepared non-production environments."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import re
import subprocess
import sys
from argparse import ArgumentParser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from acp.schema import (
    AgentMessageChunk,
    DeniedOutcome,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
)

from planner.conversation.backend_contracts import (
    AcpConversationIngress,
    AcpEmployeeChild,
    AgentBackendDefinition,
)
from planner.conversation.contracts import ConversationEmployee
from planner.conversation.hermes_backend import build_hermes_acp_backend_definition
from planner.conversation.hermes_backend_configuration import hermes_src_root
from planner.conversation.hermes_turn_strategy import HermesAcpTurnStrategy
from planner.conversation.sdk_child import (
    SdkAcpEmployeeChildFactory,
    build_panels_initialize_request,
)
from planner.conversation.wire_contracts import ProtocolUpdateRejectedPayload
from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError
from planner.environments.logic.credentials import (
    parse_environment_file,
    validate_environment_key,
)

_STORED_SESSION_RE = re.compile(r"\bstored(?:_session_id)?=(?P<id>[^\s]+)")
_EXPECTED_RESPONSE_PREFIX = "Reply with exactly: "
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
    """Create one unrelated real session in each non-production home, concurrently."""
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
    credentials = (
        parse_environment_file(manifest.credentials_env_file, kind=manifest.kind)
        if manifest.credentials_env_file is not None
        else {}
    )
    credential_environment_names = tuple(credentials)
    env = _smoke_env(
        manifest,
        hermes_python=hermes_python,
        ambient_env=ambient_env,
        credentials=credentials,
    )
    argv = [
        sys.executable,
        "-m",
        "planner.environments.hermes_smoke",
        "--home",
        str(manifest.hermes_home),
        "--hermes-python",
        str(hermes_python),
        "--repository-root",
        str(manifest.repository_roots[0]),
        "--prompt",
        prompt,
    ]
    for name in credential_environment_names:
        argv.extend(("--credential-key", name))
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
    credentials: Mapping[str, str],
) -> dict[str, str]:
    env = {
        key: value
        for key, value in ambient_env.items()
        if key in _AMBIENT_ALLOWLIST or key.startswith("LC_")
    }
    env.update(credentials)
    env["HOME"] = str(manifest.hermes_home)
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


async def _create_official_acp_session(
    *,
    home: Path,
    hermes_python: Path,
    repository_root: Path,
    prompt: str,
    credential_environment_names: tuple[str, ...] = (),
) -> str:
    """Create and prompt one real Hermes session through the production ACP child."""

    for name in credential_environment_names:
        validate_environment_key(name, kind="staging")

    agent_text: list[str] = []
    definition = build_hermes_acp_backend_definition(
        hermes_executable=hermes_python.with_name("hermes"),
        hermes_home=home,
        hermes_source_root=hermes_src_root(hermes_python),
        turn_strategy=HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None),
        additional_inherited_environment_names=credential_environment_names,
    )

    async def ingress(
        update: SessionNotification | ProtocolUpdateRejectedPayload,
    ) -> None:
        if not isinstance(update, SessionNotification):
            return
        if not isinstance(update.update, AgentMessageChunk):
            return
        if not isinstance(update.update.content, TextContentBlock):
            return
        agent_text.append(update.update.content.text)

    child = await _create_official_acp_child(
        home=home,
        repository_root=repository_root,
        definition=definition,
        ingress=cast(AcpConversationIngress, ingress),
    )
    try:
        await child.initialize(build_panels_initialize_request(definition))
        session = await child.new_session(
            NewSessionRequest(cwd=str(repository_root), additional_directories=[], mcp_servers=[])
        )
        prompt_response = await child.prompt(
            PromptRequest(
                session_id=session.session_id,
                prompt=[TextContentBlock(type="text", text=prompt)],
            )
        )
        expected_response = _expected_smoke_response(prompt)
        actual_response = "".join(agent_text)
        if prompt_response.stop_reason != "end_turn" or actual_response != expected_response:
            raise EnvironmentValidationError(
                "Hermes ACP smoke prompt failed: "
                f"stop_reason={prompt_response.stop_reason!r}; "
                f"agent_text={actual_response!r}; "
                f"expected stop_reason='end_turn' and agent_text={expected_response!r}"
            )
    finally:
        await child.close()

    fresh_child = await _create_official_acp_child(
        home=home,
        repository_root=repository_root,
        definition=definition,
        ingress=cast(AcpConversationIngress, ingress),
    )
    try:
        await fresh_child.initialize(build_panels_initialize_request(definition))
        await fresh_child.load_session(
            LoadSessionRequest(
                cwd=str(repository_root),
                session_id=session.session_id,
                mcp_servers=[],
                additional_directories=[],
            )
        )
    finally:
        await fresh_child.close()
    return session.session_id


async def _create_official_acp_child(
    *,
    home: Path,
    repository_root: Path,
    definition: AgentBackendDefinition,
    ingress: AcpConversationIngress,
) -> AcpEmployeeChild:
    async def permission(
        _request: RequestPermissionRequest,
    ) -> RequestPermissionResponse:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def death(_error: BaseException | None) -> None:
        return None

    employee = ConversationEmployee(
        employee_id="environment-hermes-smoke",
        entity_kind="agent",
        entity_id=home.name,
        workspace_roots=(repository_root,),
        backend_key="hermes",
    )
    return await SdkAcpEmployeeChildFactory(definition).create(
        employee,
        1,
        ingress,
        permission,
        death,
    )


def _expected_smoke_response(prompt: str) -> str:
    if not prompt.startswith(_EXPECTED_RESPONSE_PREFIX):
        raise EnvironmentValidationError(
            "Hermes ACP smoke prompt must start with 'Reply with exactly: '"
        )
    return prompt[len(_EXPECTED_RESPONSE_PREFIX) :]


def _run_official_acp_session_from_cli(arguments: list[str] | None = None) -> int:
    parser = ArgumentParser(description="Create one opt-in Hermes ACP smoke session")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--hermes-python", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--credential-key", action="append", default=[])
    parsed = parser.parse_args(arguments)
    try:
        session_id = asyncio.run(
            _create_official_acp_session(
                home=parsed.home,
                hermes_python=parsed.hermes_python,
                repository_root=parsed.repository_root,
                prompt=parsed.prompt,
                credential_environment_names=tuple(parsed.credential_key),
            )
        )
    except BaseException as error:
        print(f"Hermes ACP smoke failed: {error}", file=sys.stderr)
        return 1
    print(f"stored={session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_official_acp_session_from_cli())
