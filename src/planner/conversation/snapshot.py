"""What each agent backend on this machine is, and whether it needs anything from you.

A backend snapshot is a product object, not a health check hidden in a log: is the CLI
there, which version, who is it logged in as, what can it be run as, and whether Panels
can advise an update. Every answer here is either something the machine told us or an
honest absence. The snapshot never guesses, and it never reaches an agent API — identity
comes from the CLI's own local credential read, which costs a subprocess and nothing else.

Three rules hold the module together.

**One classifier for every backend.** Where a binary lives says how it was installed, and
that says how to update it. The same function classifies supported update paths. Hermes
does not offer update discovery or an update action here.

**An advisory is never a blocker.** A backend with no update advisory, or with a failed
probe, still runs. The card says what is known and offers what can be acted on.

**Absence is stated, not implied.** A backend with no models listed says why. A backend
that exposes no identity at all has no identity field rather than an empty one. Signing in
is out of band everywhere: the card names the terminal command and gets out of the way.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

import anyio
import httpx

from planner.conversation.backends.claude_model_catalog import (
    ClaudeModelCatalog,
    ClaudeModelCatalogUnavailable,
    probe_claude_model_catalog,
)
from planner.conversation.backends.codex_app_server.login import (
    CodexLoginGeneration,
    CodexLoginRejectionMemory,
    StoredCodexLogin,
    read_stored_codex_login,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModelCatalog,
    CodexModelCatalogUnavailable,
    probe_codex_model_catalog,
)
from planner.conversation.backends.probe_lifetime import owned_probe
from planner.conversation.contracts import ConversationBackendKey

if TYPE_CHECKING:
    from planner.conversation.model_resolution.contracts import (
        CatalogResult,
        CatalogWorkspaceBinding,
    )
    from planner.executor.contracts import ExecutorIdentity


@dataclass(frozen=True, slots=True)
class BackendSnapshotScope:
    """Destination configuration belongs to one workspace, account and runtime owner."""

    backend_key: ConversationBackendKey
    destination: str
    workspace: CatalogWorkspaceBinding
    executor_identity: ExecutorIdentity | None = None
    account_generation: CodexLoginGeneration | None = None


# How codex and claude are asked what they can be run as. Named as parameters because
# asking spawns a child: a test describes the answer instead of spawning one.
type CodexModelCatalogProbe = Callable[[str], Awaitable[CodexModelCatalog]]
type ClaudeModelCatalogProbe = Callable[[str], Awaitable[ClaudeModelCatalog]]

# A probe is a local command. These are generous enough for a cold start and short enough
# that a card is never left hanging on one.
VERSION_PROBE_TIMEOUT_SECONDS: Final = 20.0
IDENTITY_PROBE_TIMEOUT_SECONDS: Final = 30.0
MODEL_CATALOG_PROBE_TIMEOUT_SECONDS: Final = 30.0
REGISTRY_LOOKUP_TIMEOUT_SECONDS: Final = 4.0

# An update installs a package over a network. It gets its own, much longer, window.
UPDATE_COMMAND_TIMEOUT_SECONDS: Final = 600.0

# How much of a failed command's output is kept. Enough to see what went wrong, not enough
# to be a log file in a browser.
UPDATE_OUTPUT_TAIL_MAXIMUM_CHARACTERS: Final = 10_000

VERSION_ARGUMENTS: Final = ("--version",)

# Three numbers, optionally introduced by a `v`, and not part of a longer run of numbers.
# The last part matters: hermes prints its version next to a four-part build stamp
# (`v0.18.2 (2026.7.7.2)`), and a looser pattern reads the stamp as the version.
_VERSION_PATTERN: Final = re.compile(r"(?<![\d.])v?(\d+\.\d+\.\d+)(?![\d.])")

_NPM_REGISTRY_URL: Final = "https://registry.npmjs.org"


class BackendInstallMethod(StrEnum):
    """How a backend's binary got onto this machine, which is how it can be updated."""

    npm_global = "npm_global"
    bun_global = "bun_global"
    pnpm_global = "pnpm_global"
    homebrew = "homebrew"
    native = "native"
    manual_only = "manual_only"


class BackendIdentityStatus(StrEnum):
    """Whether the CLI is signed in, as the CLI itself reports it.

    ``unknown`` is a real answer and never a stand-in for signed out: it means the probe
    did not come back with something we can read, and inventing an account from that would
    be worse than saying so.
    """

    authenticated = "authenticated"
    unauthenticated = "unauthenticated"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class BackendIdentity:
    """Who a backend's CLI is signed in as, and how to change that."""

    status: BackendIdentityStatus
    account_label: str | None = None
    detail: str | None = None
    login_command: str | None = None


@dataclass(frozen=True, slots=True)
class BackendModel:
    """One model a backend can be run as.

    ``detail`` is one honest secondary line where a backend has one — claude's says
    which concrete model an alias reaches right now.

    ``reasoning_effort_options`` are the efforts THIS model takes, which is not always
    the backend's whole list: codex and claude both say so per model, and a picker that
    offered a model the efforts of its siblings would offer values that model refuses.
    Null means unknown metadata. An empty tuple means known empty support.
    """

    model_id: str
    display_name: str | None = None
    detail: str | None = None
    reasoning_effort_options: tuple[str, ...] | None = None
    selected_variant: str | None = None
    resolved_model_id: str | None = None
    catalogue_warning: str | None = None


@dataclass(frozen=True, slots=True)
class BackendUpdateAdvisory:
    """Whether there is a newer CLI, and what updating would actually run.

    ``update_command`` being absent is the honest end of the road for an install Panels
    cannot drive — the detail says so, and no button is offered.
    """

    install_method: BackendInstallMethod
    update_command: tuple[str, ...] | None
    latest_version: str | None
    update_available: bool
    detail: str


@dataclass(frozen=True, slots=True)
class BackendSnapshot:
    """Everything a surface needs to say what this backend is right now.

    ``identity`` is absent for a backend that has no account to be signed in to, rather
    than present-and-unknown. Empty ``available_models`` or ``reasoning_effort_options``
    mean the backend offers none to choose from, and are rendered as absence — a picker
    must not invent an option the backend would refuse.
    """

    backend_key: ConversationBackendKey
    installed: bool
    executable_path: str | None
    version: str | None
    identity: BackendIdentity | None
    available_models: tuple[BackendModel, ...]
    reasoning_effort_options: tuple[str, ...]
    # What this backend runs when nobody picks. They are concrete values from the lists
    # above, because "default" is not something a person can be shown as running — a
    # surface offering a choice pre-selects these rather than offering a word. Absent
    # means the backend does not say, which is not the same as there being none.
    default_model_id: str | None
    default_reasoning_effort: str | None
    update_advisory: BackendUpdateAdvisory | None
    diagnoses: tuple[str, ...]
    models_complete: bool = False
    catalog_account_generation: CodexLoginGeneration | None = None


