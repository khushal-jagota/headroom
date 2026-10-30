"""Read metadata from the exact destination launch, without an agent prompt."""

from __future__ import annotations

import asyncio
import os
import time
import tomllib
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from planner.conversation.backends.claude_model_catalog import (
    ClaudeModelCatalogUnavailable,
    probe_claude_model_catalog,
)
from planner.conversation.backends.codex_app_server.client import child_environment
from planner.conversation.backends.codex_app_server.login import (
    CodexLoginGeneration,
    codex_home,
    read_stored_codex_login,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModelCatalogUnavailable,
    probe_codex_model_catalog,
)
from planner.conversation.backends.contracts import BackendEmissionInputs
from planner.conversation.backends.probe_lifetime import owned_probe
from planner.conversation.contracts import ConversationBackendKey, WorkspacePathPolicy
from planner.conversation.model_resolution.config import (
    CATALOGUE_COLD_START_TIMEOUT_SECONDS,
)
from planner.conversation.model_resolution.contracts import (
    CatalogEvidenceReference,
    CatalogObservation,
    CatalogResult,
    CatalogRow,
    CatalogUnavailable,
    CatalogWorkspaceBinding,
    CorrectiveAction,
    LaunchContext,
    PrerequisiteStatus,
    SelectionKind,
    ValidationReason,
)
from planner.conversation.production_backends import (
    ProductionBackendLaunches,
    production_backend_launches,
)
from planner.conversation.snapshot import (
    BackendIdentityStatus,
    CodexLoginStanding,
    SubprocessBackendProbeEnvironment,
    read_backend_identity_status,
)
from planner.executor.contracts import ExecutorIdentity
from planner.executor.workspace import localize_workspace
from planner.tickets.contracts import TicketLocale


