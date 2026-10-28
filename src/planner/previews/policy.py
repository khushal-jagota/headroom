"""Pure HTTP compatibility rules for a prefix-aware, trusted application."""

from __future__ import annotations

from http.cookies import CookieError, SimpleCookie
from urllib.parse import unquote, urljoin, urlsplit

from planner.previews import config
from planner.previews.contracts import PreviewError

_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def header_size(headers: list[tuple[str, str]]) -> None:
    if sum(len(k.encode()) + len(v.encode()) + 4 for k, v in headers) > config.HEADER_BYTES:
        raise PreviewError("Preview headers exceed the supported limit.", 431)


def within_prefix(path: str, prefix: str) -> bool:
    decoded = unquote(path)
    return (
        decoded.startswith(prefix)
        and "\\" not in decoded
        and not any(part in {".", ".."} for part in decoded.split("/"))
    )


def request_headers(
    headers: list[tuple[str, str]], *, port: int, origin: str, cookie_names: set[str]
) -> list[tuple[str, str]]:
    header_size(headers)
    connection = {
        token.strip().lower()
        for key, value in headers
        if key.lower() == "connection"
        for token in value.split(",")
    }
    clean: list[tuple[str, str]] = []
    for key, value in headers:
        name = key.lower()
        if (
            name in _HOP | connection
            or name in {"host", "forwarded", "origin"}
            or name.startswith(("tailscale-", "x-plan-", "x-forwarded-", "proxy-"))
        ):
            continue
        if name == "cookie":
            cookie = SimpleCookie()
            try:
                cookie.load(value)
            except CookieError:
                continue
            value = "; ".join(
                f"{name}={morsel.coded_value}"
                for name, morsel in cookie.items()
                if name in cookie_names
            )
            if not value:
                continue
        clean.append((key, value))
    public = urlsplit(origin)
    clean.extend(
        [
            ("Host", f"127.0.0.1:{port}"),
            ("Origin", origin),
            ("X-Forwarded-Host", public.netloc),
            ("X-Forwarded-Proto", "https"),
        ]
    )
    return clean


def response_headers(
    headers: list[tuple[str, str]], *, request_url: str, origin: str, prefix: str
) -> tuple[list[tuple[str, str]], set[str]]:
    header_size(headers)
    connection = {
        token.strip().lower()
        for key, value in headers
        if key.lower() == "connection"
        for token in value.split(",")
    }
    clean: list[tuple[str, str]] = []
    cookies: set[str] = set()
    for key, value in headers:
        name = key.lower()
        if name in _HOP | connection or name == "service-worker-allowed":
            continue
        if name == "location":
            resolved = urlsplit(urljoin(request_url, value))
            public = urlsplit(origin)
            if (resolved.scheme, resolved.netloc) != (
                public.scheme,
                public.netloc,
            ) or not within_prefix(resolved.path, prefix):
                raise PreviewError("Preview redirect must stay under its prepared base path.", 502)
        if name == "set-cookie":
            parsed = SimpleCookie()
            try:
                parsed.load(value)
            except CookieError as exc:
                raise PreviewError("Preview returned an incompatible cookie.", 502) from exc
            if not parsed:
                raise PreviewError("Preview returned an incompatible cookie.", 502)
            for cookie_name, morsel in parsed.items():
                if (
                    cookie_name.startswith("__Host-")
                    or morsel["domain"]
                    or not within_prefix(morsel["path"], prefix)
                ):
                    raise PreviewError(
                        "Preview cookies require an explicit generation Path and no Domain.", 502
                    )
                cookies.add(cookie_name)
        clean.append((key, value))
    return clean, cookies
