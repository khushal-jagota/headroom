"""What codex says it can be run as, asked of the app-server and nothing else.

Codex advertises model metadata through its local app-server. Each model carries its
own advertised reasoning efforts. Catalog presence does not establish execution
entitlement. Catalog absence does not establish an authoritative refusal.

A probe starts one short-lived child and reads the catalog pages. It starts no thread
or turn and sends no provider prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import anyio
from pydantic import BaseModel, ValidationError

from planner import __version__
from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.adapter import (
    CLIENT_NAME,
    CLIENT_TITLE,
    INITIALIZE_CAPABILITIES,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexAppServerError,
    child_environment,
)
from planner.conversation.backends.probe_lifetime import owned_probe

# The whole probe — spawn, handshake, one request, stop — against a local child. Long
# enough for a cold start on a slow machine, short enough that a card is never left waiting.
MODEL_CATALOG_TIMEOUT_SECONDS: Final = 30.0
# One absolute reply budget leaves time for normal child shutdown. Cleanup still
# settles physically if it exceeds this reserve or the outer discovery deadline.
MODEL_CATALOG_REPLY_CLEANUP_RESERVE_SECONDS: Final = 1.0


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
    hidden: bool = False


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
    complete: bool = True
    visibility_limited: bool = True
    authenticated: bool | None = None


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


async def probe_codex_model_catalog(
    codex_executable: str,
    *,
    environment: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
) -> CodexModelCatalog:
    """Ask this machine's codex what it can be run as.

    Raises ``CodexModelCatalogUnavailable`` when the child would not start, would not
    shake hands, or did not answer — all of which mean the same thing to a card: codex is
    here but it did not say, so nothing is listed.
    """
    try:
        return await owned_probe(
            lambda: _ask_codex(
                codex_executable, environment=environment, working_directory=working_directory
            ),
            timeout_seconds=MODEL_CATALOG_TIMEOUT_SECONDS,
        )
    except TimeoutError as did_not_answer:
        raise CodexModelCatalogUnavailable(
            "codex did not answer within "
            f"{MODEL_CATALOG_TIMEOUT_SECONDS:g}s"
        ) from did_not_answer


async def _ask_codex(
    codex_executable: str,
    *,
    environment: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
) -> CodexModelCatalog:
    client = CodexAppServerClient(handler=_SaysNothing(), description="model-catalog")
    pages: list[bindings.Model1] = []
    complete = False
    loop = asyncio.get_running_loop()
    reply_deadline = loop.time() + max(
        0, MODEL_CATALOG_TIMEOUT_SECONDS - MODEL_CATALOG_REPLY_CLEANUP_RESERVE_SECONDS
    )
    try:
        await client.start(
            argv=(codex_executable, "app-server"),
            environment=child_environment() if environment is None else environment,
            working_directory=Path.home() if working_directory is None else working_directory,
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
            timeout_seconds=max(0, reply_deadline - loop.time()),
        )
        await client.notify("initialized")
        account = bindings.GetAccountResponse.model_validate(await client.request(
            "account/read", {"refreshToken": False},
            timeout_seconds=max(0, reply_deadline - loop.time()),
        ))
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            try:
                answered = await client.request(
                    "model/list", {} if cursor is None else {"cursor": cursor},
                    timeout_seconds=max(0, reply_deadline - loop.time()),
                )
                listed = bindings.ModelListResponse.model_validate(answered)
            except (CodexAppServerError, ValidationError):
                if not pages:
                    raise
                break
            pages.extend(listed.data)
            cursor = listed.nextCursor
            if cursor is None:
                complete = True
                break
            if cursor in seen:
                break
            seen.add(cursor)
    except (CodexAppServerError, ValidationError) as would_not_say:
        raise CodexModelCatalogUnavailable(str(would_not_say)) from would_not_say
    finally:
        with anyio.CancelScope(shield=True):
            await client.stop()
    return _catalog(
        bindings.ModelListResponse(data=pages), complete=complete,
        authenticated=account.account is not None or not account.requiresOpenaiAuth,
    )


def _catalog(
    listed: bindings.ModelListResponse, *, complete: bool = True, authenticated: bool | None = None
) -> CodexModelCatalog:
    """Keep provider metadata, including hidden rows, for the snapshot projection.

    The efforts are collected in the order they were listed and de-duplicated, so the list
    a picker shows is codex's own order rather than an alphabet nobody chose.
    """
    models: list[CodexModel] = []
    efforts: list[str] = []
    for model in listed.data:
        offered = tuple(option.reasoningEffort for option in model.supportedReasoningEfforts)
        models.append(
            CodexModel(
                model_id=model.model,
                display_name=model.displayName,
                reasoning_effort_options=offered,
                default_reasoning_effort=model.defaultReasoningEffort,
                is_default=model.isDefault,
                hidden=model.hidden,
            )
        )
        for effort in offered:
            if effort not in efforts:
                efforts.append(effort)
    default = next((model for model in models if model.is_default and not model.hidden), None)
    return CodexModelCatalog(
        models=tuple(models),
        complete=complete,
        authenticated=authenticated,
        reasoning_effort_options=tuple(efforts),
        default_model_id=None if default is None else default.model_id,
        default_reasoning_effort=None if default is None else default.default_reasoning_effort,
    )