class ProductionCatalogObserver:
    """Own each read-only observation and all subprocess cleanup it admits.

    Opaque scope IDs compare private effective inputs. They contain no environment,
    credential-derived digest, or configuration content. A restart invalidates them.
    """

    def __init__(
        self,
        *,
        destination: TicketLocale,
        panels_server_url: str,
        executor_identity: ExecutorIdentity | None = None,
        workspace_home: Path | None = None,
        launches: Callable[[], ProductionBackendLaunches] | None = None,
        environment: SubprocessBackendProbeEnvironment | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        if (destination is TicketLocale.macos) != (executor_identity is not None):
            raise ValueError("Destination and executor identity disagree")
        self.destination = destination
        self.executor_identity = executor_identity
        self._workspace_home = Path.home() if workspace_home is None else workspace_home
        self._launches = launches or (
            lambda: production_backend_launches(panels_server_url=panels_server_url)
        )
        self._environment = environment or SubprocessBackendProbeEnvironment()
        self._now = now
        self._contexts: dict[tuple[object, ...], str] = {}

    def _workspace(self, workspace: CatalogWorkspaceBinding) -> Path:
        if (
            workspace.workspace_path_policy is WorkspacePathPolicy.native_absolute
            and workspace.workspace_home_relative is not None
        ):
            raise ValueError("Native absolute workspace cannot use a home mapping")
        if self.destination is TicketLocale.vps and workspace.workspace_home_relative is not None:
            raise ValueError("VPS workspace cannot use a native home mapping")
        return localize_workspace(
            workspace.workspace_folder,
            workspace_home=self._workspace_home,
            workspace_home_relative=workspace.workspace_home_relative,
        )

    def _launch(self, backend_key: ConversationBackendKey) -> tuple[str, dict[str, str]]:
        launches = self._launches()
        if backend_key is ConversationBackendKey.codex:
            return launches.codex.argv[0], child_environment(
                overrides=launches.codex.environment_overrides
            )
        if backend_key is ConversationBackendKey.claude:
            executable = launches.claude.claude_executable
            if executable is None:
                raise ValueError("The production Claude executable is unspecified")
            return str(executable), {**os.environ, **dict(launches.claude.environment_overrides)}
        raise ValueError("This provider has no scoped catalog observer")

    async def capture_context(
        self, backend_key: ConversationBackendKey, *, workspace: CatalogWorkspaceBinding
    ) -> LaunchContext:
        """Capture actual execution ownership without probing configuration."""
        localized = self._workspace(workspace)
        _, environment = self._launch(backend_key)
        generation = _account_generation(backend_key, environment, localized)
        # Unknown accounts stay local to this capture/probe operation. Known login
        # generations can share advisory rows within this runtime owner only.
        facts = (
            self.destination,
            backend_key,
            self.executor_identity,
            workspace,
            localized,
            generation,
        )
        context_ref = (
            self._contexts.setdefault(facts, uuid4().hex) if generation is not None else uuid4().hex
        )
        return LaunchContext(
            context_ref,
            self.destination,
            backend_key,
            self.executor_identity,
            workspace,
            localized,
            None,
            None,
            None,
            generation,
        )

    def verify_context(self, expected: LaunchContext) -> bool:
        """The owner and authorized directory are the observable admission boundary."""
        try:
            return (
                expected.destination == self.destination
                and expected.executor_identity == self.executor_identity
                and self._workspace(expected.workspace) == expected.localized_workspace_folder
            )
        except (OSError, ValueError):
            return False

    def verify_prepared_launch(
        self, expected: LaunchContext, prepared: BackendEmissionInputs
    ) -> bool:
        if not self.verify_context(expected):
            return False
        if prepared.executable is None:
            return True
        executable, environment = self._launch(expected.backend_key)
        return _resolve_launch_executable(
            executable, environment, prepared.workspace
        ) == _resolve_launch_executable(
            prepared.executable, prepared.environment or environment, prepared.workspace
        )

    def _unavailable(
        self,
        backend_key: ConversationBackendKey,
        context: LaunchContext | None,
        reason: ValidationReason,
        action: CorrectiveAction = CorrectiveAction.verify_launch_context,
    ) -> CatalogUnavailable:
        return CatalogUnavailable(
            backend_key,
            self.destination,
            context,
            reason,
            action,
            "The destination catalog could not verify this launch context.",
            int(self._now()),
            None,
        )

    async def observe(
        self,
        backend_key: ConversationBackendKey,
        *,
        workspace: CatalogWorkspaceBinding,
        attempt_id: str,
        deadline: float,
    ) -> CatalogResult:
        captured: LaunchContext | None = None
        observed_at = int(self._now())

        async def operation() -> CatalogResult:
            nonlocal captured
            captured = await self.capture_context(backend_key, workspace=workspace)
            executable, environment = self._launch(backend_key)
            credentials = PrerequisiteStatus.unknown
            status_arguments = (
                ("login", "status")
                if backend_key is ConversationBackendKey.codex
                else ("auth", "status", "--json")
            )
            status = await self._environment.run(
                (str(executable), *status_arguments),
                timeout_seconds=30,
                environment_overrides=environment,
                working_directory=captured.localized_workspace_folder,
            )
            codex_login = None
            if backend_key is ConversationBackendKey.codex and _uses_default_codex_account(
                environment, captured.localized_workspace_folder
            ):
                codex_login = CodexLoginStanding(
                    read_stored_codex_login(codex_home(environment)), rejected=False
                )
            identity_status = read_backend_identity_status(
                backend_key,
                status,
                codex_login=codex_login,
                now=datetime.fromtimestamp(self._now(), UTC),
            )
            credentials = {
                BackendIdentityStatus.authenticated: PrerequisiteStatus.available,
                BackendIdentityStatus.unauthenticated: PrerequisiteStatus.unavailable,
                BackendIdentityStatus.unknown: PrerequisiteStatus.unknown,
            }[identity_status]
            rows: tuple[CatalogRow, ...]
            if backend_key is ConversationBackendKey.codex:
                catalog = await probe_codex_model_catalog(
                    str(executable),
                    environment=environment,
                    working_directory=captured.localized_workspace_folder,
                )
                rows = tuple(
                    CatalogRow(
                        model.model_id,
                        SelectionKind.concrete,
                        model.model_id,
                        model.display_name or None,
                        model.reasoning_effort_options,
                        False,
                        None,
                        model.model_id,
                        "codex:model/list",
                    )
                    for model in catalog.models
                )
                complete, limited = catalog.complete, catalog.visibility_limited
            else:
                claude_catalog = await probe_claude_model_catalog(
                    str(executable),
                    environment=environment,
                    working_directory=captured.localized_workspace_folder,
                )
                rows = tuple(
                    CatalogRow(
                        model.model_id,
                        SelectionKind.concrete
                        if model.model_id == model.resolved_model_id
                        else SelectionKind.provider_alias,
                        model.resolved_model_id,
                        model.display_name or None,
                        model.reasoning_effort_options,
                        model.hidden,
                        model.enabled,
                        model.model_id if model.model_id == model.resolved_model_id else None,
                        "claude:list_models",
                    )
                    for model in claude_catalog.models
                )
                complete, limited = claude_catalog.complete, claude_catalog.visibility_limited
            identity = uuid4().hex
            observation = CatalogObservation(
                CatalogEvidenceReference(
                    identity, identity, captured, observed_at, attempt_id, 0.0
                ),
                rows,
                complete,
                limited,
                False,
                PrerequisiteStatus.available,
                credentials,
            )
            return observation

        try:
            return await owned_probe(
                operation,
                timeout_seconds=min(
                    CATALOGUE_COLD_START_TIMEOUT_SECONDS,
                    max(0, deadline - asyncio.get_running_loop().time()),
                ),
            )
        except TimeoutError:
            return self._unavailable(
                backend_key, captured, ValidationReason.timeout, CorrectiveAction.refresh_catalog
            )
        except (CodexModelCatalogUnavailable, ClaudeModelCatalogUnavailable):
            return self._unavailable(
                backend_key,
                captured,
                ValidationReason.catalog_unavailable,
                CorrectiveAction.refresh_catalog,
            )
        except (OSError, ValueError):
            return self._unavailable(
                backend_key, captured, ValidationReason.configuration_scope_unknown
            )


def _account_generation(
    backend_key: ConversationBackendKey, environment: Mapping[str, str], workspace: Path
) -> CodexLoginGeneration | None:
    if backend_key is not ConversationBackendKey.codex or not _uses_default_codex_account(
        environment, workspace
    ):
        return None
    return read_stored_codex_login(codex_home(environment)).generation


def _uses_default_codex_account(environment: Mapping[str, str], workspace: Path) -> bool:
    if any(environment.get(key) for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")):
        return False
    # Project or managed configuration can replace the effective provider route.
    if any(
        (parent / ".codex" / "config.toml").exists() for parent in (workspace, *workspace.parents)
    ):
        return False
    if any(
        path.exists()
        for path in (
            Path("/etc/codex/config.toml"),
            Path("/etc/codex/managed_config.toml"),
            Path("/Library/Application Support/Codex/config.toml"),
        )
    ):
        return False
    try:
        configuration = tomllib.loads((codex_home(environment) / "config.toml").read_text())
        if (
            configuration.get("model_provider", "openai") != "openai"
            or configuration.get("cli_auth_credentials_store", "file") != "file"
            or "model_providers" in configuration
            or "profile" in configuration
            or "profiles" in configuration
            or _has_indirect_source(configuration)
        ):
            return False
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return False
    return True


def _has_indirect_source(document: object) -> bool:
    """Unknown source indirection requires actual configuration verification."""
    if isinstance(document, dict):
        for key, value in document.items():
            if isinstance(key, str) and any(
                marker in key.lower()
                for marker in ("include", "import", "config_path", "settings_path", "managed")
            ):
                return True
            if _has_indirect_source(value):
                return True
    elif isinstance(document, list):
        return any(_has_indirect_source(item) for item in document)
    return False


def _resolve_launch_executable(
    executable: str, environment: Mapping[str, str], workspace: Path
) -> Path | None:
    """Identify the launch lookup without changing its executable token or policy.

    POSIX exec resolves slash-relative tokens and relative PATH entries against the
    child cwd. The Panels process cwd cannot establish that binary's identity.
    """
    if not executable:
        return None
    if os.path.dirname(executable):
        candidates = [Path(executable)]
    else:
        candidates = [Path(entry) / executable for entry in os.get_exec_path(environment)]
    for candidate in candidates:
        if not candidate.is_absolute():
            candidate = workspace / candidate
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return resolved
        except OSError:
            continue
    return None
