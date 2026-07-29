"""Hermes' configured model inventory, exposed as one small JSON door.

This file is deliberately self-contained. Panels runs it with Hermes' configured Python
interpreter, where ``hermes_cli`` is installed, instead of importing Hermes provider and
credential machinery into the Panels process.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from typing import Final, Protocol, cast

SCHEMA_VERSION: Final = 1


class _HermesInventory(Protocol):
    def load_picker_context(self) -> object: ...

    def build_models_payload(
        self, context: object, **options: object
    ) -> dict[str, object]: ...


def _setter_provider_id(provider_id: str, known_provider_names: object) -> str:
    """Spell a picker provider the way Hermes' ``session/set_model`` parser reads it."""
    if provider_id.startswith("custom:"):
        return provider_id
    if isinstance(known_provider_names, set) and provider_id.lower() in known_provider_names:
        return provider_id
    # Hermes' picker exposes arbitrary ``providers:`` entries by their raw slug, but its
    # model-input parser recognizes only built-ins and the ``custom:<name>:<model>``
    # spelling. The compatible custom-provider view includes these configured endpoints,
    # so this is the opaque ID that resolves back to the same provider at selection time.
    return f"custom:{provider_id}"


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _configured_inventory(*, refresh: bool) -> dict[str, object]:
    inventory = cast(
        _HermesInventory, importlib.import_module("hermes_cli.inventory")
    )
    models_module = importlib.import_module("hermes_cli.models")
    known_provider_names: object = getattr(
        models_module, "_KNOWN_PROVIDER_NAMES", set()
    )

    # Provider helpers occasionally print status while they inspect local endpoints.
    # stdout belongs exclusively to this door's one JSON answer.
    with redirect_stdout(sys.stderr):
        context = inventory.load_picker_context()
        payload = inventory.build_models_payload(
            context,
            explicit_only=True,
            include_unconfigured=False,
            picker_hints=True,
            canonical_order=True,
            pricing=False,
            capabilities=False,
            force_fresh_nous_tier=False,
            refresh=refresh,
            # Normal first demand reads cached inventory and probes only the active
            # custom endpoint. Explicit refresh deliberately forwards freshness and
            # permits every configured custom endpoint to answer.
            probe_custom_providers=refresh,
            probe_current_custom_provider=not refresh,
            max_models=None,
        )
    rows = payload.get("providers")
    if not isinstance(rows, list):
        rows = []

    providers: list[dict[str, object]] = []
    qualified_model_ids: set[str] = set()
    for candidate in rows:
        if not isinstance(candidate, Mapping):
            continue
        raw_provider_id = _text(candidate.get("slug"))
        if raw_provider_id is None:
            continue
        provider_id = _setter_provider_id(raw_provider_id, known_provider_names)
        provider_name = _text(candidate.get("name")) or raw_provider_id
        raw_models = candidate.get("models")
        if not isinstance(raw_models, list):
            continue
        models: list[dict[str, str]] = []
        seen_native_ids: set[str] = set()
        for raw_model in raw_models:
            native_model_id = _text(raw_model)
            if native_model_id is None or native_model_id in seen_native_ids:
                continue
            seen_native_ids.add(native_model_id)
            qualified_model_id = f"{provider_id}:{native_model_id}"
            qualified_model_ids.add(qualified_model_id)
            models.append(
                {
                    "id": qualified_model_id,
                    "displayName": native_model_id,
                    "detail": provider_name,
                }
            )
        if models:
            providers.append(
                {
                    "id": provider_id,
                    "displayName": provider_name,
                    "models": models,
                }
            )

    raw_current_provider = _text(payload.get("provider"))
    current_provider = (
        None
        if raw_current_provider is None
        else _setter_provider_id(raw_current_provider, known_provider_names)
    )
    current_model = _text(payload.get("model"))
    configured = current_provider is not None and current_model is not None
    default_model_id = (
        f"{current_provider}:{current_model}" if configured else None
    )
    runnable = default_model_id is not None and default_model_id in qualified_model_ids
    if not configured:
        status = "not_configured"
    elif not runnable:
        status = "default_unavailable"
    else:
        status = "runnable"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": status,
        "providers": providers,
        "defaultModelId": default_model_id,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    arguments = parser.parse_args(argv)
    answer = _configured_inventory(refresh=arguments.refresh)
    print(json.dumps(answer, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
