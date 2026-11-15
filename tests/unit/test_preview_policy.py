"""Guard origin authority and compatibility without network/server fixtures."""

from __future__ import annotations

import pytest

from planner.previews.contracts import PreviewError
from planner.previews.policy import request_headers, response_headers

PREFIX = "/dev/tickets/t_one/gen/"
ORIGIN = "https://panels.example"


def test_request_discards_authority_and_only_forwards_declared_cookies() -> None:
    headers = request_headers(
        [
            ("Connection", "x-private"),
            ("X-Private", "secret"),
            ("Tailscale-User-Login", "owner"),
            ("X-Plan-Ticket", "t_other"),
            ("X-Forwarded-Host", "evil"),
            ("Host", "evil"),
            ("Cookie", "panels=secret; demo=one"),
        ],
        port=3333,
        origin=ORIGIN,
        cookie_names={"demo"},
    )
    clean = {key.lower(): value for key, value in headers}
    assert clean["cookie"] == "demo=one"
    assert clean["host"] == "127.0.0.1:3333"
    assert clean["x-forwarded-host"] == "panels.example"
    assert not {"x-private", "tailscale-user-login", "x-plan-ticket"} & clean.keys()


@pytest.mark.parametrize(
    "location",
    ["/login", "https://elsewhere.example/", PREFIX + "../other/", PREFIX + "%2e%2e/other/"],
)
def test_redirect_cannot_leave_generation(location: str) -> None:
    with pytest.raises(PreviewError, match="redirect"):
        response_headers(
            [("Location", location)], request_url=ORIGIN + PREFIX, origin=ORIGIN, prefix=PREFIX
        )


@pytest.mark.parametrize(
    "cookie",
    [
        "demo=yes",
        "demo=yes; Path=/",
        f"demo=yes; Path={PREFIX}; Domain=panels.example",
        f"__Host-demo=yes; Path={PREFIX}",
    ],
)
def test_cookie_requires_generation_path_without_domain(cookie: str) -> None:
    with pytest.raises(PreviewError, match="cookie"):
        response_headers(
            [("Set-Cookie", cookie)], request_url=ORIGIN + PREFIX, origin=ORIGIN, prefix=PREFIX
        )


def test_compatible_redirect_cookie_and_worker_scope() -> None:
    clean, names = response_headers(
        [
            ("Location", "next"),
            ("Set-Cookie", f"demo=yes; Path={PREFIX}; HttpOnly"),
            ("Service-Worker-Allowed", "/"),
        ],
        request_url=ORIGIN + PREFIX,
        origin=ORIGIN,
        prefix=PREFIX,
    )
    assert names == {"demo"}
    assert clean == [("Location", "next"), ("Set-Cookie", f"demo=yes; Path={PREFIX}; HttpOnly")]
