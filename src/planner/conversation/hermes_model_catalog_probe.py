"""Read Hermes's configured provider model catalog without creating a session."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any


def _catalog_payload(hermes_source_root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(hermes_source_root))

    # Some provider helpers print status text. Keep stdout reserved for the one
    # machine-readable result consumed by Panels.
    with contextlib.redirect_stdout(sys.stderr):
        from hermes_cli.config import load_config_readonly  # type: ignore[import-not-found]
        from hermes_cli.models import (  # type: ignore[import-not-found]
            curated_models_for_provider,
            normalize_provider,
        )

        configuration = load_config_readonly()
        model_configuration = configuration.get("model")
        if not isinstance(model_configuration, dict):
            raise RuntimeError("Hermes model configuration is unavailable")

        raw_provider = str(model_configuration.get("provider") or "").strip()
        if not raw_provider:
            raise RuntimeError("Hermes model provider is unavailable")
        provider = str(normalize_provider(raw_provider) or "").strip().lower()
        if not provider:
            raise RuntimeError("Hermes model provider is unavailable")

        native_model = str(model_configuration.get("default") or "").strip() or None
        raw_models = curated_models_for_provider(provider)

    models: list[dict[str, str | None]] = []
    for raw_model, raw_description in raw_models:
        model = str(raw_model or "").strip()
        if not model:
            continue
        description = str(raw_description or "").strip() or None
        models.append({"model": model, "description": description})

    return {
        "provider": provider,
        "nativeModel": native_model,
        "models": models,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("Hermes model catalog probe requires one source root", file=sys.stderr)
        return 2
    source_root = Path(arguments[0])
    if not source_root.is_absolute():
        print("Hermes model catalog probe source root must be absolute", file=sys.stderr)
        return 2
    try:
        payload = _catalog_payload(source_root)
    except BaseException:
        print("Hermes model catalog probe failed", file=sys.stderr)
        return 1
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
