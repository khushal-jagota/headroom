"""Click entry points for isolated Panels environments."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import click

from planner.conversation.hermes_backend_configuration import resolve_hermes_python
from planner.core.config import Config, load_config
from planner.environments.app import build_exported_app, validate_app_manifest
from planner.environments.app_compatibility import prove_previous_app_compatibility
from planner.environments.backup import create_database_backup, restore_database_snapshot
from planner.environments.contracts import (
    DynamicEnvironmentPort,
    EnvironmentKind,
    EnvironmentManifest,
    EnvironmentValidationError,
    FixedEnvironmentPort,
    ResolvedEnvironmentInstance,
)
from planner.environments.deployment import (
    HttpHealthClient,
    SubprocessServiceController,
    deploy_app,
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
    import_live_environment_state,
    inspect_environment_instance,
    manifest_to_json,
    prepare_environment_instance,
    remove_environment_instance,
    reset_environment_instance,
)
from planner.environments.repository_runtime import resolve_repository_runtime_python
from planner.environments.runtime_port import reserve_available_tcp_listener
from planner.environments.vps_status import (
    CleanupInventory,
    VpsStatusDependencies,
    apply_cleanup_inventory,
    collect_cleanup_inventory,
    collect_vps_status,
)

ExecFn = Callable[[str, list[str], Mapping[str, str]], object]
ResolveInstanceFn = Callable[..., ResolvedEnvironmentInstance]
MaterializeFn = Callable[..., EnvironmentManifest]
InspectInstanceFn = Callable[..., EnvironmentManifest]
ImportLiveFn = Callable[..., EnvironmentManifest]
ResolveRepositoryRuntimePythonFn = Callable[[Path], Path]
LoadConfigFn = Callable[[], Config]


@dataclass(frozen=True)
class EnvironmentCliDependencies:
    resolve_instance: ResolveInstanceFn = resolve_environment_instance
    prepare_instance: MaterializeFn = prepare_environment_instance
    inspect_instance: InspectInstanceFn = inspect_environment_instance
    import_live_state: ImportLiveFn = import_live_environment_state
    reset_instance: MaterializeFn = reset_environment_instance
    remove_instance: MaterializeFn = remove_environment_instance
    exec_fn: ExecFn = os.execvpe
    ambient_env: Mapping[str, str] | None = None
    resolve_repository_runtime_python: ResolveRepositoryRuntimePythonFn = (
        resolve_repository_runtime_python
    )
    load_config: LoadConfigFn = load_config
    vps_status_dependencies: VpsStatusDependencies | None = None


@click.group("environment")
def environment() -> None:
    """Prepare and run isolated Panels environments."""


def _environment_cli_dependencies() -> EnvironmentCliDependencies:
    dependencies = click.get_current_context().obj
    return (
        dependencies
        if isinstance(dependencies, EnvironmentCliDependencies)
        else EnvironmentCliDependencies()
    )


@environment.command("status")
@click.option(
    "--json", "as_json", is_flag=True, help="Print the sanitized status snapshot as JSON."
)
def status(as_json: bool) -> None:
    """Collect a local operational snapshot without contacting the HTTP server."""
    dependencies = _environment_cli_dependencies()
    snapshot = collect_vps_status(
        dependencies.load_config(),
        dependencies=dependencies.vps_status_dependencies,
    )
    payload = snapshot.as_dict()
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    click.echo(f"{payload['overall_state']}: {payload['collected_at']}")


@environment.command("cleanup")
@click.option(
    "--apply", "apply", is_flag=True, help="Apply a fresh, immediately re-proven inventory."
)
@click.option("--json", "as_json", is_flag=True, help="Print the inventory or result as JSON.")
def cleanup(apply: bool, as_json: bool) -> None:
    """Inspect or safely maintain host-local Panels state (dry run by default)."""
    dependencies = _environment_cli_dependencies()
    inventory: CleanupInventory = collect_cleanup_inventory(
        dependencies.load_config(), dependencies=dependencies.vps_status_dependencies
    )
    payload: dict[str, object] = {"dry_run": not apply, **inventory.as_dict()}
    if apply:
        payload["result"] = apply_cleanup_inventory(inventory).as_dict()
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    click.echo("applied cleanup" if apply else "cleanup dry run")
    for candidate in inventory.candidates:
        click.echo(f"{candidate.kind}: {candidate.path}")


@environment.command("backup")
@click.option(
    "--source-db", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True
)
@click.option("--backup-dir", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option("--deployed-revision", required=True)
def backup(source_db: Path, backup_dir: Path, deployed_revision: str) -> None:
    """Create one verified snapshot of the database and the managed-file tree."""
    try:
        snapshot = create_database_backup(source_db, backup_dir, deployed_revision)
    except (OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(snapshot))


@environment.command("app-build")
@click.option(
    "--source-root", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True
)
@click.option("--requested-sha", required=True)
@click.option("--candidate-app", type=click.Path(path_type=Path, file_okay=False), required=True)
def app_build(source_root: Path, requested_sha: str, candidate_app: Path) -> None:
    """Build and validate one host-native exact-SHA app."""
    try:
        manifest = build_exported_app(
            source_root,
            requested_sha=requested_sha,
            candidate_app=candidate_app,
            install_dependencies=True,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(manifest.as_dict(), sort_keys=True))


@environment.command("app-identity")
@click.option(
    "--app", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True
)
def app_identity(app: Path) -> None:
    """Print the validated SHA for one app directory."""
    try:
        manifest = validate_app_manifest(app / "manifest.json")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(manifest.app_sha)


@environment.command("backup-current")
@click.option(
    "--source-db", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True
)
@click.option("--backup-dir", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option(
    "--current-app",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
def backup_current(source_db: Path, backup_dir: Path, current_app: Path) -> None:
    """Validate the current app and back up the database and file tree with its identity."""
    try:
        revision = validate_app_manifest(current_app / "manifest.json").app_sha
        snapshot = create_database_backup(source_db, backup_dir, revision)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(snapshot))


@environment.command("app-deploy")
@click.option(
    "--candidate-app", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True
)
@click.option("--current-root", type=click.Path(path_type=Path), required=True)
@click.option("--source-db", type=click.Path(path_type=Path, dir_okay=False), required=True)
@click.option("--backup-dir", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option("--health-url", required=True)
@click.option("--service-manager", type=click.Choice(["systemctl", "launchctl"]), required=True)
@click.option("--service-name", required=True)
@click.option("--lock-path", type=click.Path(path_type=Path))
def app_deploy(
    candidate_app: Path,
    current_root: Path,
    source_db: Path,
    backup_dir: Path,
    health_url: str,
    service_manager: str,
    service_name: str,
    lock_path: Path | None,
) -> None:
    """Replace the one deployed app with backup, compatibility proof, and recovery."""
    try:
        result = deploy_app(
            candidate_app=candidate_app,
            current_root=current_root,
            source_db=source_db,
            backup=lambda revision: create_database_backup(source_db, backup_dir, revision),
            prove_compatibility=lambda candidate, current, database: (
                prove_previous_app_compatibility(
                    candidate_app=candidate,
                    current_app=current,
                    source_db=database,
                )
            ),
            service=SubprocessServiceController(service_manager, service_name),
            health=HttpHealthClient(health_url),
            lock_path=lock_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = json.dumps(result.__dict__, sort_keys=True)
    click.echo(payload)
    if result.status not in {"succeeded", "unchanged"}:
        detail = result.detail or "requested app is not running"
        raise click.ClickException(f"app deployment {result.status}: {detail}")


@environment.command("restore")
@click.option(
    "--snapshot", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True
)
@click.option("--destination-db", type=click.Path(path_type=Path, dir_okay=False), required=True)
@click.option("--live-stopped", is_flag=True, required=True)
def restore(snapshot: Path, destination_db: Path, live_stopped: bool) -> None:
    """Restore a verified snapshot's database and file tree into a stopped live environment."""
    try:
        restore_database_snapshot(snapshot, destination_db, live_stopped=live_stopped)
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(destination_db))