class BackendUpdateOutcome(StrEnum):
    """How an update went, told apart rather than assumed.

    ``unchanged`` is the one that matters: a command that exits cleanly and leaves the
    version where it was has not updated anything, and saying "updated" would be a lie.
    """

    succeeded = "succeeded"
    unchanged = "unchanged"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class BackendUpdateResult:
    outcome: BackendUpdateOutcome
    detail: str
    output_tail: str


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """A command that was run, as it finished. A command that would not run is exit -1."""

    exit_code: int
    standard_output: str
    standard_error: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    def output_tail(self, maximum_characters: int) -> str:
        combined = "\n".join(part for part in (self.standard_output, self.standard_error) if part)
        return combined[-maximum_characters:]


class BackendProbeEnvironment(Protocol):
    """The machine, as the snapshot is allowed to touch it.

    Everything that leaves this process goes through here: finding a binary, following it
    to where it really lives, running a command, asking a registry what the latest version
    is. A test hands over a stand-in and gets a snapshot without a single subprocess.
    """

    def executable_path(self, executable_name: str) -> str | None: ...

    def configured_executable_path(self, executable_name: str) -> str | None: ...

    def real_path(self, path: str) -> str: ...

    def user_local_npm_prefix(self) -> str: ...

    def prefix_is_owned_and_writable(self, prefix: str) -> bool: ...

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> CommandOutcome: ...

    async def latest_released_version(
        self, package_name: str, *, timeout_seconds: float
    ) -> str | None: ...


class SubprocessBackendProbeEnvironment:
    """The real machine: PATH, real paths, child processes, and the npm registry."""

    def executable_path(self, executable_name: str) -> str | None:
        if executable_name == "claude":
            from planner.conversation.production_backends import production_backend_launches

            selected = production_backend_launches(panels_server_url="").claude.claude_executable
            return str(selected) if selected is not None and selected.is_file() else None
        return shutil.which(executable_name)

    def configured_executable_path(self, executable_name: str) -> str | None:
        if executable_name != "hermes":
            return None
        from planner.environments.hermes_home import resolve_hermes_python

        hermes_executable = resolve_hermes_python().with_name("hermes")
        return str(hermes_executable) if hermes_executable.is_file() else None

    def real_path(self, path: str) -> str:
        return os.path.realpath(path)

    def user_local_npm_prefix(self) -> str:
        return str(Path.home() / ".local")

    def prefix_is_owned_and_writable(self, prefix: str) -> bool:
        """Whether an npm prefix can be changed by this service identity.

        The prefix and the existing npm directories are checked as the effective user.
        This deliberately does not try to repair ownership or ask for privilege: an
        install that does not already belong to the service is manual-only.
        """
        try:
            effective_uid = os.geteuid()
        except AttributeError:
            return False
        prefix_path = Path(prefix)
        candidates = (prefix_path, prefix_path / "lib", prefix_path / "lib" / "node_modules")
        try:
            for candidate in candidates:
                if not candidate.is_dir():
                    return False
                metadata = candidate.stat()
                if metadata.st_uid != effective_uid or not os.access(candidate, os.W_OK | os.X_OK):
                    return False
        except OSError:
            return False
        return True

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        environment_overrides: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> CommandOutcome:
        environment = dict(os.environ)
        if environment_overrides is not None:
            environment.update(environment_overrides)

        async def operation() -> CommandOutcome:
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=environment,
                    cwd=working_directory,
                    start_new_session=True,
                )
            except (OSError, ValueError) as error:
                return CommandOutcome(-1, "", str(error))
            try:
                standard_output, standard_error = await process.communicate()
                return CommandOutcome(
                    process.returncode if process.returncode is not None else -1,
                    standard_output.decode("utf-8", errors="replace"),
                    standard_error.decode("utf-8", errors="replace"),
                )
            except BaseException:
                with anyio.CancelScope(shield=True):
                    _end_the_whole_process_group(process)
                    await process.wait()
                raise

        try:
            return await owned_probe(operation, timeout_seconds=timeout_seconds)
        except TimeoutError:
            return CommandOutcome(
                -1, "", f"{argv[0]} did not answer within {timeout_seconds:g}s"
            )

    async def latest_released_version(
        self, package_name: str, *, timeout_seconds: float
    ) -> str | None:
        """The newest published version, or nothing at all if the registry did not say.

        A lookup that fails is not an error anyone needs to see: the advisory is the only
        thing that depends on it, and an advisory nobody can produce is simply absent.
        """
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(f"{_NPM_REGISTRY_URL}/{package_name}/latest")
                response.raise_for_status()
                published = response.json()
        except Exception:
            return None
        if not isinstance(published, dict):
            return None
        version = published.get("version")
        return version if isinstance(version, str) and version else None


# --- how a path says what installed it ------------------------------------------------


def _end_the_whole_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill the command and everything it started.

    A command that has run out of time is one nobody is waiting for any more, so nothing
    it spawned should carry on working on this machine either. If the group has already
    gone there is nothing to do; if this process may not signal it, the child itself is
    still killed rather than left running.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
        return
    except (ProcessLookupError, PermissionError):
        pass
    with suppress(ProcessLookupError):
        process.kill()


def _normalized_path(path: str) -> str:
    return path.replace("\\", "/").lower()


_INSTALL_METHOD_PATH_MARKERS: Final[tuple[tuple[BackendInstallMethod, tuple[str, ...]], ...]] = (
    (BackendInstallMethod.bun_global, ("/.bun/bin/",)),
    (
        BackendInstallMethod.pnpm_global,
        ("/.local/share/pnpm/", "/library/pnpm/", "/local/share/pnpm/", "/pnpm/global/"),
    ),
    (
        BackendInstallMethod.npm_global,
        ("/node_modules/.bin/", "/lib/node_modules/", "/npm/node_modules/"),
    ),
    (
        BackendInstallMethod.homebrew,
        ("/opt/homebrew/cellar/", "/usr/local/cellar/", "/homebrew/bin/"),
    ),
)


def classify_install_method(
    candidate_paths: Sequence[str], *, native_path_marker: str | None
) -> BackendInstallMethod:
    """Work out how a binary was installed from where it lives.

    Both the path found on PATH and the path it really resolves to are classified, because
    the useful one is usually the second: a Homebrew binary is a symlink out of ``bin``
    into ``Cellar``, and a vendor's own installer hides behind a link in ``~/.local/bin``.

    A backend's own installer wins when its marker matches, because a CLI that ships an
    updater knows better than a package manager that did not put it there. Everything else
    is decided by the same path substrings for every backend. Nothing matching means
    nobody here can update it, which is a real answer and not a failure.
    """
    normalized = [_normalized_path(path) for path in candidate_paths]
    if native_path_marker is not None:
        marker = _normalized_path(native_path_marker)
        if any(marker in path for path in normalized):
            return BackendInstallMethod.native
    for install_method, markers in _INSTALL_METHOD_PATH_MARKERS:
        if any(marker in path for path in normalized for marker in markers):
            return install_method
    return BackendInstallMethod.manual_only


