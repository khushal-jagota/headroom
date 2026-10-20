"""Cache only content-addressed Vite assets for the life of their filename."""

from __future__ import annotations

import re

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_HASHED_ASSET = re.compile(r"^assets/[^/]+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9.]+$")


class WebAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if response.status_code in (200, 304) and _HASHED_ASSET.fullmatch(path)
            else "no-cache, must-revalidate"
        )
        return response
