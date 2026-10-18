"""Real browser asset routing across a synthetic Mac/VPS executor boundary.

The material risk is that HTML works while its browser-requested asset, link loss,
or later request recovery fails through the live proxy. Route/transport unit tests
cannot establish the combined browser -> Panels -> relay -> Mac stream boundary.
The two hosts are isolated processes on one Linux host, never a physical Mac.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from playwright.sync_api import Browser, expect
from tests.e2e.harness import REPO_ROOT, WAIT_MS, ServerHandle
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.db import connect
from planner.tickets import data


def test_browser_mac_preview_asset_link_loss_and_new_request_recovery(
    server_factory: Callable[..., ServerHandle], browser: Browser, tmp_path: Path,
) -> None:
    ticket_id = ""

    def seed(db: Path) -> None:
        nonlocal ticket_id
        with connect(str(db)) as conn:
            ticket = data.create_ticket(
                conn, title="Synthetic Mac browser preview", worker_type="coding",
                principal=OWNER_PRINCIPAL, now=1, title_max_chars=200,
            )
            ticket_id = ticket.id
            conn.execute("UPDATE tickets SET locale='macos' WHERE id=?", (ticket_id,))
            conn.commit()

    executor_socket = tmp_path / "executor.sock"
    server = server_factory(seed_db=seed, trusted_ingress_env={
        "PLAN_EXECUTOR_MODE": "remote",
        "PLAN_EXECUTOR_VPS_SOCKET": str(executor_socket),
        "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
    })
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PLAN_")}
    environment.update({
        "PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(tmp_path / "mac-unused.db"),
        "PLAN_LOGS_DIR": str(tmp_path / "mac-logs"),
        "PLAN_HERMES_HOME": str(tmp_path / "mac-hermes"),
    })
    processes: list[subprocess.Popen[bytes]] = []

    def start_mac() -> subprocess.Popen[bytes]:
        log = (tmp_path / f"mac-{len(processes)}.log").open("wb")
        try:
            process = subprocess.Popen([
                sys.executable, "-m", "tests.support.preview_fixture",
                "--root", str(tmp_path / "mac"), "--socket", str(executor_socket),
                "--port", str(port),
            ], cwd=REPO_ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        finally:
            log.close()
        processes.append(process)
        return process

    def wait_ready(ready: bool) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            response = httpx.get(server.base + "/api/executor/health", timeout=2)
            assert response.status_code == 200, response.text
            if response.json()["reconciliation_ready"] is ready:
                return
            if ready:
                log_path = tmp_path / f"mac-{len(processes)-1}.log"
                assert processes[-1].poll() is None, log_path.read_text()
            time.sleep(0.05)
        raise AssertionError(f"executor ready did not become {ready}: {response.text}")

    context = browser.new_context()
    try:
        process = start_mac()
        wait_ready(True)
        page = context.new_page()
        url = f"{server.base}/dev/tickets/{ticket_id}/{port}/"
        assets: list[int] = []
        page.on("response", lambda response: assets.append(response.status)
                if response.url.endswith("/asset.js") else None)
        response = page.goto(url, timeout=WAIT_MS)
        assert response is not None and response.status == 200
        expect(page.locator("#asset")).to_have_text("Mac asset rendered", timeout=WAIT_MS)
        assert assets == [200]
        evidence = Path(os.environ.get("PANELS_PREVIEW_EVIDENCE_DIR", str(tmp_path)))
        evidence.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(evidence / "synthetic-mac-preview-browser.png"))

        process.send_signal(signal.SIGUSR1)
        wait_ready(False)
        # Its HTTP server is still listening; 503 must come from executor loss,
        # never accidental fallback to the VPS's same-numbered loopback port.
        assert httpx.get(f"http://127.0.0.1:{port}/", timeout=2).status_code == 200
        offline = page.goto(url, timeout=WAIT_MS)
        assert offline is not None and offline.status == 503

        process.terminate()
        process.wait(timeout=10)
        start_mac()
        wait_ready(True)
        recovered = page.goto(url, timeout=WAIT_MS)
        assert recovered is not None and recovered.status == 200
        expect(page.locator("#asset")).to_have_text("Mac asset rendered", timeout=WAIT_MS)
        (evidence / "synthetic-mac-preview-browser.txt").write_text(
            "PASS: real Chromium browser -> isolated live Panels app -> production "
            "ExecutorEndpoint -> production relay -> MacExecutor -> loopback HTTP fixture.\n"
            "Synthetic two-host boundary on one Linux host; no physical Mac or live route.\n"
            "Initial HTML 200; relative JavaScript asset 200 and rendered DOM asserted.\n"
            "Executor link cut with loopback HTTP still alive: new browser request 503. "
            "Replacement executor: new "
            "HTML and asset requests 200 with rendered DOM asserted.\n",
        )
    finally:
        context.close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
