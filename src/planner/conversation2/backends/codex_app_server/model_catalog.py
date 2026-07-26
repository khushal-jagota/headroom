"""What codex says it can be run as, asked of the app-server and nothing else.

Codex is the one backend whose catalog cannot be written down anywhere. Its models are
whatever the account is entitled to today, and its reasoning efforts are not a fixed list
at all — the protocol calls an effort "a non-empty reasoning effort value advertised by the
model", and every model carries its own set. So the only honest answer comes from asking.

Asking costs one short-lived child and one request. No thread is started and no turn is
run, so nothing here reaches an agent API or spends anything.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from planner import __version__
from planner.conversation2.backends.codex_app_server import bindings_gen as bindings
from planner.conversation2.backends.codex_app_server.adapter import (
    CLIENT_NAME,
    CLIENT_TITLE,
    INITIALIZE_CAPABILITIES,
)
from planner.conversation2.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexAppServerError,
    child_environment,
)

# The whole probe — spawn, handshake, one request, stop — against a local child. Long
# enough for a cold start on a slow machine, short enough that a card is never left waiting.
MODEL_CATALOG_TIMEOUT_SECONDS: Final = 30.0


class CodexModelCatalogUnavailable(Exception):
    """Codex could not be asked what it runs, and why."""


@dataclass(frozen=True, slots=True)
class CodexModel:
    """One model codex offers, with the efforts that model itself advertises."""

    model_id: str
    display_name: str
    reasoning_effort_options: tuple[str, ...]
    default_reasoning_effort: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class CodexModelCatalog:
    """Everything codex offers, and the efforts a picker may show.

    ``reasoning_effort_options`` is the union across the models, kept in the order codex
    listed them, because a picker showing one list has to show something — and an effort
    that no model takes is one no turn could ever run under.
    """

    models: tuple[CodexModel, ...]
    reasoning_effort_options: tuple[str, ...]
    # The model codex runs when nobody picks one, and the effort that model starts at.
    # Codex flags both itself, per model, so neither is worked out here.
    default_model_id: str | None = None
    default_reasoning_effort: str | None = None


class _SaysNothing:
    """A message handler for a probe that asks one question and reads one answer.

    The catalog request is answered directly, so nothing codex volunteers along the way is
    anything this needs. A server request is refused by the client itself, and a child that
    ends early shows up as the request never being answered.
    """

    async def on_notification(self, method: str, notification: BaseModel) -> None:
        del method, notification

    async def on_server_request(
        self, method: str, request_id: Any, params: BaseModel
    ) -> None:
        del method, request_id, params

    async def on_child_ended(self) -> None:
        return


async def probe_codex_model_catalog(codex_executable: str) -> CodexModelCatalog:
    """Ask this machine's codex what it can be run as.

    Raises ``CodexModelCatalogUnavailable`` when the child would not start, would not
    shake hands, or did not answer — all of which mean the same thing to a card: codex is
    here but it did not say, so nothing is listed.
    """
    try:
        return await asyncio.wait_for(
            _ask_codex(codex_executable), timeout=MODEL_CATALOG_TIMEOUT_SECONDS
        )
    except TimeoutError as did_not_answer:
        raise CodexModelCatalogUnavailable(
            "codex did not answer within "
            f"{MODEL_CATALOG_TIMEOUT_SECONDS:g}s"
        ) from did_not_answer


async def _ask_codex(codex_executable: str) -> CodexModelCatalog:
    client = CodexAppServerClient(handler=_SaysNothing(), description="model-catalog")
    try:
        await client.start(
            argv=(codex_executable, "app-server"),
            environment=child_environment(),
            working_directory=Path.home(),
        )
        await client.request(
            "initialize",
            bindings.InitializeParams(
                clientInfo=bindings.ClientInfo(
                    name=CLIENT_NAME, title=CLIENT_TITLE, version=__version__
                ),
                capabilities=INITIALIZE_CAPABILITIES,
                # Every field here is one this probe sets, so there is no absent-versus-null
                # question for codex to answer: dropping the unset ones is the whole rule.
            ).model_dump(mode="json", exclude_none=True, by_alias=True),
        )
        await client.notify("initialized")
        answered = await client.request("model/list", {})
        listed = bindings.ModelListResponse.model_validate(answered)
    except (CodexAppServerError, ValidationError) as would_not_say:
        raise CodexModelCatalogUnavailable(str(would_not_say)) from would_not_say
    finally:
        await client.stop()
    return _catalog(listed)


def _catalog(listed: bindings.ModelListResponse) -> CodexModelCatalog:
    """The answer as a catalog. Hidden models are left out: codex hides its own.

    The efforts are collected in the order they were listed and de-duplicated, so the list
    a picker shows is codex's own order rather than an alphabet nobody chose.
    """
    models: list[CodexModel] = []
    efforts: list[str] = []
    for model in listed.data:
        if model.hidden:
            continue
        offered = tuple(option.reasoningEffort for option in model.supportedReasoningEfforts)
        models.append(
            CodexModel(
                model_id=model.model,
                display_name=model.displayName,
                reasoning_effort_options=offered,
                default_reasoning_effort=model.defaultReasoningEffort,
                is_default=model.isDefault,
            )
        )
        for effort in offered:
            if effort not in efforts:
                efforts.append(effort)
    default = next((model for model in models if model.is_default), None)
    return CodexModelCatalog(
        models=tuple(models),
        reasoning_effort_options=tuple(efforts),
        default_model_id=None if default is None else default.model_id,
        default_reasoning_effort=None if default is None else default.default_reasoning_effort,
    )