@dataclass(frozen=True, slots=True)
class _ResolvedInstall:
    install_method: BackendInstallMethod
    package_prefix: str | None = None


def _npm_prefix_from_path(path: str) -> str | None:
    """Find the global npm prefix from a resolved package binary path."""
    normalized = path.replace("\\", "/")
    lowered = normalized.lower()
    for marker in ("/lib/node_modules/", "/npm/node_modules/", "/node_modules/"):
        position = lowered.find(marker)
        if position >= 0:
            return normalized[:position] or "/"
    return None


def _same_path(left: str, right: str) -> bool:
    return _normalized_path(os.path.realpath(left)) == _normalized_path(os.path.realpath(right))


def _resolve_install(
    recipe: _BackendProbeRecipe,
    executable_path: str,
    environment: BackendProbeEnvironment,
) -> _ResolvedInstall:
    real_path = environment.real_path(executable_path)
    classified = classify_install_method(
        (executable_path, real_path), native_path_marker=recipe.native_path_marker
    )
    if classified is BackendInstallMethod.native:
        return _ResolvedInstall(classified)
    if classified is not BackendInstallMethod.npm_global:
        return _ResolvedInstall(BackendInstallMethod.manual_only)

    package_prefix = _npm_prefix_from_path(real_path)
    expected_prefix = environment.user_local_npm_prefix()
    if (
        package_prefix is None
        or not _same_path(package_prefix, expected_prefix)
        or not environment.prefix_is_owned_and_writable(package_prefix)
    ):
        return _ResolvedInstall(BackendInstallMethod.manual_only)
    return _ResolvedInstall(classified, package_prefix)


def parse_version(output: str) -> str | None:
    """The version in a ``--version`` line, whatever else the line says around it."""
    found = _VERSION_PATTERN.search(output)
    return None if found is None else found.group(1)


def _version_is_newer(latest: str, installed: str) -> bool:
    def parts(version: str) -> tuple[int, ...] | None:
        try:
            return tuple(int(part) for part in version.split("."))
        except ValueError:
            return None

    latest_parts = parts(latest)
    installed_parts = parts(installed)
    if latest_parts is None or installed_parts is None:
        return latest != installed
    return latest_parts > installed_parts


# --- what each backend is, as data ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _IdentityRequest:
    """What an identity reader has to work with.

    Claude's login is a question its CLI answers in full, so its reader uses the outcome
    and nothing else. Codex's cannot be: ``codex login status`` reads the stored file and
    reports that it exists, which is not the same as the server still accepting it. So the
    saved login and anything a turn has already established about it travel here too.

    ``codex_login`` is absent when nobody took that reading, which is a real state and not
    an empty login: the CLI's own word is then all there is, and it stands.
    """

    outcome: CommandOutcome
    codex_login: CodexLoginStanding | None
    # The instant this card is about. One clock for the staleness check and the wording,
    # so a card cannot be re-probed for an expiry its own reader then disagrees with.
    now: datetime


@dataclass(frozen=True, slots=True)
class _CatalogRequest:
    environment: BackendProbeEnvironment
    version: str | None
    executable_path: str
    refresh: bool
    codex_model_catalog_probe: CodexModelCatalogProbe
    claude_model_catalog_probe: ClaudeModelCatalogProbe


@dataclass(frozen=True, slots=True)
class _CatalogAnswer:
    """What a backend can be run as, and anything honest to say about not knowing."""

    models: tuple[BackendModel, ...] = ()
    reasoning_effort_options: tuple[str, ...] = ()
    default_model_id: str | None = None
    default_reasoning_effort: str | None = None
    diagnoses: tuple[str, ...] = ()
    complete: bool = False


@dataclass(frozen=True, slots=True)
class _BackendProbeRecipe:
    """The per-backend facts the one shared probe runs on."""

    executable_name: str
    missing_binary_diagnosis: str
    identity_arguments: tuple[str, ...] | None
    read_identity: Callable[[_IdentityRequest], BackendIdentity] | None
    login_command: str | None
    registry_package_name: str | None
    homebrew_formula: str | None
    native_path_marker: str | None
    native_update_command: tuple[str, ...] | None
    manual_only_detail: str
    read_catalog: Callable[[_CatalogRequest], Awaitable[_CatalogAnswer]]


# --- claude ------------------------------------------------------------------------------

# `claude auth status --json` reads the credentials already on this machine. Verified on
# 2.1.219 with every network route blackholed: same answer, in a fifth of a second.
_CLAUDE_IDENTITY_ARGUMENTS: Final = ("auth", "status", "--json")

# What the effort picker falls back to when claude cannot be asked: the levels
# `claude --effort` documents on the installed CLI. When the catalog probe answers,
# the CLI's own per-model levels replace this.
_CLAUDE_FALLBACK_REASONING_EFFORT_OPTIONS: Final = ("low", "medium", "high", "xhigh", "max")


def _read_claude_identity(request: _IdentityRequest) -> BackendIdentity:
    outcome = request.outcome
    if not outcome.succeeded:
        return BackendIdentity(status=BackendIdentityStatus.unknown)
    try:
        reported = json.loads(outcome.standard_output)
    except ValueError:
        return BackendIdentity(status=BackendIdentityStatus.unknown)
    if not isinstance(reported, dict):
        return BackendIdentity(status=BackendIdentityStatus.unknown)
    logged_in = reported.get("loggedIn")
    if not isinstance(logged_in, bool):
        return BackendIdentity(status=BackendIdentityStatus.unknown)
    if not logged_in:
        return BackendIdentity(status=BackendIdentityStatus.unauthenticated)
    return BackendIdentity(
        status=BackendIdentityStatus.authenticated,
        account_label=_optional_text(reported.get("email")),
        detail=_claude_plan_detail(reported),
    )


def _claude_plan_detail(reported: Mapping[str, Any]) -> str | None:
    subscription = _optional_text(reported.get("subscriptionType"))
    method = _optional_text(reported.get("authMethod"))
    if subscription is not None and method is not None:
        return f"{subscription} plan via {method}"
    return subscription or method


async def _claude_catalog(request: _CatalogRequest) -> _CatalogAnswer:
    """Claude's aliases move, so the catalog is read from the CLI's own handshake.

    Each entry says what ``--model`` accepts, which concrete model that value reaches
    right now, and the effort levels that model takes — the CLI's account, not a list
    written down here that would quietly rot.
    """
    try:
        catalog = await request.claude_model_catalog_probe(request.executable_path)
    except ClaudeModelCatalogUnavailable:
        return _CatalogAnswer(
            reasoning_effort_options=_CLAUDE_FALLBACK_REASONING_EFFORT_OPTIONS,
            diagnoses=(
                "Claude is installed but did not answer when asked what it can run, so "
                "no models are listed. Check that `claude` starts from a terminal.",
            ),
        )
    return _CatalogAnswer(
        complete=catalog.complete,
        models=tuple(
            BackendModel(
                model_id=model.model_id,
                display_name=model.display_name,
                detail=f"{model.model_id} → {model.resolved_model_id}",
                resolved_model_id=model.resolved_model_id,
                reasoning_effort_options=model.reasoning_effort_options,
            )
            for model in catalog.models if not getattr(model, "hidden", False)
        ),
        reasoning_effort_options=catalog.reasoning_effort_options
        or _CLAUDE_FALLBACK_REASONING_EFFORT_OPTIONS,
        default_model_id=catalog.default_model_id,
        # Claude's handshake names no default effort — its entries say which efforts a
        # model takes and nothing about where it starts — so there is none to report.
        default_reasoning_effort=None,
    )


