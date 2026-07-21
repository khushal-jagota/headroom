"""Click entry points for isolated Panels environments."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import click

from planner.conversation.hermes_backend_configuration import resolve_hermes_python
from planner.environments.contracts import (
    EnvironmentKind,
    EnvironmentManifest,
    EnvironmentValidationError,
    ResolvedEnvironmentInstance,
)
from planner.environments.hermes_smoke import (
    HermesSmokeReport,
    smoke_prepared_nonproduction_instances,
)
from planner.environments.linux import render_linux_specification
from planner.environments.logic.credentials import parse_environment_file
from planner.environments.logic.launch_env import (
    build_environment_run_env,
    build_test_environment_run_env,
)
from planner.environments.logic.registry import resolve_environment_instance
from planner.environments.logic.validation import validate_repository_roots
from planner.environments.materialize import (
    inspect_environment_instance,
    manifest_to_json,
    prepare_environment_instance,
    remove_environment_instance,
    reset_environment_instance,
)

ExecFn = Callable[[str, list[str], Mapping[str, str]], object]
ResolveInstanceFn = Callable[..., ResolvedEnvironmentInstance]
MaterializeFn = Callable[..., EnvironmentManifest]
InspectInstanceFn = Callable[..., EnvironmentManifest]
SmokeInstancesFn = Callable[..., HermesSmokeReport]


@dataclass(frozen=True)
class EnvironmentCliDependencies:
    resolve_instance: ResolveInstanceFn = resolve_environment_instance
    prepare_instance: MaterializeFn = prepare_environment_instance
    inspect_instance: InspectInstanceFn = inspect_environment_instance
    reset_instance: MaterializeFn = reset_environment_instance
    remove_instance: MaterializeFn = remove_environment_instance
    smoke_instances: SmokeInstancesFn = smoke_prepared_nonproduction_instances
    exec_fn: ExecFn = os.execvpe
    ambient_env: Mapping[str, str] | None = None
    executable: str = sys.executable


@click.group("environment")
def environment() -> None:
    """Prepare and run isolated Panels environments."""


@environment.command("prepare")
@click.option("--kind", type=click.Choice(["live", "staging", "preview"]), required=True)
@click.option("--instance-id")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option("--port", type=int)
@click.option("--credentials-env-file", type=click.Path(path_type=Path))
@click.option(
    "--repository-root",
    "repository_roots",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    multiple=True,
)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def prepare(
    ctx: click.Context,
    *,
    kind: EnvironmentKind,
    instance_id: str | None,
    environment_root: Path,
    port: int | None,
    credentials_env_file: str | None,
    repository_roots: tuple[Path, ...],
    json_output: bool,
) -> None:
    """Materialize one isolated environment instance."""
    deps = _dependencies_from_context(ctx)
    try:
        manifest = deps.prepare_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=environment_root,
            port=port,
            credentials_env_file=Path(credentials_env_file)
            if credentials_env_file is not None
            else None,
            repository_roots=repository_roots,
        )
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    _emit_manifest(manifest, json_output=json_output)


@environment.command("inspect")
@click.option("--kind", type=click.Choice(["live", "staging", "preview"]), required=True)
@click.option("--instance-id")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option("--port", type=int)
@click.option("--credentials-env-file", type=click.Path(path_type=Path))
@click.option(
    "--repository-root",
    "repository_roots",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    multiple=True,
)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def inspect(
    ctx: click.Context,
    *,
    kind: EnvironmentKind,
    instance_id: str | None,
    environment_root: Path,
    port: int | None,
    credentials_env_file: str | None,
    repository_roots: tuple[Path, ...],
    json_output: bool,
) -> None:
    """Inspect one environment instance without exposing credential values."""
    deps = _dependencies_from_context(ctx)
    try:
        manifest = deps.inspect_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=environment_root,
            port=port,
            credentials_env_file=Path(credentials_env_file)
            if credentials_env_file is not None
            else None,
            repository_roots=repository_roots,
        )
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    _emit_manifest(manifest, json_output=json_output)


@environment.command("reset")
@click.option("--kind", type=click.Choice(["staging", "preview"]), required=True)
@click.option("--instance-id")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option("--port", type=int)
@click.option("--credentials-env-file", type=click.Path(path_type=Path))
@click.option(
    "--repository-root",
    "repository_roots",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    multiple=True,
)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def reset(
    ctx: click.Context,
    *,
    kind: EnvironmentKind,
    instance_id: str | None,
    environment_root: Path,
    port: int | None,
    credentials_env_file: str | None,
    repository_roots: tuple[Path, ...],
    json_output: bool,
) -> None:
    """Rebuild staging or preview fake state while preserving instance identity."""
    deps = _dependencies_from_context(ctx)
    try:
        manifest = deps.reset_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=environment_root,
            port=port,
            credentials_env_file=Path(credentials_env_file)
            if credentials_env_file is not None
            else None,
            repository_roots=repository_roots,
        )
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    _emit_manifest(manifest, json_output=json_output)


@environment.command("remove")
@click.option("--kind", type=click.Choice(["staging", "preview"]), required=True)
@click.option("--instance-id")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option("--port", type=int)
@click.option("--credentials-env-file", type=click.Path(path_type=Path))
@click.option(
    "--repository-root",
    "repository_roots",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    multiple=True,
)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def remove(
    ctx: click.Context,
    *,
    kind: EnvironmentKind,
    instance_id: str | None,
    environment_root: Path,
    port: int | None,
    credentials_env_file: str | None,
    repository_roots: tuple[Path, ...],
    json_output: bool,
) -> None:
    """Remove one stopped staging or preview environment."""
    deps = _dependencies_from_context(ctx)
    try:
        manifest = deps.remove_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=environment_root,
            port=port,
            credentials_env_file=Path(credentials_env_file)
            if credentials_env_file is not None
            else None,
            repository_roots=repository_roots,
        )
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    _emit_manifest(manifest, json_output=json_output, running=False)


@environment.command("render-linux")
@click.option("--kind", type=click.Choice(["live", "staging", "preview"]), required=True)
@click.option("--instance-id")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def render_linux(
    ctx: click.Context,
    *,
    kind: EnvironmentKind,
    instance_id: str | None,
    environment_root: Path,
    json_output: bool,
) -> None:
    """Render Linux unit and ownership intent without installing it."""
    deps = _dependencies_from_context(ctx)
    try:
        manifest = deps.inspect_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=environment_root,
            port=None,
            credentials_env_file=None,
            repository_roots=(),
        )
        rendered = render_linux_specification(manifest)
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(
            json.dumps(
                {
                    "required_account": rendered.required_account,
                    "unit_name": rendered.unit_name,
                    "unit_text": rendered.unit_text,
                    "tmpfiles_text": rendered.tmpfiles_text,
                    "ownership_text": rendered.ownership_text,
                    "strict_writable_paths": [
                        str(path) for path in rendered.strict_writable_paths
                    ],
                    "repository_path_policy": rendered.repository_path_policy,
                    "credential_file_reference": (
                        str(rendered.credential_file_reference)
                        if rendered.credential_file_reference is not None
                        else None
                    ),
                    "local_effects": rendered.local_effects,
                    "vps_enforcement_verified": rendered.vps_enforcement_verified,
                },
                sort_keys=True,
            )
        )
        return

    click.echo(f"# Unit: {rendered.unit_name}")
    click.echo(f"# Required account: {rendered.required_account}")
    click.echo(f"# Credential file: {rendered.credential_file_reference or '(none)'}")
    click.echo(f"# Repository policy: {rendered.repository_path_policy}")
    click.echo(f"# Local effects: {rendered.local_effects}")
    click.echo("\n[unit]\n" + rendered.unit_text)
    click.echo("\n[tmpfiles]\n" + rendered.tmpfiles_text)
    click.echo("\n[ownership]\n" + rendered.ownership_text)


@environment.command("smoke-hermes")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option("--preview-id", required=True)
@click.option("--hermes-python", type=click.Path(path_type=Path), required=True)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def smoke_hermes(
    ctx: click.Context,
    *,
    environment_root: Path,
    preview_id: str,
    hermes_python: Path,
    json_output: bool,
) -> None:
    """Opt in to a real Hermes cross-home smoke for staging and one preview."""
    deps = _dependencies_from_context(ctx)
    try:
        staging = deps.inspect_instance(
            kind="staging",
            instance_id=None,
            environment_root=environment_root,
            port=None,
            credentials_env_file=None,
            repository_roots=(),
        )
        preview = deps.inspect_instance(
            kind="preview",
            instance_id=preview_id,
            environment_root=environment_root,
            port=None,
            credentials_env_file=None,
            repository_roots=(),
        )
        report = deps.smoke_instances(
            staging=staging,
            preview=preview,
            hermes_python=hermes_python,
            ambient_env=deps.ambient_env,
        )
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc

    payload = {
        "preview_home": str(report.preview_home),
        "preview_stored_session_id": report.preview_stored_session_id,
        "staging_home": str(report.staging_home),
        "staging_stored_session_id": report.staging_stored_session_id,
    }
    if json_output:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    click.echo(f"staging {payload['staging_home']} {payload['staging_stored_session_id']}")
    click.echo(f"preview {payload['preview_home']} {payload['preview_stored_session_id']}")


@environment.command("run")
@click.option("--kind", type=click.Choice(["live", "staging", "preview"]), required=True)
@click.option("--instance-id")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option("--port", type=int)
@click.option("--credentials-env-file", type=click.Path(path_type=Path))
@click.option(
    "--repository-root",
    "repository_roots",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    multiple=True,
)
@click.option("--test-mode", is_flag=True, hidden=True)
@click.pass_context
def run(
    ctx: click.Context,
    *,
    kind: EnvironmentKind,
    instance_id: str | None,
    environment_root: Path,
    port: int | None,
    credentials_env_file: str | None,
    repository_roots: tuple[Path, ...],
    test_mode: bool,
) -> None:
    """Replace this process with `python -m planner serve` for one prepared instance."""
    deps = _dependencies_from_context(ctx)
    try:
        launch_repository_root = _one_caller_trusted_repository_root(repository_roots)
        manifest = deps.inspect_instance(
            kind=kind,
            instance_id=instance_id,
            environment_root=environment_root,
            port=port,
            credentials_env_file=Path(credentials_env_file)
            if credentials_env_file is not None
            else None,
            repository_roots=(launch_repository_root,),
        )
        instance = _resolved_instance_from_manifest(manifest)
        run_environment_instance(
            instance,
            deps=deps,
            launch_repository_root=launch_repository_root,
            test_mode=test_mode,
        )
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc


def run_environment_instance(
    instance: ResolvedEnvironmentInstance,
    *,
    deps: EnvironmentCliDependencies,
    launch_repository_root: Path,
    test_mode: bool,
) -> None:
    if not instance.prepared:
        raise click.ClickException("environment instance is not prepared")

    credentials = (
        parse_environment_file(instance.credentials_env_file, kind=instance.kind)
        if instance.credentials_env_file is not None
        else {}
    )
    ambient = deps.ambient_env if deps.ambient_env is not None else os.environ
    hermes_python = resolve_hermes_python(env=ambient)
    if test_mode:
        run_env = build_test_environment_run_env(
            instance,
            credentials=credentials,
            ambient=ambient,
            hermes_python=hermes_python,
        )
    else:
        run_env = build_environment_run_env(
            instance,
            credentials=credentials,
            ambient=ambient,
            hermes_python=hermes_python,
        )
    argv = [deps.executable, "-m", "planner", "serve"]
    launch_root = _validated_launch_repository_root(
        launch_repository_root,
        allowed_repository_roots=instance.allowed_repository_roots,
    )
    previous_cwd = Path.cwd()
    os.chdir(launch_root)
    try:
        deps.exec_fn(deps.executable, argv, run_env)
    finally:
        if Path.cwd() == launch_root:
            os.chdir(previous_cwd)


def _one_caller_trusted_repository_root(repository_roots: tuple[Path, ...]) -> Path:
    if len(repository_roots) != 1:
        raise EnvironmentValidationError(
            "environment run requires exactly one repository root"
        )
    return repository_roots[0]


def _validated_launch_repository_root(
    launch_repository_root: Path,
    *,
    allowed_repository_roots: tuple[Path, ...],
) -> Path:
    return validate_repository_roots(
        (launch_repository_root,),
        allowed_repository_roots,
    )[0]


def _dependencies_from_context(ctx: click.Context) -> EnvironmentCliDependencies:
    if isinstance(ctx.obj, EnvironmentCliDependencies):
        return ctx.obj
    return EnvironmentCliDependencies()


def _emit_manifest(
    manifest: EnvironmentManifest,
    *,
    json_output: bool,
    running: bool | None = None,
) -> None:
    if json_output:
        try:
            click.echo(manifest_to_json(manifest, running=running))
        except EnvironmentValidationError as exc:
            raise click.ClickException(str(exc)) from exc
        return
    click.echo(f"{manifest.kind} {manifest.instance_id} prepared at {manifest.instance_root}")


def _resolved_instance_from_manifest(manifest: EnvironmentManifest) -> ResolvedEnvironmentInstance:
    return ResolvedEnvironmentInstance(
        kind=manifest.kind,
        instance_id=manifest.instance_id,
        environment_root=manifest.environment_root,
        instance_root=manifest.instance_root,
        db_path=manifest.db_path,
        managed_files_root=manifest.managed_files_root,
        hermes_home=manifest.hermes_home,
        logs_dir=manifest.logs_dir,
        dispatcher_lock_path=manifest.dispatcher_lock_path,
        server_control_socket_path=manifest.server_control_socket_path,
        port=manifest.port,
        credentials_env_file=manifest.credentials_env_file,
        allowed_repository_roots=manifest.repository_roots,
        expected_linux_account=manifest.expected_linux_account,
        fixture_version=manifest.fixture_version,
        prepared=manifest.prepared_at is not None,
        running=False,
    )
