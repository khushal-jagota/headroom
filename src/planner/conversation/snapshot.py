"""What each agent backend on this machine is, and whether it needs anything from you.

A backend snapshot is a product object, not a health check hidden in a log: is the CLI
there, which version, who is it logged in as, what can it be run as, and is there a newer
one. Every answer here is either something the machine told us or an honest absence. The
snapshot never guesses, and it never reaches an agent API — identity comes from the CLI's
own local credential read, which costs a subprocess and nothing else.

Three rules hold the module together.

**One classifier for every backend.** Where a binary lives says how it was installed, and
that says how to update it. The same function classifies all three; what differs between
backends is data — a package name, a native marker — never a code path of its own.

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
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

import httpx

from planner.conversation.backend_lifecycle import (
    BackendLifecycleCoordinator,
    BackendMaintenanceLease,
)
from planner.conversation.backends.claude_model_catalog import (
    ClaudeModelCatalog,
    ClaudeModelCatalogUnavailable,
    probe_claude_model_catalog,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModelCatalog,
    CodexModelCatalogUnavailable,
    probe_codex_model_catalog,
)
from planner.conversation.contracts import ConversationBackendKey

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
_HERMES_UPDATE_AVAILABLE_MARKER: Final = "update available"
_HERMES_CURRENT_MARKER: Final = "already up to date"
_HERMES_UNSUPPORTED_MARKERS: Final = (
    "this hermes installation is managed by",
    "doesn't apply inside the docker container",
    "not a git repository",
    "update hermes through the nix source that installed it",
    "unrecognized arguments: --check",
    "invalid choice: 'update'",
    "no such command",
)


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
    Empty means the model said nothing about it, and the backend's own list applies.
    """

    model_id: str
    display_name: str | None = None
    detail: str | None = None
    reasoning_effort_options: tuple[str, ...] = ()


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

    def real_path(self, path: str) -> str: ...

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
        return shutil.which(executable_name)

    def real_path(self, path: str) -> str:
        return os.path.realpath(path)

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        environment = dict(os.environ)
        if environment_overrides is not None:
            environment.update(environment_overrides)
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                # Its own process group, so that everything this command starts can be
                # stopped with it. An installer is a script that runs other programs, and
                # killing only the script it was launched as would leave those still
                # writing to this machine after Panels had reported the update failed.
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            return CommandOutcome(exit_code=-1, standard_output="", standard_error=str(error))
        try:
            standard_output, standard_error = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            _end_the_whole_process_group(process)
            await process.wait()
            return CommandOutcome(
                exit_code=-1,
                standard_output="",
                standard_error=f"{argv[0]} did not answer within {timeout_seconds:g}s",
            )
        except asyncio.CancelledError:
            # An HTTP caller going away must not leave an installer working after its
            # maintenance lease is released. End the whole group before cancellation
            # leaves this boundary, just as a timeout does.
            _end_the_whole_process_group(process)
            await process.wait()
            raise
        return CommandOutcome(
            exit_code=process.returncode if process.returncode is not None else -1,
            standard_output=standard_output.decode("utf-8", errors="replace"),
            standard_error=standard_error.decode("utf-8", errors="replace"),
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
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
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
class _CatalogRequest:
    environment: BackendProbeEnvironment
    version: str | None
    executable_path: str
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


@dataclass(frozen=True, slots=True)
class _BackendProbeRecipe:
    """The per-backend facts the one shared probe runs on."""

    executable_name: str
    missing_binary_diagnosis: str
    identity_arguments: tuple[str, ...] | None
    read_identity: Callable[[CommandOutcome], BackendIdentity] | None
    login_command: str | None
    registry_package_name: str | None
    homebrew_formula: str | None
    native_path_marker: str | None
    native_update_command: tuple[str, ...] | None
    native_update_check_arguments: tuple[str, ...] | None
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


def _read_claude_identity(outcome: CommandOutcome) -> BackendIdentity:
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
        models=tuple(
            BackendModel(
                model_id=model.model_id,
                display_name=model.display_name,
                detail=f"{model.model_id} → {model.resolved_model_id}",
                reasoning_effort_options=model.reasoning_effort_options,
            )
            for model in catalog.models
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


def _read_codex_identity(outcome: CommandOutcome) -> BackendIdentity:
    reported = outcome.standard_output.strip() or outcome.standard_error.strip()
    if not outcome.succeeded:
        return BackendIdentity(status=BackendIdentityStatus.unauthenticated)
    if not reported:
        return BackendIdentity(status=BackendIdentityStatus.unknown)
    if "not logged in" in reported.lower():
        return BackendIdentity(status=BackendIdentityStatus.unauthenticated)
    return BackendIdentity(
        status=BackendIdentityStatus.authenticated, account_label=reported.splitlines()[0]
    )


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
        models=tuple(
            BackendModel(
                model_id=model.model_id,
                display_name=model.display_name,
                reasoning_effort_options=model.reasoning_effort_options,
            )
            for model in catalog.models
        ),
        reasoning_effort_options=catalog.reasoning_effort_options,
        default_model_id=catalog.default_model_id,
        default_reasoning_effort=catalog.default_reasoning_effort,
    )


# --- hermes ------------------------------------------------------------------------------

_HERMES_MODEL_CATALOG_PROBE = (
    Path(__file__).resolve().parents[1] / "conversation" / "hermes_model_catalog_probe.py"
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
    outcome = await request.environment.run(
        (str(hermes_python), str(_HERMES_MODEL_CATALOG_PROBE), str(source_root)),
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
    return _CatalogAnswer(
        models=_hermes_models(outcome.standard_output),
        # Hermes calls it the native model: the one its own configuration runs on.
        default_model_id=_hermes_native_model(outcome.standard_output),
    )


def _hermes_native_model(probe_output: str) -> str | None:
    try:
        native = json.loads(probe_output)["nativeModel"]
    except (ValueError, KeyError, TypeError):
        return None
    return _optional_text(native)


def _hermes_models(probe_output: str) -> tuple[BackendModel, ...]:
    try:
        payload = json.loads(probe_output)
        listed = payload["models"]
    except (ValueError, KeyError, TypeError):
        return ()
    if not isinstance(listed, list):
        return ()
    models: list[BackendModel] = []
    for entry in listed:
        if not isinstance(entry, dict):
            continue
        model_id = _optional_text(entry.get("model"))
        if model_id is None:
            continue
        models.append(
            BackendModel(
                model_id=model_id, display_name=_optional_text(entry.get("description"))
            )
        )
    return tuple(models)


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
        native_update_check_arguments=None,
        manual_only_detail=(
            "Panels cannot tell how `claude` was installed, so it offers no update here."
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
        native_update_check_arguments=None,
        manual_only_detail=(
            "Panels cannot tell how `codex` was installed, so it offers no update here."
        ),
        read_catalog=_codex_catalog,
    ),
    ConversationBackendKey.hermes: _BackendProbeRecipe(
        executable_name="hermes",
        missing_binary_diagnosis="`hermes` is not installed or not on PATH.",
        identity_arguments=None,
        read_identity=None,
        login_command=None,
        registry_package_name=None,
        homebrew_formula=None,
        native_path_marker=None,
        native_update_command=("hermes", "update", "--yes"),
        native_update_check_arguments=("update", "--check"),
        manual_only_detail=(
            "Panels cannot confirm this Hermes installation is managed by Hermes' native "
            "updater, so it offers no update here."
        ),
        read_catalog=_hermes_catalog,
    ),
}


# --- probing ------------------------------------------------------------------------------


async def probe_backend(
    backend_key: ConversationBackendKey,
    environment: BackendProbeEnvironment,
    *,
    codex_model_catalog_probe: CodexModelCatalogProbe = probe_codex_model_catalog,
    claude_model_catalog_probe: ClaudeModelCatalogProbe = probe_claude_model_catalog,
) -> BackendSnapshot:
    """Everything this machine can say about one backend, without touching an agent API."""
    recipe = _BACKEND_PROBE_RECIPES[backend_key]
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

    identity = await _probe_identity(recipe, executable_path, environment)
    if identity is not None and identity.status is BackendIdentityStatus.unauthenticated:
        diagnoses.append(
            f"`{recipe.executable_name}` is not signed in."
            + (f" Run `{recipe.login_command}` in a terminal." if recipe.login_command else "")
        )
    if identity is not None and identity.status is BackendIdentityStatus.unknown:
        diagnoses.append(
            f"Panels could not read who `{recipe.executable_name}` is signed in as."
        )

    catalog = await recipe.read_catalog(
        _CatalogRequest(
            environment=environment,
            version=version,
            executable_path=executable_path,
            codex_model_catalog_probe=codex_model_catalog_probe,
            claude_model_catalog_probe=claude_model_catalog_probe,
        )
    )
    diagnoses.extend(catalog.diagnoses)

    advisory = await _update_advisory(recipe, executable_path, version, environment)
    return BackendSnapshot(
        backend_key=backend_key,
        installed=True,
        executable_path=executable_path,
        version=version,
        identity=identity,
        available_models=catalog.models,
        reasoning_effort_options=catalog.reasoning_effort_options,
        default_model_id=catalog.default_model_id,
        default_reasoning_effort=catalog.default_reasoning_effort,
        update_advisory=advisory,
        diagnoses=tuple(diagnoses),
    )


async def _probe_identity(
    recipe: _BackendProbeRecipe, executable_path: str, environment: BackendProbeEnvironment
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
    return replace(recipe.read_identity(outcome), login_command=recipe.login_command)


async def _update_advisory(
    recipe: _BackendProbeRecipe,
    executable_path: str,
    version: str | None,
    environment: BackendProbeEnvironment,
) -> BackendUpdateAdvisory:
    install_method = classify_install_method(
        (executable_path, environment.real_path(executable_path)),
        native_path_marker=recipe.native_path_marker,
    )
    update_command = _update_command(recipe, install_method)
    latest_version: str | None = None
    hermes_detail: str | None = None
    hermes_update_available = False
    if recipe.native_update_check_arguments is not None:
        check = await environment.run(
            (executable_path, *recipe.native_update_check_arguments),
            timeout_seconds=VERSION_PROBE_TIMEOUT_SECONDS,
        )
        supported, hermes_update_available, hermes_detail = _read_hermes_update_check(check)
        install_method = (
            BackendInstallMethod.native if supported else BackendInstallMethod.manual_only
        )
        native_update_command = recipe.native_update_command
        update_command = (
            (executable_path, *native_update_command[1:])
            if supported and native_update_command is not None
            else None
        )
    if recipe.registry_package_name is not None and install_method in _REGISTRY_INSTALL_METHODS:
        latest_version = await environment.latest_released_version(
            recipe.registry_package_name, timeout_seconds=REGISTRY_LOOKUP_TIMEOUT_SECONDS
        )
    update_available = hermes_update_available or (
        latest_version is not None
        and version is not None
        and _version_is_newer(latest_version, version)
    )
    return BackendUpdateAdvisory(
        install_method=install_method,
        update_command=update_command,
        latest_version=latest_version,
        update_available=update_available,
        detail=hermes_detail
        or _advisory_detail(
            recipe, install_method, update_command, latest_version, update_available
        ),
    )


def _read_hermes_update_check(
    outcome: CommandOutcome,
) -> tuple[bool, bool, str]:
    """Translate Hermes' human-readable native check without trusting exit code alone."""
    output = "\n".join(
        part for part in (outcome.standard_output, outcome.standard_error) if part
    ).strip()
    normalized = output.lower()
    if any(marker in normalized for marker in _HERMES_UNSUPPORTED_MARKERS):
        return False, False, output or "This Hermes installation cannot update itself."
    if outcome.succeeded and _HERMES_UPDATE_AVAILABLE_MARKER in normalized:
        return True, True, "A Hermes update is available."
    if outcome.succeeded and _HERMES_CURRENT_MARKER in normalized:
        return True, False, "This Hermes installation is current."
    detail = output.splitlines()[-1] if output else "The update check returned no answer."
    return (
        True,
        False,
        f"Panels could not determine whether Hermes has an update: {detail}",
    )


_REGISTRY_INSTALL_METHODS: Final = frozenset(
    {
        BackendInstallMethod.npm_global,
        BackendInstallMethod.bun_global,
        BackendInstallMethod.pnpm_global,
    }
)


def _update_command(
    recipe: _BackendProbeRecipe, install_method: BackendInstallMethod
) -> tuple[str, ...] | None:
    package_name = recipe.registry_package_name
    match install_method:
        case BackendInstallMethod.native:
            return recipe.native_update_command
        case BackendInstallMethod.npm_global if package_name is not None:
            return ("npm", "install", "-g", f"{package_name}@latest")
        case BackendInstallMethod.bun_global if package_name is not None:
            return ("bun", "install", "-g", f"{package_name}@latest")
        case BackendInstallMethod.pnpm_global if package_name is not None:
            return ("pnpm", "add", "-g", f"{package_name}@latest")
        case BackendInstallMethod.homebrew if recipe.homebrew_formula is not None:
            return ("brew", "upgrade", recipe.homebrew_formula)
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
        backend_lifecycle: BackendLifecycleCoordinator | None = None,
    ) -> None:
        self._environment = environment or SubprocessBackendProbeEnvironment()
        self._codex_model_catalog_probe = codex_model_catalog_probe
        self._claude_model_catalog_probe = claude_model_catalog_probe
        self._backend_lifecycle = backend_lifecycle
        self._snapshots: dict[ConversationBackendKey, BackendSnapshot] = {}
        self._lock = asyncio.Lock()
        # One lock per backend, held for a whole update rather than for a probe. Reading a
        # card is quick and shares the lock above; installing a package is slow, changes
        # the machine, and must not happen twice at once — so the two are different locks,
        # and a running update never blocks somebody looking at a different backend's card.
        self._update_locks: dict[ConversationBackendKey, asyncio.Lock] = {
            backend_key: asyncio.Lock() for backend_key in ConversationBackendKey
        }

    async def snapshots(self, *, refresh: bool = False) -> tuple[BackendSnapshot, ...]:
        return tuple(
            [
                await self.snapshot(backend_key, refresh=refresh)
                for backend_key in ConversationBackendKey
            ]
        )

    async def snapshot(
        self, backend_key: ConversationBackendKey, *, refresh: bool = False
    ) -> BackendSnapshot:
        async with self._lock:
            kept = None if refresh else self._snapshots.get(backend_key)
            if kept is not None:
                return kept
            probed = await probe_backend(
                backend_key,
                self._environment,
                codex_model_catalog_probe=self._codex_model_catalog_probe,
                claude_model_catalog_probe=self._claude_model_catalog_probe,
            )
            self._snapshots[backend_key] = probed
            return probed

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
        if not before.installed or advisory is None or advisory.update_command is None:
            return BackendUpdateResult(
                outcome=BackendUpdateOutcome.failed,
                detail=(
                    before.diagnoses[0]
                    if before.diagnoses
                    else "There is no update Panels can run for this backend."
                ),
                output_tail="",
            )
        maintenance_lease: BackendMaintenanceLease | None = None
        backend_lifecycle = self._backend_lifecycle
        if (
            backend_key is ConversationBackendKey.hermes
            and backend_lifecycle is not None
        ):
            maintenance_lease = await backend_lifecycle.try_begin_maintenance(backend_key)
            if maintenance_lease is None:
                return BackendUpdateResult(
                    outcome=BackendUpdateOutcome.failed,
                    detail=(
                        "Hermes cannot be updated while a Panels-owned Hermes process "
                        "is starting or running. Stop it and try again."
                    ),
                    output_tail="",
                )
        try:
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
        finally:
            if maintenance_lease is not None and backend_lifecycle is not None:
                await backend_lifecycle.end_maintenance(maintenance_lease)


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