# --- codex -------------------------------------------------------------------------------

# `codex login status` reads the stored credentials. Verified on codex-cli 0.145.0 with
# every network route blackholed: same answer, in a thirtieth of a second.
_CODEX_IDENTITY_ARGUMENTS: Final = ("login", "status")

# Where codex's own installer puts the release it manages, which is what `codex update`
# updates. Matching this is what makes the update button real on such an install.
_CODEX_NATIVE_PATH_MARKER: Final = "/.codex/packages/standalone/"


def _read_codex_identity(request: _IdentityRequest) -> BackendIdentity:
    """Who codex is signed in as, and whether that sign-in can be believed.

    ``codex login status`` answers a narrower question than it looks like it answers: it
    reads the saved file and says a login is there. It never asks the server, so it said
    "Logged in using ChatGPT" for twelve days while every turn failed. This reader stops
    treating it as proof and adds the two things that can be known for free.

    The saved login states when its access token expires. An expired one is routine —
    codex refreshes on demand — so expiry alone is not a fault. What it does mean is that
    the next thing codex does depends on a refresh nobody has tried, and Panels cannot
    confirm that without spending one. That is ``unknown``: no answer this can stand
    behind, which is what the status has always meant.

    A turn that already failed has spent a refresh, and if the server refused the saved
    login, that is an answer. It arrives scoped to the credential it was about, so it is
    read only while the file still holds that same credential.
    """
    outcome = request.outcome
    reported = outcome.standard_output.strip() or outcome.standard_error.strip()
    cli_line = reported.splitlines()[0] if reported else None
    if not outcome.succeeded or "not logged in" in reported.lower():
        return BackendIdentity(status=BackendIdentityStatus.unauthenticated)
    standing = request.codex_login
    if standing is None:
        if not reported:
            return BackendIdentity(status=BackendIdentityStatus.unknown)
        return BackendIdentity(status=BackendIdentityStatus.authenticated, account_label=cli_line)
    stored = standing.stored
    label = stored.account_label or cli_line
    if not stored.present:
        return BackendIdentity(status=BackendIdentityStatus.unauthenticated)
    if standing.rejected:
        return BackendIdentity(
            status=BackendIdentityStatus.unauthenticated,
            account_label=label,
            detail="This saved login was rejected the last time codex used it.",
        )
    if not stored.readable:
        return BackendIdentity(
            status=BackendIdentityStatus.unknown,
            account_label=label,
            detail="Panels could not read the saved codex login, so it cannot say more.",
        )
    if stored.access_token_has_expired(now=request.now):
        return BackendIdentity(
            status=BackendIdentityStatus.unknown,
            account_label=label,
            detail=(
                "This saved login expired and codex has not refreshed it yet. Panels "
                "cannot confirm it works until a turn runs."
            ),
        )
    if not reported:
        return BackendIdentity(status=BackendIdentityStatus.unknown, account_label=label)
    return BackendIdentity(status=BackendIdentityStatus.authenticated, account_label=label)


async def _codex_catalog(request: _CatalogRequest) -> _CatalogAnswer:
    """Codex advertises both of these itself, per model, and only over its app-server.

    The pinned protocol has no fixed list to read: ``ReasoningEffort`` is any non-empty
    string the model says it takes, and each model in ``model/list`` carries its own
    supported efforts. So there is nothing to hard-code here — the answer is asked of the
    codex on this machine, which costs one short-lived child and reaches no agent API.

    The efforts are the union across the models, because a picker shows one list. A model
    that does not take the effort it is asked for is codex's own business to refuse.
    """
    try:
        catalog = await request.codex_model_catalog_probe(request.executable_path)
    except CodexModelCatalogUnavailable:
        return _CatalogAnswer(
            diagnoses=(
                "Codex is installed but did not answer when asked what it can run, so no "
                "models are listed. Check that `codex app-server` starts from a terminal.",
            )
        )
    return _CatalogAnswer(
        complete=catalog.complete,
        models=tuple(
            BackendModel(
                model_id=model.model_id,
                display_name=model.display_name,
                reasoning_effort_options=model.reasoning_effort_options,
            )
            for model in catalog.models if not getattr(model, "hidden", False)
        ),
        reasoning_effort_options=catalog.reasoning_effort_options,
        default_model_id=catalog.default_model_id,
        default_reasoning_effort=catalog.default_reasoning_effort,
    )


# --- hermes ------------------------------------------------------------------------------

_HERMES_MODEL_CATALOG_PROBE = (
    Path(__file__).resolve().parent / "backends" / "hermes_model_catalog.py"
)


async def _hermes_catalog(request: _CatalogRequest) -> _CatalogAnswer:
    """Hermes' own model catalog, read out of the hermes install without starting a session.

    Reasoning effort is deliberately empty: hermes exposes no such setting, and a hermes
    conversation refuses one. The snapshot is where a picker learns not to offer it.

    The catalog does not depend on the installed version: it is read out of the install
    itself, so whatever is there is the answer.
    """
    from planner.environments.hermes_home import (
        hermes_src_root,
        resolve_hermes_python,
        resolve_planner_home,
    )

    hermes_python = resolve_hermes_python()
    source_root = hermes_src_root(hermes_python)
    command = [str(hermes_python), str(_HERMES_MODEL_CATALOG_PROBE)]
    if request.refresh:
        command.append("--refresh")
    outcome = await request.environment.run(
        tuple(command),
        timeout_seconds=MODEL_CATALOG_PROBE_TIMEOUT_SECONDS,
        environment_overrides={
            "HERMES_HOME": str(resolve_planner_home()),
            "HERMES_PYTHON_SRC_ROOT": str(source_root),
            "PYTHONIOENCODING": "utf-8",
        },
    )
    if not outcome.succeeded:
        return _CatalogAnswer(
            diagnoses=(
                "Hermes' model catalog could not be read from its install, so no models "
                "are listed. Check that hermes runs from a terminal.",
            )
        )
    try:
        payload = json.loads(outcome.standard_output)
    except ValueError:
        payload = None
    inventory = _parse_hermes_inventory(payload)
    if inventory is None:
        return _CatalogAnswer(
            diagnoses=(
                "Hermes' model inventory returned an invalid answer, so no models are listed.",
            )
        )
    models, configured_default_model_id, status = inventory
    default_model_id = configured_default_model_id if status == "runnable" else None
    diagnoses: tuple[str, ...]
    if status == "not_configured":
        diagnoses = (
            "Hermes is installed but has no default provider and model configured. "
            "Run `hermes model` in a terminal.",
        )
    elif status == "default_unavailable":
        diagnoses = (
            "Hermes' configured default does not have usable credentials or is not in "
            "its configured model inventory. Run `hermes model` in a terminal.",
        )
    elif status == "runnable":
        diagnoses = ()
    else:  # The parser above owns the closed status vocabulary.
        raise AssertionError(f"unreachable Hermes inventory status: {status}")
    return _CatalogAnswer(
        models=models,
        default_model_id=default_model_id,
        diagnoses=diagnoses,
    )


