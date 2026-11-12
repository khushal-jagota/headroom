"""Compression for answers, and no compression in front of the routes marked to skip it.

Bodies over 1 KB travel gzipped, because reads are large and the link is remote. Two
kinds of route must not go through that middleware at all.

A live event stream is the first, and the reason is not the one the obvious reading
gives. Starlette's gzip middleware withholds the response headers until the first body
chunk arrives, unconditionally, because it cannot know what to say about the encoding
until it has seen something to encode. Its list of excluded content types is read later,
in the body branch: it decides whether frames are compressed, not whether headers are
held. A stream that has nothing to say yet sends no body, so a browser that has just
connected never receives the headers and ``EventSource`` never opens. Nothing set on the
response changes this, because the decision the middleware has already made is to wait.

The Mac update channel is the second. Its archives are already gzipped, and a Mac client
that does not decompress transport encoding would save the wrapped bytes and reject them
against the published size and hash. Mac runtimes already in the field send
``Accept-Encoding: gzip`` and cannot change before they install an update, so the server
is the side that has to send these bytes as they are.

So the decision is made here instead, before the request reaches the application, and a
marked request is sent down a path that has no compression in it. Which requests those
are comes from the routes themselves: a route is marked where it is written, and the
paths come from the marked routes. A route added later is covered by marking it, and
there is no list of paths anywhere to keep in step.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any

from starlette.middleware.gzip import GZipMiddleware
from starlette.routing import BaseRoute, Route, compile_path

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_MARK = "__answers_uncompressed__"


def answers_uncompressed[Endpoint: Callable[..., Any]](endpoint: Endpoint) -> Endpoint:
    """Mark a route handler whose response must never pass through the gzip middleware.

    Apply it under the route decorator, so that the route registers the marked function::

        @app.get("/api/changes")
        @answers_uncompressed
        async def changes() -> StreamingResponse:
    """
    setattr(endpoint, _MARK, True)
    return endpoint


def uncompressed_route_patterns(
    routes: Iterable[BaseRoute], prefix: str = ""
) -> tuple[re.Pattern[str], ...]:
    """The paths of the marked routes in one router or application, compiled to match.

    A router's own routes carry the path it was written with, so the prefix it is
    included under is supplied by whoever includes it. An application's routes do not
    list the routes of a router it includes, so pass the router itself.
    """
    return tuple(
        compile_path(prefix + route.path)[0]
        for route in routes
        if isinstance(route, Route) and getattr(route.endpoint, _MARK, False)
    )


class CompressExceptMarkedRoutes:
    """Gzip in front of the application, with the marked routes routed around it."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        uncompressed_patterns: Iterable[re.Pattern[str]],
        minimum_size: int,
        compresslevel: int,
    ) -> None:
        self._uncompressed = app
        self._compressed = GZipMiddleware(
            app, minimum_size=minimum_size, compresslevel=compresslevel
        )
        self._uncompressed_patterns = tuple(uncompressed_patterns)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._is_uncompressed_request(scope):
            await self._uncompressed(scope, receive, send)
            return
        await self._compressed(scope, receive, send)

    def _is_uncompressed_request(self, scope: Scope) -> bool:
        if scope["type"] != "http":
            return False
        # The same string the router will match on. This middleware is the outermost
        # one and the application is served at the root, so nothing has taken a prefix
        # off the path yet and nothing is going to put one back.
        path: str = scope["path"]
        return any(pattern.match(path) for pattern in self._uncompressed_patterns)