@environment.command("prepare")
@click.option("--kind", type=click.Choice(["live", "staging"]), required=True)
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
@click.option("--kind", type=click.Choice(["live", "staging"]), required=True)
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


@environment.command("import-live")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option(
    "--source-db",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--source-managed-files-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--source-hermes-home",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--source-runtime-user-home",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--source-logs-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def import_live(
    ctx: click.Context,
    *,
    environment_root: Path,
    source_db: Path,
    source_managed_files_root: Path,
    source_hermes_home: Path,
    source_runtime_user_home: Path,
    source_logs_root: Path,
    json_output: bool,
) -> None:
    """Import or restore durable state into a prepared, stopped live environment."""
    deps = _dependencies_from_context(ctx)
    try:
        manifest = deps.import_live_state(
            environment_root=environment_root,
            source_db_path=source_db,
            source_managed_files_root=source_managed_files_root,
            source_hermes_home=source_hermes_home,
            source_runtime_user_home=source_runtime_user_home,
            source_logs_root=source_logs_root,
        )
    except EnvironmentValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    _emit_manifest(manifest, json_output=json_output, running=False)


@environment.command("reset")
@click.option("--kind", type=click.Choice(["staging"]), required=True)
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
    """Rebuild staging fake state while preserving its prepared identity."""
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
@click.option("--kind", type=click.Choice(["staging"]), required=True)
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
    """Remove the stopped staging environment."""
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
@click.option("--kind", type=click.Choice(["live", "staging"]), required=True)
@click.option("--instance-id")
@click.option("--environment-root", type=click.Path(path_type=Path), required=True)
@click.option(
    "--environment-manager-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option("--json", "json_output", is_flag=True)
@click.pass_context
def render_linux(
    ctx: click.Context,
    *,
    kind: EnvironmentKind,
    instance_id: str | None,
    environment_root: Path,
    environment_manager_root: Path,
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
        rendered = render_linux_specification(
            manifest,
            environment_manager_root=environment_manager_root,
        )
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
                    "strict_writable_paths": [str(path) for path in rendered.strict_writable_paths],
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


@environment.command("run")
@click.option("--kind", type=click.Choice(["live", "staging"]), required=True)
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
    listener = None
    if isinstance(instance.port_policy, FixedEnvironmentPort):
        runtime_port = instance.port_policy.port
    elif isinstance(instance.port_policy, DynamicEnvironmentPort):
        listener, runtime_port = reserve_available_tcp_listener(
            bind_attempts=instance.port_policy.bind_attempts
        )
    else:  # pragma: no cover - union exhaustiveness
        raise EnvironmentValidationError("unknown environment runtime port policy")

    environment_builder = build_test_environment_run_env if test_mode else build_environment_run_env
    run_env = environment_builder(
        instance,
        credentials=credentials,
        ambient=ambient,
        hermes_python=hermes_python,
        runtime_port=runtime_port,
    )
    if listener is not None:
        run_env["PLAN_SERVER_LISTENER_FD"] = str(listener.fileno())
        run_env["PLAN_SERVER_LIFECYCLE_LEASE_PATH"] = str(
            instance.instance_root / "run" / "server-lifecycle.lock"
        )
        click.echo(f"staging http://127.0.0.1:{runtime_port}", err=True)
    launch_root = _validated_launch_repository_root(
        launch_repository_root,
        allowed_repository_roots=instance.allowed_repository_roots,
    )
    launch_interpreter = deps.resolve_repository_runtime_python(launch_root)
    argv = [str(launch_interpreter), "-m", "planner", "serve"]
    previous_cwd = Path.cwd()
    os.chdir(launch_root)
    try:
        deps.exec_fn(str(launch_interpreter), argv, run_env)
    finally:
        if listener is not None:
            listener.close()
        if Path.cwd() == launch_root:
            os.chdir(previous_cwd)


def _one_caller_trusted_repository_root(repository_roots: tuple[Path, ...]) -> Path:
    if len(repository_roots) != 1:
        raise EnvironmentValidationError("environment run requires exactly one repository root")
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
        runtime_user_home=manifest.runtime_user_home,
        logs_dir=manifest.logs_dir,
        dispatcher_lock_path=manifest.dispatcher_lock_path,
        server_control_socket_path=manifest.server_control_socket_path,
        port_policy=manifest.port_policy,
        credentials_env_file=manifest.credentials_env_file,
        allowed_repository_roots=manifest.repository_roots,
        expected_linux_account=manifest.expected_linux_account,
        fixture_version=manifest.fixture_version,
        prepared=manifest.prepared_at is not None,
        running=False,
    )