def _parse_hermes_inventory(
    payload: object,
) -> tuple[tuple[BackendModel, ...], str | None, str] | None:
    """Validate the inventory door's complete answer before trusting any of it."""
    if not isinstance(payload, dict):
        return None
    if type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
        return None
    if "status" not in payload or "defaultModelId" not in payload or "providers" not in payload:
        return None

    status = payload["status"]
    if not isinstance(status, str) or status not in {
        "not_configured",
        "default_unavailable",
        "runnable",
    }:
        return None
    raw_default_model_id = payload["defaultModelId"]
    if raw_default_model_id is not None and _optional_text(raw_default_model_id) is None:
        return None
    default_model_id = _optional_text(raw_default_model_id)

    providers = payload["providers"]
    if not isinstance(providers, list):
        return None
    models: list[BackendModel] = []
    seen_provider_ids: set[str] = set()
    seen_model_ids: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            return None
        if not {"id", "displayName", "models"} <= provider.keys():
            return None
        provider_id = _optional_text(provider["id"])
        provider_name = _optional_text(provider["displayName"])
        provider_models = provider["models"]
        if (
            provider_id is None
            or provider_name is None
            or provider_id in seen_provider_ids
            or not isinstance(provider_models, list)
            or not provider_models
        ):
            return None
        seen_provider_ids.add(provider_id)
        provider_model_prefix = f"{provider_id}:"
        for entry in provider_models:
            if not isinstance(entry, dict):
                return None
            if not {"id", "displayName", "detail"} <= entry.keys():
                return None
            model_id = _optional_text(entry["id"])
            display_name = _optional_text(entry["displayName"])
            detail = _optional_text(entry["detail"])
            if (
                model_id is None
                or display_name is None
                or detail is None
                or model_id in seen_model_ids
                or not model_id.startswith(provider_model_prefix)
                or model_id == provider_model_prefix
            ):
                return None
            seen_model_ids.add(model_id)
            models.append(
                BackendModel(
                    model_id=model_id,
                    display_name=display_name,
                    detail=detail,
                )
            )

    if status == "not_configured":
        if default_model_id is not None:
            return None
    elif status == "default_unavailable":
        if default_model_id is None or default_model_id in seen_model_ids:
            return None
    elif default_model_id is None or default_model_id not in seen_model_ids:
        return None
    return tuple(models), default_model_id, status


_BACKEND_PROBE_RECIPES: Final[Mapping[ConversationBackendKey, _BackendProbeRecipe]] = {
    ConversationBackendKey.claude: _BackendProbeRecipe(
        executable_name="claude",
        missing_binary_diagnosis="`claude` is not installed or not on PATH.",
        identity_arguments=_CLAUDE_IDENTITY_ARGUMENTS,
        read_identity=_read_claude_identity,
        login_command="claude auth login",
        registry_package_name="@anthropic-ai/claude-code",
        homebrew_formula="claude-code",
        native_path_marker=None,
        native_update_command=None,
        manual_only_detail=(
            "Panels only updates `claude` from the configured vps-owned user-local npm "
            "prefix; update this installation manually."
        ),
        read_catalog=_claude_catalog,
    ),
    ConversationBackendKey.codex: _BackendProbeRecipe(
        executable_name="codex",
        missing_binary_diagnosis="`codex` is not installed or not on PATH.",
        identity_arguments=_CODEX_IDENTITY_ARGUMENTS,
        read_identity=_read_codex_identity,
        login_command="codex login",
        registry_package_name="@openai/codex",
        homebrew_formula="codex",
        native_path_marker=_CODEX_NATIVE_PATH_MARKER,
        native_update_command=("codex", "update"),
        manual_only_detail=(
            "Panels only updates `codex` from the configured vps-owned user-local npm "
            "prefix; update this installation manually."
        ),
        read_catalog=_codex_catalog,
    ),
    ConversationBackendKey.hermes: _BackendProbeRecipe(
        executable_name="hermes",
        missing_binary_diagnosis=(
            "Hermes is not installed at the configured PLAN_HERMES_PYTHON environment."
        ),
        identity_arguments=None,
        read_identity=None,
        login_command=None,
        registry_package_name=None,
        homebrew_formula=None,
        native_path_marker=None,
        native_update_command=None,
        manual_only_detail=(
            "Panels cannot confirm this Hermes installation is managed by Hermes' native "
            "updater, so it offers no update here."
        ),
        read_catalog=_hermes_catalog,
    ),
}


# --- probing ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CodexLoginStanding:
    """The saved codex login as it stands right now, and what a turn made of it.

    Read in one place and passed in, so that one probe cannot see one state and the check
    that decides whether a kept card is still true see another.
    """

    stored: StoredCodexLogin
    rejected: bool

    @property
    def generation(self) -> CodexLoginGeneration | None:
        return self.stored.generation

    def everything_a_card_reads(self, *, now: datetime) -> tuple[object, ...]:
        """Every part of this standing a card's wording depends on, at one instant.

        Expiry is in here and not only the generation, because a token expiring is the one
        change that moves a card from signed-in to unconfirmed while the file itself sits
        still. A kept card that watched only the generation would go on saying signed in
        for as long as the process lived, which is a smaller version of the fault this
        whole ticket is about.

        ``now`` is taken rather than read, because the answer is about an instant. The kept
        card's reading is the instant it was probed at: computing both sides at the moment
        of comparison would make an expired token equal to itself, which is exactly the
        reading a stale card needs to be caught by.
        """
        return (
            self.generation,
            self.rejected,
            self.stored.present,
            self.stored.readable,
            self.stored.access_token_has_expired(now=now),
        )


def read_codex_login_standing(
    remembered_rejection: CodexLoginRejectionMemory | None,
) -> CodexLoginStanding:
    stored = read_stored_codex_login()
    rejected = remembered_rejection is not None and remembered_rejection.applies_to(
        stored.generation
    )
    return CodexLoginStanding(stored=stored, rejected=rejected)


