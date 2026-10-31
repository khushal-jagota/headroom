"""A browser alone can prove Vite's generated module/HMR URLs under the root SW.

One Panels host, context and connected case cover two real Git-worktree apps.
The extra loopback TLS edge replaces only Tailscale TLS/header insertion: neither
Vite, Linux socket evidence nor Panels transport is mocked. Cost is printed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.sync_api import Browser, WebSocket, expect
from tests.support.server import REPO_ROOT, ServerHandle


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    # Chromium's service-worker loader does not honor context.ignore_https_errors.
    # The isolated self-signed TLS edge needs the browser-level equivalent too.
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            "--ignore-certificate-errors",
        ],
    }


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "darwin", reason="Real preview registration needs Linux /proc")
def test_real_vite_applications_share_only_their_generation(
    browser: Browser,
    server_factory: Callable[..., ServerHandle],
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    # Survey existing listeners before choosing OS-assigned free ports.
    subprocess.run(["ss", "-ltn"], check=True, capture_output=True, env=environment)
    edge_port = _port()
    origin = f"https://localhost:{edge_port}"
    server = server_factory(
        trusted_ingress_env={
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@example.test",
            "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": origin,
        }
    )
    repo = tmp_path / "project"
    repo.mkdir()
    for command in (
        ["git", "init", "-q", str(repo)],
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(command, check=True, env=environment, capture_output=True)
    certificate, key = tmp_path / "certificate.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
        env=environment,
    )
    with ExitStack() as stack, httpx.Client(base_url=server.base, timeout=10) as owner:

        def api(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            response = owner.request(method, path, **kwargs)
            assert response.is_success, response.text
            return dict(response.json())

        def preview_cli(ticket_id: str, *arguments: str) -> dict[str, Any]:
            worker_environment = {
                **environment,
                "PLAN_ACTOR": "worker",
                "PLAN_TICKET_ID": ticket_id,
                "PLAN_SERVER_URL": server.base,
            }
            result = subprocess.run(
                [str(REPO_ROOT / ".venv/bin/panels"), "preview", *arguments, "--json"],
                cwd=REPO_ROOT,
                env=worker_environment,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == 0, result.stderr
            return dict(json.loads(result.stdout))

        owner_preview = owner.get("/api/previews")
        assert owner_preview.status_code == 400
        assert owner_preview.json()["error"]["code"] == "agent_forbidden"

        project = api(
            "POST", "/api/projects", json={"name": "Vite proof", "folder_path": str(repo)}
        )
        apps = []
        for index in range(2):
            ticket = api(
                "POST",
                "/api/tickets",
                json={
                    "title": f"Vite {index}",
                    "worker_type": "coding",
                    "brief": "Browser proof",
                    "project_id": project["id"],
                    "manager": {"kind": "chief", "id": "chief"},
                },
            )
            worktree = repo / "worktrees" / f"{ticket['id']}-preview"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree)],
                check=True,
                capture_output=True,
                env=environment,
            )
            prepared = preview_cli(ticket["id"], "prepare")
            base = prepared["base_path"]
            app_port = _port()
            (worktree / "index.html").write_text(
                '<title>Application preview proof</title><h1 id="title"></h1>'
                '<img id="asset" src="./badge.svg"><a href="./next">Navigate</a>'
                '<script type="module" src="./main.js"></script>'
            )
            (worktree / "badge.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="24">'
                '<rect width="80" height="24" fill="teal"/></svg>'
            )
            source = worktree / "message.js"
            source.write_text(f'export default "Application {index} original";\n')
            (worktree / "main.js").write_text(
                'import message from "./message.js";'
                'document.querySelector("#title").textContent=message;'
                'if(import.meta.hot) import.meta.hot.accept("./message.js", module=>{'
                'document.querySelector("#title").textContent=module.default;});'
            )
            (worktree / "vite.config.mjs").write_text(
                "export default "
                + json.dumps(
                    {
                        "base": base,
                        "server": {
                            "host": "127.0.0.1",
                            "port": app_port,
                            "strictPort": True,
                            "hmr": {
                                "host": "localhost",
                                "protocol": "wss",
                                "clientPort": edge_port,
                            },
                        },
                    }
                )[:-1]
                + ',plugins:[{name:"proof",configureServer(server){'
                "server.middlewares.use((req,res,next)=>{"
                f"if(req.url==={json.dumps(base + 'redirect')}){{res.statusCode=302;"
                f'res.setHeader("Location",{json.dumps(origin + base + "next")});'
                "res.end();return;}"
                f'if(req.url==={json.dumps(base + "cookie")}){{res.setHeader("Set-Cookie",'
                + json.dumps(f"app{index}=persisted; Path={base}; SameSite=Lax; Secure")
                + ');res.end("set");return;}'
                f"if(req.url==={json.dumps(base + 'read-cookie')}){{"
                'res.end(req.headers.cookie||"");return;}'
                f"if(req.url==={json.dumps(base + 'bad-redirect')}){{res.statusCode=302;"
                f'res.setHeader("Location",{json.dumps(origin + "/")});res.end();return;}}'
                f"if(req.url==={json.dumps(base + 'bad-cookie')}){{"
                'res.setHeader("Set-Cookie","escape=blocked; Path=/");res.end();return;}'
                "next();});}}]};"
            )
            log = stack.enter_context((worktree / "vite.log").open("wb"))
            process = subprocess.Popen(
                [
                    "node",
                    str(REPO_ROOT / "web/node_modules/vite/bin/vite.js"),
                    "--config",
                    str(worktree / "vite.config.mjs"),
                ],
                cwd=worktree,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            stack.callback(_stop, process)
            deadline = time.monotonic() + 15
            while True:
                assert process.poll() is None, (worktree / "vite.log").read_text()
                try:
                    if (
                        httpx.get(f"http://127.0.0.1:{app_port}{base}", timeout=1).status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline, (worktree / "vite.log").read_text()
                time.sleep(0.05)
            record = preview_cli(ticket["id"], "register", "--port", str(app_port))
            apps.append((ticket, record, source))
        # Settle this synthetic Ticket through the real writers before timing expiry.
        fields = ("brief", "what_changes", "implementation")
        for field, next_field in zip(fields, (*fields[1:], "consequences"), strict=True):
            worker = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": apps[0][0]["id"]}
            api(
                "POST",
                f"/api/tickets/{apps[0][0]['id']}/propose",
                headers=worker,
                json={"body": "Synthetic browser proof."},
            )
            api(
                "POST",
                f"/api/tickets/{apps[0][0]['id']}/accept/{field}",
                json={
                    "next_ceiling": f"needs_{next_field}",
                    "next_holder": {"kind": "owner", "id": "owner"},
                },
            )
        api(
            "POST",
            f"/api/tickets/{apps[0][0]['id']}/propose",
            headers=worker,
            json={"body": "Synthetic browser proof complete."},
        )
        edge_log = stack.enter_context((tmp_path / "edge.log").open("wb"))
        edge = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.support.application_preview_browser",
                server.base,
                str(edge_port),
                str(certificate),
                str(key),
            ],
            cwd=REPO_ROOT,
            env=environment,
            stdout=edge_log,
            stderr=subprocess.STDOUT,
        )
        stack.callback(_stop, edge)
        deadline = time.monotonic() + 10
        while True:
            try:
                if httpx.get(origin + "/api/meta", verify=False, timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert edge.poll() is None, (tmp_path / "edge.log").read_text()
            assert time.monotonic() < deadline
            time.sleep(0.05)
        context = browser.new_context(ignore_https_errors=True)
        stack.callback(context.close)
        root = context.new_page()
        root.goto(origin + "/", timeout=15000)
        root.evaluate("navigator.serviceWorker.register('/service-worker.js', {scope:'/'})")
        root.evaluate("navigator.serviceWorker.ready.then(registration=>registration.scope)")
        root.reload(timeout=15000)
        root.wait_for_function("navigator.serviceWorker.controller !== null", timeout=10000)
        network: list[str] = []
        sockets: list[str] = []
        socket_peers: list[WebSocket] = []

        def remember_socket(socket: WebSocket) -> None:
            sockets.append(socket.url)
            socket_peers.append(socket)

        pages = []
        for index, (_, record, _) in enumerate(apps):
            page = context.new_page()
            page.on("request", lambda request: network.append(request.url))
            page.on("websocket", remember_socket)
            page.goto(record["url"], timeout=15000)
            expect(page.locator("#title")).to_have_text(
                f"Application {index} original", timeout=10000
            )
            assert page.locator("#asset").evaluate(
                "image=>image.complete && image.naturalWidth === 80"
            )
            page.get_by_text("Navigate", exact=True).click()
            assert page.url == record["url"] + "next"
            page.goto(record["url"] + "redirect", timeout=15000)
            assert page.url == record["url"] + "next"
            page.evaluate("async base=>{await fetch(base+'cookie');}", record["base_path"])
            assert (
                page.evaluate(
                    "async base=>(await fetch(base+'read-cookie')).text()", record["base_path"]
                )
                == f"app{index}=persisted"
            )
            pages.append(page)
        for endpoint in ("bad-redirect", "bad-cookie"):
            assert (
                pages[0].evaluate(
                    "async url=>(await fetch(url,{redirect:'manual'})).status",
                    apps[0][1]["base_path"] + endpoint,
                )
                == 502
            )
        apps[0][2].write_text('export default "Application 0 edited through HMR";\n')
        expect(pages[0].locator("#title")).to_have_text(
            "Application 0 edited through HMR", timeout=15000
        )
        expect(pages[1].locator("#title")).to_have_text("Application 1 original")
        assert len(sockets) >= 2
        for address in network + sockets:
            parsed = urlsplit(address)
            assert parsed.netloc == urlsplit(origin).netloc, address
            assert any(parsed.path.startswith(app[1]["base_path"]) for app in apps), address
        assert all(address.startswith("wss://") for address in sockets)
        evidence = os.environ.get("PANELS_UI_PROOF_DIR")
        if evidence:
            folder = Path(evidence)
            folder.mkdir(parents=True, exist_ok=True)
            pages[0].screenshot(path=str(folder / "application-preview-hmr.png"), full_page=True)
            (folder / "application-preview-network.json").write_text(
                json.dumps(
                    {
                        "requests": network,
                        "websockets": sockets,
                        "root_service_worker": True,
                        "elapsed_seconds": time.monotonic() - started,
                    },
                    indent=2,
                )
            )
        # Canonical completion/deletion closes HMR without killing either Vite process.
        active_sockets = [peer for peer in socket_peers if not peer.is_closed()]
        assert len(active_sockets) == 2
        closed_at: dict[str, float] = {}

        def record_close(peer: WebSocket) -> None:
            closed_at.setdefault(peer.url, time.monotonic())

        for peer in active_sockets:
            peer.on("close", record_close)
        expired_at = time.monotonic()
        api(
            "POST",
            f"/api/tickets/{apps[0][0]['id']}/accept/consequences",
            json={
                "next_ceiling": "done",
                "next_holder": {"kind": "owner", "id": "owner"},
            },
        )
        api("DELETE", f"/api/tickets/{apps[1][0]['id']}")
        deadline = expired_at + 2
        while len(closed_at) < 2:
            assert time.monotonic() < deadline, closed_at
            root.wait_for_timeout(10)
        assert all(moment - expired_at < 2 for moment in closed_at.values())
        if evidence:
            (Path(evidence) / "application-preview-expiry.json").write_text(
                json.dumps(
                    {
                        "hmr_close_seconds": {
                            url: moment - expired_at for url, moment in closed_at.items()
                        },
                        "application_pids_still_alive": [
                            record["process"]["pid"] for _, record, _ in apps
                        ],
                    },
                    indent=2,
                )
            )
        for _, record, _ in apps:
            os.kill(record["process"]["pid"], 0)
        for page, (_, record, _) in zip(pages, apps, strict=True):
            response = page.goto(record["url"], timeout=10000)
            assert response is not None and response.status == 410
            assert any(
                word in page.locator("body").inner_text().lower() for word in ("preview", "ticket")
            )
        ended = preview_cli(apps[0][0]["id"], "show")
        assert ended["state"] == "ended"
    print(f"VITE_BROWSER_BOUNDARY seconds={time.monotonic() - started:.3f}")
