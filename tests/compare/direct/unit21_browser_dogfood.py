"""Explicit manual browser proof. Ordinary CI does not discover this filename.

On hosted CI only, run scripts/dev pytest tests/compare/direct/unit21_browser_dogfood.py.
Reuse the existing TS comparison host and built frontend. All machine evidence
comes from the existing synthetic collector fixture, never this host's services.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect, sync_playwright
from tests.support.server_fixtures import _start_server, _stop_server
from tests.support.synthetic_homes import create_synthetic_homes
from tests.unit.test_vps_metrics_collectors import fixture_collector, process, write

from planner.environments.vps_metrics import collectors
from planner.environments.vps_metrics.data import Store

ROOT = Path(__file__).resolve().parents[3]
BROWSER_CACHE = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
EVIDENCE = ROOT / "server/environments/evidence"


def _private_environment(root: Path) -> dict[str, str]:
    selected_node = shutil.which("node")
    if selected_node is None:
        raise FileNotFoundError("Node is absent from the caller's PATH")
    environment = {
        "PATH": str(Path(selected_node).resolve().parent) + os.pathsep + os.defpath,
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        **create_synthetic_homes(root),
    }
    if BROWSER_CACHE is not None:
        environment["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_CACHE
    for key, name in {
        "PLAN_LOGS_DIR": "logs",
        "PLAN_BACKUP_DIR": "backups",
        "PLAN_WORKSPACE_ROOT": "workspace",
        "PLAN_TICKET_SCRATCH_ROOT": "scratch",
        "PLAN_APP_ROOT": "app",
    }.items():
        path = root / name
        path.mkdir()
        environment[key] = str(path)
    config = root / "config.yaml"
    config.write_text("{}\n")
    environment.update(
        {
            "PLAN_CONFIG_PATH": str(config),
            "PLAN_DB_PATH": str(root / "server/planning.db"),
            "PLAN_TICKET_SCRATCH_DATABASE": str(root / "server/planning.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_CREDENTIAL_KEY_PATH": str(root / "credential.key"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
        }
    )
    app = Path(environment["PLAN_APP_ROOT"])
    (app / "web").mkdir()
    (app / "web/dist").symlink_to(ROOT / "web/dist", target_is_directory=True)
    for name in ("assets", "static"):
        (app / name).symlink_to(ROOT / name, target_is_directory=True)
    return environment


def test_unit21_browser_vps_retained_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    began = time.perf_counter()
    environment = _private_environment(tmp_path)
    fixture = tmp_path / "synthetic-machine"
    seeded: dict[str, Any] = {}

    def seed(database: Path) -> None:
        collector, clock, calls = fixture_collector(fixture)
        # The original collector fixture includes an unreadable PID. This proof needs a
        # complete immutable page to check that its process reaches the real UI.
        shutil.rmtree(collector.proc / "12")
        write(collector.proc, "meminfo", "MemTotal: 8000000 kB\nMemAvailable: 3000000 kB\n")
        write(collector.proc, "loadavg", "0.75 1.0 1.2 1/2 1")
        write(collector.proc, "uptime", "345600 10")
        collector.statvfs = lambda _path: os.statvfs_result(
            (4096, 4096, 50_000_000, 20_000_000, 20_000_000, 0, 0, 0, 0, 255)
        )
        original_read = collectors._read

        def synthetic_read(path: Path) -> str:
            assert path.is_relative_to(fixture)
            assert path.name not in {"cmdline", "environ"}
            return original_read(path)

        def synthetic_constant(name: str) -> int:
            assert name in {"SC_CLK_TCK", "SC_PAGE_SIZE"}
            return 100 if name == "SC_CLK_TCK" else 4096

        store = Store(Path(str(database) + ".vps-metrics.sqlite3"))
        store.initialize()
        last = time.time() - 1
        user, idle = 100, 100
        with monkeypatch.context() as scope:
            scope.setattr(collectors, "_read", synthetic_read)
            scope.setattr(os, "sysconf", synthetic_constant)
            for index in range(21):
                clock[0] = last - (20 - index) * 15
                percent = 42 if index == 20 else [22, 32, 45, 55, 38, 30, 42][index % 7]
                if index:
                    user += percent
                    idle += 100 - percent
                write(
                    collector.proc, "stat", f"cpu {user} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1\ncpu1 1\n"
                )
                write(
                    collector.proc,
                    "net/dev",
                    "header\nheader\n"
                    f"eth0: {100 + index * 15_000_000} 0 0 0 0 0 0 0 "
                    f"{200 + index * 7_500_000} 0 0 0 0 0 0 0\n",
                )
                write(
                    collector.proc,
                    "diskstats",
                    f"254 0 vda 1 0 {10 + index * 61_440} 0 1 0 {20 + index * 122_880} 0 0 0 0 0\n",
                )
                process(collector.proc, 11, cpu=100 + index * 750)
                write(
                    collector.cgroup,
                    "user.slice/user@1000.service/app.slice/panels.service/memory.current",
                    "512000000",
                )
                current = collector.collect(include_slow=index % 4 == 0)
                store.append(current)
        assert current["metrics"]["cpu_percent"]["value"] == 42
        assert current["process_availability"] == "available"
        seeded.update({"sequence": 21, "timestamp": last, "service_scopes": calls})

    network: list[dict[str, Any]] = []
    page_errors: list[str] = []
    handle = None
    evidence: dict[str, Any] = {}
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # The existing server fixture inherits this deliberately minimal private environment.
    # Static build aliases and the browser binary cache are read-only shared inputs.
    with patch.dict(os.environ, environment, clear=True):
        try:
            handle = _start_server(
                tmp_path / "server",
                fake_now=datetime.now(UTC).isoformat(),
                seed_db=seed,
                server_module="tests.compare.ts_launcher",
                trusted_ingress_env={
                    key: value
                    for key, value in environment.items()
                    if key.startswith("PLAN_")
                    and key not in {"PLAN_DB_PATH", "PLAN_LOGS_DIR", "PLAN_DISPATCHER_LOCK_PATH"}
                },
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(viewport={"width": 1440, "height": 1500})
                    page = context.new_page()
                    page.on("pageerror", lambda error: page_errors.append(str(error)))

                    def response_seen(response: Any) -> None:
                        path = urlsplit(response.url).path
                        if path.startswith("/api/"):
                            entry: dict[str, Any] = {
                                "method": response.request.method,
                                "path": path,
                                "status": response.status,
                            }
                            if path == "/api/resource-body":
                                entry["resource_key"] = parse_qs(urlsplit(response.url).query).get(
                                    "key", [None]
                                )[0]
                            network.append(entry)

                    page.on("response", response_seen)
                    with page.expect_response(
                        lambda response: urlsplit(response.url).path == "/api/updates",
                        timeout=15_000,
                    ) as updates:
                        page.goto(
                            handle.base + "/#/vps", wait_until="domcontentloaded", timeout=30_000
                        )
                    assert updates.value.status == 200
                    screen = page.locator('[data-screen="vps"]')
                    expect(screen).to_be_visible(timeout=15_000)
                    expect(
                        screen.locator('[data-vps-metric="cpu"] .instrument-value')
                    ).to_contain_text("42", timeout=15_000)
                    expect(
                        screen.locator('[data-vps-metric="memory"] .instrument-value')
                    ).to_contain_text("5.1", timeout=15_000)
                    expect(
                        screen.locator('[data-vps-metric="disk"] .instrument-value')
                    ).to_contain_text("60", timeout=15_000)
                    row = screen.locator('[data-vps-row="processes"][data-vps-name="agent ) name"]')
                    expect(row).to_be_visible(timeout=15_000)
                    expect(row).to_contain_text("25% CPU", timeout=15_000)
                    expect(
                        screen.locator('[data-vps-row="services"][data-vps-name="panels.service"]')
                    ).to_contain_text("512.0 MB", timeout=15_000)
                    line = screen.locator('[data-vps-metric="cpu"] path.trend-line').first
                    expect(line).to_have_attribute("d", re.compile(r".*L.*"), timeout=15_000)
                    latest = page.request.get(handle.base + "/api/vps-metrics/latest").json()
                    assert latest["sequence"] == seeded["sequence"]
                    assert latest["collection"]["last_sample_at"] == seeded["timestamp"]
                    assert latest["sample"]["metrics"]["cpu_percent"]["value"] == 42
                    for method, path in [
                        ("GET", "/api/updates"),
                        ("GET", "/api/vps-metrics/latest"),
                        ("POST", "/api/vps-metrics/activity"),
                        ("GET", "/api/vps-metrics/processes"),
                        ("GET", "/api/vps-metrics/history"),
                    ]:
                        assert any(
                            item == {"method": method, "path": path, "status": 200}
                            for item in network
                        ), (method, path, network)
                    assert not page_errors, page_errors
                    page.screenshot(path=str(EVIDENCE / "part2-vps.png"), full_page=True)
                    evidence = {
                        "result": "passed",
                        "fixture": (
                            "21 Python-written samples from existing fake proc/sys/cgroup "
                            "collector. Injected filesystem/sysconf and scripted service queries. "
                            "No host metrics collection."
                        ),
                        "boundary": (
                            "Existing TS comparison server and built frontend in Chromium. "
                            "Real browser query, JSON decode, chart and workload rendering. "
                            "No request mocking."
                        ),
                        "owned_server_pid": handle.proc.pid,
                        "route": "#/vps",
                        "rendered_route": "#" + urlsplit(page.url).fragment,
                        "browser": browser.version,
                        "latest_sequence": latest["sequence"],
                        "cpu_percent": 42,
                        "process_row": row.inner_text(),
                        "chart_path": line.get_attribute("d"),
                        "api_responses": network,
                        "unsupported_requests": [item for item in network if item["status"] >= 400],
                        "page_errors": page_errors,
                    }
                    context.close()
                finally:
                    browser.close()
        finally:
            if handle is not None:
                _stop_server(handle)
                evidence["owned_server_stopped"] = handle.proc.poll() is not None
                evidence["owned_server_exit_code"] = handle.proc.returncode
            evidence["seconds"] = time.perf_counter() - began
            (EVIDENCE / "part2-browser.json").write_text(json.dumps(evidence, indent=2) + "\n")