def read_backend_identity_status(
    backend_key: ConversationBackendKey,
    outcome: CommandOutcome,
    *,
    codex_login: CodexLoginStanding | None = None,
    now: datetime | None = None,
) -> BackendIdentityStatus:
    """Project the existing local identity parser without leaking provider output."""
    reader = _BACKEND_PROBE_RECIPES[backend_key].read_identity
    if reader is None:
        return BackendIdentityStatus.unknown
    return reader(_IdentityRequest(
        outcome=outcome,
        codex_login=codex_login,
        now=now if now is not None else datetime.now(UTC),
    )).status


async def probe_backend(
    backend_key: ConversationBackendKey,
    environment: BackendProbeEnvironment,
    *,
    codex_model_catalog_probe: CodexModelCatalogProbe = probe_codex_model_catalog,
    claude_model_catalog_probe: ClaudeModelCatalogProbe = probe_claude_model_catalog,
    refresh: bool = False,
    discover_update_advisory: bool = True,
    codex_login: CodexLoginStanding | None = None,
    now: datetime | None = None,
) -> BackendSnapshot:
    """Everything this machine can say about one backend, without touching an agent API.

    ``codex_login`` is the saved codex login as somebody already read it. Without it this
    probe reads no files and reports what the CLIs say, which is what a test driving a
    scripted machine wants.
    """
    recipe = _BACKEND_PROBE_RECIPES[backend_key]
    now = now if now is not None else datetime.now(UTC)
    if backend_key is ConversationBackendKey.hermes:
        # Hermes launch is derived from PLAN_HERMES_PYTHON, so its snapshot must describe
        # that exact install rather than an unrelated executable that happens to be on
        # PATH. Codex and Claude retain their PATH behavior.
        executable_path = environment.configured_executable_path(recipe.executable_name)
    else:
        executable_path = environment.executable_path(recipe.executable_name)
    if executable_path is None:
        return BackendSnapshot(
            backend_key=backend_key,
            installed=False,
            executable_path=None,
            version=None,
            identity=None,
            available_models=(),
            reasoning_effort_options=(),
            default_model_id=None,
            default_reasoning_effort=None,
            update_advisory=None,
            diagnoses=(recipe.missing_binary_diagnosis,),
        )

    diagnoses: list[str] = []
    version_outcome = await environment.run(
        (executable_path, *VERSION_ARGUMENTS), timeout_seconds=VERSION_PROBE_TIMEOUT_SECONDS
    )
    version = parse_version(version_outcome.standard_output + version_outcome.standard_error)
    if version is None:
        diagnoses.append(
            f"`{recipe.executable_name} --version` did not report a version, so Panels "
            "cannot tell which one is installed."
        )

    identity = await _probe_identity(recipe, executable_path, environment, codex_login, now)
    if identity is not None and identity.status is BackendIdentityStatus.unauthenticated:
        diagnoses.append(
            f"`{recipe.executable_name}` is not signed in."
            + (f" Run `{recipe.login_command}` in a terminal." if recipe.login_command else "")
        )
    if identity is not None and identity.status is BackendIdentityStatus.unknown:
        # A reader that says why it cannot stand behind an answer has said the useful
        # thing already. The fallback is for a probe that came back with nothing at all,
        # where not knowing who the account is really is the whole of it.
        diagnoses.append(
            identity.detail
            or f"Panels could not read who `{recipe.executable_name}` is signed in as."
        )

    catalog = await recipe.read_catalog(
        _CatalogRequest(
            environment=environment,
            version=version,
            executable_path=executable_path,
            refresh=refresh,
            codex_model_catalog_probe=codex_model_catalog_probe,
            claude_model_catalog_probe=claude_model_catalog_probe,
        )
    )
    diagnoses.extend(catalog.diagnoses)

    advisory = (
        await _update_advisory(recipe, executable_path, version, environment)
        if discover_update_advisory
        else None
    )
    return BackendSnapshot(
        backend_key=backend_key,
        installed=True,
        executable_path=executable_path,
        version=version,
        identity=identity,
        available_models=_model_metadata(backend_key, catalog.models),
        reasoning_effort_options=catalog.reasoning_effort_options,
        default_model_id=catalog.default_model_id,
        default_reasoning_effort=catalog.default_reasoning_effort,
        update_advisory=advisory,
        diagnoses=tuple(diagnoses),
        models_complete=catalog.complete,
    )


def _model_metadata(
    backend_key: ConversationBackendKey, models: tuple[BackendModel, ...]
) -> tuple[BackendModel, ...]:
    from planner.conversation.model_resolution.logic.variants import recognize_variant

    entries: list[BackendModel] = []
    for model in models:
        recognized = recognize_variant(backend_key, model.model_id)
        variant = None if recognized is None else recognized.name
        entries.append(
            replace(
                model,
                selected_variant=variant,
                resolved_model_id=model.resolved_model_id or model.model_id,
            )
        )
    return tuple(entries)


async def _probe_identity(
    recipe: _BackendProbeRecipe,
    executable_path: str,
    environment: BackendProbeEnvironment,
    codex_login: CodexLoginStanding | None,
    now: datetime,
) -> BackendIdentity | None:
    """Who the CLI is signed in as, when the CLI has a cheap way of saying.

    A backend with no account to be signed in to gets no identity at all rather than an
    empty one, because "hermes has no login" and "we could not read claude's login" are
    different things and a card that showed them the same way would be lying about one.
    """
    if recipe.identity_arguments is None or recipe.read_identity is None:
        return None
    outcome = await environment.run(
        (executable_path, *recipe.identity_arguments),
        timeout_seconds=IDENTITY_PROBE_TIMEOUT_SECONDS,
    )
    request = _IdentityRequest(outcome=outcome, codex_login=codex_login, now=now)
    return replace(recipe.read_identity(request), login_command=recipe.login_command)


async def _update_advisory(
    recipe: _BackendProbeRecipe,
    executable_path: str,
    version: str | None,
    environment: BackendProbeEnvironment,
) -> BackendUpdateAdvisory | None:
    if recipe.registry_package_name is None and recipe.native_update_command is None:
        return None
    resolved_install = _resolve_install(recipe, executable_path, environment)
    install_method = resolved_install.install_method
    update_command = _update_command(recipe, install_method, resolved_install.package_prefix)
    latest_version: str | None = None
    if recipe.registry_package_name is not None and install_method in _REGISTRY_INSTALL_METHODS:
        latest_version = await environment.latest_released_version(
            recipe.registry_package_name, timeout_seconds=REGISTRY_LOOKUP_TIMEOUT_SECONDS
        )
    update_available = (
        latest_version is not None
        and version is not None
        and _version_is_newer(latest_version, version)
    )
    return BackendUpdateAdvisory(
        install_method=install_method,
        update_command=update_command,
        latest_version=latest_version,
        update_available=update_available,
        detail=_advisory_detail(
            recipe, install_method, update_command, latest_version, update_available
        ),
    )


_REGISTRY_INSTALL_METHODS: Final = frozenset(
    {
        BackendInstallMethod.npm_global,
        BackendInstallMethod.bun_global,
        BackendInstallMethod.pnpm_global,
    }
)


