"""Browser proof for hosted trusted-ingress origin policy."""

from __future__ import annotations

from collections.abc import Callable

from conftest import WAIT_MS, ServerHandle
from playwright.sync_api import Browser

ALLOWED_LOGIN = "khushal@example.com"

OPEN_SOCKET = """url => new Promise(resolve => {
    let answered = false;
    const finish = value => {
        if (!answered) {
            answered = true;
            resolve(value);
        }
    };
    const socket = new WebSocket(url);
    socket.onopen = () => finish({opened: true, code: null});
    socket.onclose = event => finish({opened: false, code: event.code});
    setTimeout(() => finish({opened: false, code: -1}), 5000);
})"""


def _trusted_ingress_env(base: str) -> dict[str, str]:
    return {
        "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
        "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": ALLOWED_LOGIN,
        "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": base,
    }


def test_trusted_ingress_browser_websocket_origin_and_wrong_origin_rejection(
    server_factory: Callable[..., ServerHandle],
    browser: Browser,
) -> None:
    server = server_factory(trusted_ingress_env=_trusted_ingress_env)

    allowed_context = browser.new_context(
        extra_http_headers={"Tailscale-User-Login": ALLOWED_LOGIN}
    )
    try:
        page = allowed_context.new_page()
        page.goto(f"{server.base}/#/workspace")
        # The allowed browser is admitted end to end: its live-update stream opens.
        page.wait_for_function(
            "() => window.__plannerDebug && window.__plannerDebug.sseOpens >= 1",
            timeout=WAIT_MS,
        )
    finally:
        allowed_context.close()

    attacker_server = server_factory()
    attacker_context = browser.new_context()
    try:
        attacker = attacker_context.new_page()
        attacker.goto(f"{attacker_server.base}/")
        rejected_socket = server.base.replace("http://", "ws://") + "/api/conversation"
        result = attacker.evaluate(OPEN_SOCKET, rejected_socket)

        # A server-side close before the WebSocket is accepted is exposed by Chromium as
        # abnormal closure 1006. The direct ASGI test asserts the middleware's 1008 policy
        # code, and that an allowed origin gets past the middleware into the application.
        assert result == {"opened": False, "code": 1006}
    finally:
        attacker_context.close()