def _update_command(
    recipe: _BackendProbeRecipe,
    install_method: BackendInstallMethod,
    package_prefix: str | None = None,
) -> tuple[str, ...] | None:
    package_name = recipe.registry_package_name
    match install_method:
        case BackendInstallMethod.native:
            return recipe.native_update_command
        case BackendInstallMethod.npm_global if package_name is not None:
            if package_prefix is None:
                return None
            return (
                "npm",
                "install",
                "--global",
                "--prefix",
                package_prefix,
                f"{package_name}@latest",
            )
        case _:
            return None


def _advisory_detail(
    recipe: _BackendProbeRecipe,
    install_method: BackendInstallMethod,
    update_command: tuple[str, ...] | None,
    latest_version: str | None,
    update_available: bool,
) -> str:
    if update_command is None:
        return recipe.manual_only_detail
    if update_available and latest_version is not None:
        return f"Version {latest_version} is available."
    if latest_version is not None:
        return "This is the newest published version."
    return "Panels can run this backend's own update command; there is no newer version to report."


# --- keeping the answers ------------------------------------------------------------------


class BackendSnapshotService:
    """The snapshots this process has, and the updates it can run.

    Probes are run when something asks and then kept, because they cost real subprocesses
    and the answer only changes when the machine does. Nothing is on a timer: a surface
    that wants a fresh answer asks for one, which is also what an update does to itself
    once its command has finished.
    """

    def __init__(
        self,
        environment: BackendProbeEnvironment | None = None,
        *,
        codex_model_catalog_probe: CodexModelCatalogProbe = probe_codex_model_catalog,
        claude_model_catalog_probe: ClaudeModelCatalogProbe = probe_claude_model_catalog,
        remembered_codex_login_rejection: CodexLoginRejectionMemory | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._environment = environment or SubprocessBackendProbeEnvironment()
        self._now = now or (lambda: datetime.now(UTC))
        self._codex_model_catalog_probe = codex_model_catalog_probe
        self._claude_model_catalog_probe = claude_model_catalog_probe
        self._remembered_codex_login_rejection = remembered_codex_login_rejection
        self._snapshots: dict[ConversationBackendKey | BackendSnapshotScope, BackendSnapshot] = {}
        # What the kept codex card was probed against, read at the instant it was probed.
        # A card is kept because probing costs subprocesses and the machine rarely changes
        # — but a login is the one thing here that changes without the machine changing,
        # and a card that outlives its login is the lie this is for. The reading is kept
        # alongside the card, and a card whose login no longer reads the same is probed
        # again.
        self._codex_card_was_probed_against: tuple[object, ...] | None = None
        # A probe is shared only with another reader of the same backend. Independent
        # backends use separate CLIs and catalogues, so one slow child must not hold the
        # others behind it.
        self._snapshot_locks: dict[ConversationBackendKey | BackendSnapshotScope, asyncio.Lock] = {
            backend_key: asyncio.Lock() for backend_key in ConversationBackendKey
        }
        # One lock per backend, held for a whole update rather than for a probe. Reading a
        # card is quick and shares the lock above; installing a package is slow, changes
        # the machine, and must not happen twice at once — so the two are different locks,
        # and a running update never blocks somebody looking at a different backend's card.
        self._update_locks: dict[ConversationBackendKey, asyncio.Lock] = {
            backend_key: asyncio.Lock() for backend_key in ConversationBackendKey
        }

    def clear_destination_snapshots(
        self, backend_key: ConversationBackendKey | None = None,
    ) -> None:
        """Forget suggestions when the existing runtime owner or sign-in lifecycle changes."""
        for scope in tuple(self._snapshot_locks):
            if isinstance(scope, BackendSnapshotScope) and (
                backend_key is None or scope.backend_key == backend_key
            ):
                self._snapshots.pop(scope, None)
                self._snapshot_locks.pop(scope, None)

    def retained_destination(self, scope: BackendSnapshotScope) -> BackendSnapshot | None:
        """Read already retained suggestions without observation or a freshness gate."""
        return self._snapshots.get(scope)

    async def destination_snapshot(
        self,
        scope: BackendSnapshotScope,
        *,
        observe: Callable[[], Awaitable[CatalogResult]],
        is_current: Callable[[], Awaitable[bool]],
        refresh: bool = False,
    ) -> BackendSnapshot:
        """Coalesce one scoped refresh and retain a complete list through partial failure."""
        from planner.conversation.model_resolution.contracts import (
            CatalogObservation,
            PrerequisiteStatus,
        )

        empty = BackendSnapshot(
            scope.backend_key, False, None, None, None, (), (), None, None, None, (),
        )
        if not await is_current():
            return empty
        kept = self._snapshots.get(scope)
        if kept is not None and not refresh:
            return kept
        lock = self._snapshot_locks.setdefault(scope, asyncio.Lock())
        waiting = lock.locked()
        async with lock:
            if waiting:
                return self._snapshots.get(scope, empty) if await is_current() else empty
            # Another request that held this lock already supplied this refresh.
            latest = self._snapshots.get(scope)
            if latest is not None and latest is not kept:
                return latest
            try:
                result = await observe()
            except Exception:  # noqa: BLE001 - optional suggestions retain their previous list
                return self._snapshots.get(scope, empty) if await is_current() else empty
            if not await is_current() or self._snapshot_locks.get(scope) is not lock:
                return empty
            if not isinstance(result, CatalogObservation):
                return self._snapshots.get(scope, empty)
            context = result.evidence.context
            if (
                context.backend_key != scope.backend_key
                or context.destination.value != scope.destination
                or context.workspace != scope.workspace
                or context.executor_identity != scope.executor_identity
                or (scope.account_generation is not None
                    and context.account_generation != scope.account_generation)
            ):
                return empty
            latest = self._snapshots.get(scope)
            if (not result.complete and latest is not None and latest.models_complete
                    and latest.catalog_account_generation == context.account_generation):
                return latest
            models = _model_metadata(scope.backend_key, tuple(
                BackendModel(
                    row.submitted_token,
                    display_name=row.display_name,
                    reasoning_effort_options=row.supported_reasoning_efforts,
                    resolved_model_id=row.resolved_model_id,
                )
                for row in result.rows if not row.hidden
            ))
            snapshot = BackendSnapshot(
                backend_key=scope.backend_key,
                installed=result.installation is PrerequisiteStatus.available,
                executable_path=None,
                version=context.executable_version,
                identity=BackendIdentity({
                    PrerequisiteStatus.available: BackendIdentityStatus.authenticated,
                    PrerequisiteStatus.unavailable: BackendIdentityStatus.unauthenticated,
                    PrerequisiteStatus.unknown: BackendIdentityStatus.unknown,
                }[result.credentials]),
                available_models=models,
                reasoning_effort_options=tuple(dict.fromkeys(
                    effort for model in models for effort in model.reasoning_effort_options or ()
                )),
                default_model_id=None,
                default_reasoning_effort=None,
                update_advisory=None,
                diagnoses=(),
                models_complete=result.complete,
                catalog_account_generation=context.account_generation,
            )
            self._snapshots[scope] = snapshot
            return snapshot

    async def snapshots(self, *, refresh: bool = False) -> tuple[BackendSnapshot, ...]:
        return tuple(
            await asyncio.gather(
                *(
                    self.snapshot(backend_key, refresh=refresh)
                    for backend_key in ConversationBackendKey
                )
            )
        )

    async def snapshot(
        self,
        backend_key: ConversationBackendKey,
        *,
        refresh: bool = False,
        discover_update_advisory: bool = True,
    ) -> BackendSnapshot:
        # A forced advisory refresh keeps the last complete card readable. An ordinary
        # reader never waits behind remote work when this process already has an answer.
        stale = self._codex_login_has_moved(backend_key)
        if not refresh and not stale:
            kept = self._snapshots.get(backend_key)
            if kept is not None:
                return kept
        waiting = self._snapshot_locks[backend_key].locked()
        async with self._snapshot_locks[backend_key]:
            if waiting and not self._codex_login_has_moved(backend_key):
                shared = self._snapshots.get(backend_key)
                if shared is not None:
                    return shared
            kept = None if refresh or stale else self._snapshots.get(backend_key)
            if kept is not None:
                return kept
            reading_at = self._now()
            codex_login = (
                read_codex_login_standing(self._remembered_codex_login_rejection)
                if backend_key is ConversationBackendKey.codex
                else None
            )
            probed = await probe_backend(
                backend_key,
                self._environment,
                codex_model_catalog_probe=self._codex_model_catalog_probe,
                claude_model_catalog_probe=self._claude_model_catalog_probe,
                refresh=refresh,
                # Ordinary reads serve the catalogue without waiting for remote update
                # discovery. An explicit refresh replaces this cached answer with the
                # complete advisory, which update_backend also requires before it acts.
                discover_update_advisory=refresh and discover_update_advisory,
                codex_login=codex_login,
                now=reading_at,
            )
            if codex_login is not None:
                latest_login = read_codex_login_standing(self._remembered_codex_login_rejection)
                if (latest_login.everything_a_card_reads(now=self._now())
                        != codex_login.everything_a_card_reads(now=reading_at)):
                    return replace(probed, identity=None, available_models=(),
                                   reasoning_effort_options=(), default_model_id=None,
                                   default_reasoning_effort=None, models_complete=False)
            if stale and not refresh and probed.update_advisory is None:
                # This probe happened because the login moved, not because anybody asked
                # for update discovery, so it carries no advisory. Dropping the one the
                # kept card had would make the Update button vanish for a reason that has
                # nothing to do with updates.
                kept_advisory = self._snapshots.get(backend_key)
                if kept_advisory is not None and kept_advisory.update_advisory is not None:
                    probed = replace(probed, update_advisory=kept_advisory.update_advisory)
            previous = self._snapshots.get(backend_key)
            if (not stale and previous is not None
                    and previous.models_complete and not probed.models_complete):
                probed = replace(
                    probed, available_models=previous.available_models,
                    reasoning_effort_options=previous.reasoning_effort_options,
                    default_model_id=previous.default_model_id,
                    default_reasoning_effort=previous.default_reasoning_effort,
                    models_complete=True,
                )
            self._snapshots[backend_key] = probed
            if backend_key is ConversationBackendKey.codex and codex_login is not None:
                self._codex_card_was_probed_against = codex_login.everything_a_card_reads(
                    now=reading_at
                )
            return probed

    def _codex_login_has_moved(self, backend_key: ConversationBackendKey) -> bool:
        """Whether the kept codex card was probed against a login that no longer stands.

        This is a file read and a comparison, so every reader can afford it, and it is what
        makes a sign-in or a rejection show up on the card without anyone pressing refresh.
        """
        if backend_key is not ConversationBackendKey.codex:
            return False
        at_probe = self._codex_card_was_probed_against
        if at_probe is None:
            return False
        standing = read_codex_login_standing(self._remembered_codex_login_rejection)
        return standing.everything_a_card_reads(now=self._now()) != at_probe

    async def update_backend(self, backend_key: ConversationBackendKey) -> BackendUpdateResult:
        """Run the update this backend's install implies, then look again.

        Looking again is the whole point: a command can exit cleanly and change nothing,
        and only the version says which of those happened.

        The whole of it — looking, running, looking again — happens under this backend's
        update lock. Two people pressing Update are two package-manager runs against the
        same install, and package managers do not survive that. The second one waits, and
        then finds an install the first has already moved, so it reports what actually
        happened to it: nothing changed.
        """
        async with self._update_locks[backend_key]:
            return await self._update_backend(backend_key)

    async def _update_backend(self, backend_key: ConversationBackendKey) -> BackendUpdateResult:
        before = await self.snapshot(backend_key, refresh=True)
        advisory = before.update_advisory
        if not before.installed:
            return BackendUpdateResult(
                outcome=BackendUpdateOutcome.failed,
                detail=(
                    before.diagnoses[0]
                    if before.diagnoses
                    else "There is no update Panels can run for this backend."
                ),
                output_tail="",
            )
        if advisory is None:
            return BackendUpdateResult(
                outcome=BackendUpdateOutcome.failed,
                detail="There is no update Panels can run for this backend.",
                output_tail="",
            )
        if advisory.update_command is None:
            return BackendUpdateResult(
                outcome=BackendUpdateOutcome.failed,
                detail=advisory.detail,
                output_tail="",
            )
        outcome = await self._environment.run(
            advisory.update_command, timeout_seconds=UPDATE_COMMAND_TIMEOUT_SECONDS
        )
        output_tail = outcome.output_tail(UPDATE_OUTPUT_TAIL_MAXIMUM_CHARACTERS)
        # Refresh even after a failed command. An updater may have changed part of an
        # installation before failing, and the card must describe what remains.
        after = await self.snapshot(backend_key, refresh=True)
        if not outcome.succeeded:
            return BackendUpdateResult(
                outcome=BackendUpdateOutcome.failed,
                detail=f"The update command exited with code {outcome.exit_code}.",
                output_tail=output_tail,
            )
        if after.version is not None and after.version != before.version:
            return BackendUpdateResult(
                outcome=BackendUpdateOutcome.succeeded,
                detail=f"Updated to {after.version}.",
                output_tail=output_tail,
            )
        return BackendUpdateResult(
            outcome=BackendUpdateOutcome.unchanged,
            detail=(
                "The update command finished, but the installed version is still "
                f"{before.version if before.version is not None else 'unknown'}."
            ),
            output_tail=output_tail,
        )


def _version_at_least(version: str, minimum: tuple[int, ...]) -> bool:
    try:
        parts = tuple(int(part) for part in version.split("."))
    except ValueError:
        return False
    return parts >= minimum


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
