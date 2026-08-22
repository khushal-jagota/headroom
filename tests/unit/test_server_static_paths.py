"""Server static mounts do not depend on the caller's working directory."""

from __future__ import annotations

import struct
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.conversation.contracts import FLOOR_DEFAULT_WORKSPACE_FOLDER
from planner.core import server
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _png_size(payload: bytes) -> tuple[int, int]:
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", payload[16:24])


def test_worker_workspace_root_prefers_existing_projects_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    preferred_root = tmp_path / "projects"
    preferred_root.mkdir()
    monkeypatch.setattr(server, "_REPO_ROOT", repository_root)
    monkeypatch.setattr(server, "_PREFERRED_WORKER_WORKSPACE_ROOT", preferred_root)

    assert server.resolve_worker_workspace_root() == preferred_root.resolve()


def test_worker_workspace_root_falls_back_without_creating_missing_preference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    missing_preferred_root = tmp_path / "missing" / "projects"
    monkeypatch.setattr(server, "_REPO_ROOT", repository_root)
    monkeypatch.setattr(
        server, "_PREFERRED_WORKER_WORKSPACE_ROOT", missing_preferred_root
    )

    assert server.resolve_worker_workspace_root() == repository_root.resolve()
    assert not missing_preferred_root.exists()
    assert not missing_preferred_root.parent.exists()


def test_worker_workspace_root_falls_back_when_preference_is_not_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    preferred_file = tmp_path / "projects"
    preferred_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(server, "_REPO_ROOT", repository_root)
    monkeypatch.setattr(server, "_PREFERRED_WORKER_WORKSPACE_ROOT", preferred_file)

    assert server.resolve_worker_workspace_root() == repository_root.resolve()


def test_shared_workspace_defaults_name_the_projects_folder() -> None:
    expected = Path.home() / "projects"

    assert server._PREFERRED_WORKER_WORKSPACE_ROOT == expected
    assert FLOOR_DEFAULT_WORKSPACE_FOLDER == expected


def test_static_assets_are_served_when_cwd_has_no_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    monkeypatch.chdir(tmp_path)
    app = create_app(config, clock, conn_factory)
    with TestClient(app) as client:
        root = client.get("/")
        css = client.get("/assets/app.css")
        favicon = client.get("/static/favicon.ico")
        favicon_png = client.get("/static/favicon-32.png")
        apple_touch_icon = client.get("/static/apple-touch-icon.png")
        icon_192 = client.get("/static/icon-192.png")
        icon_512 = client.get("/static/icon-512.png")
        maskable_icon = client.get("/static/icon-maskable-512.png")
        manifest = client.get("/static/manifest.webmanifest")
        service_worker = client.get("/service-worker.js")

    assert root.status_code == 200
    assert "data-svelte-app" in root.text
    assert '<meta name="panels-app-sha" content="">' in root.text
    assert root.headers["cache-control"] == "no-cache, must-revalidate"
    assert css.status_code == 200
    assert ".ticket-page" in css.text
    assert favicon.status_code == 200
    assert favicon_png.status_code == 200
    assert apple_touch_icon.status_code == 200
    assert icon_192.status_code == 200
    assert icon_512.status_code == 200
    assert maskable_icon.status_code == 200
    assert _png_size(favicon_png.content) == (32, 32)
    assert _png_size(apple_touch_icon.content) == (180, 180)
    assert _png_size(icon_192.content) == (192, 192)
    assert _png_size(icon_512.content) == (512, 512)
    assert _png_size(maskable_icon.content) == (512, 512)
    assert '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png"' in root.text
    assert 'href="/static/favicon-32.png"' in root.text
    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["display"] == "standalone"
    assert manifest_payload["icons"] == [
        {
            "src": "/static/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/static/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/static/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ]
    assert manifest_payload["shortcuts"] == [
        {
            "name": "Chief of Staff",
            "short_name": "Chief",
            "description": "Open the Chief of Staff conversation.",
            "url": "/#/workspace/chief-of-staff",
            "icons": [
                {
                    "src": "/static/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                }
            ],
        }
    ]
    assert service_worker.status_code == 200
    assert service_worker.headers["content-type"].startswith("application/javascript")
    assert service_worker.headers["cache-control"] == "no-cache"
    assert "notificationclick" in service_worker.text
    assert "fetch" not in service_worker.text


def test_static_asset_paths_remain_anchored_to_repository_root() -> None:
    assert server._WEB_DIST == server._REPO_ROOT / "web" / "dist"
    assert server._ASSETS_DIR == server._REPO_ROOT / "assets"
    assert server._STATIC_DIR == server._REPO_ROOT / "static"


def test_root_document_contains_the_configured_boot_app_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = "0123456789abcdef0123456789abcdef01234567"
    index = tmp_path / "index.html"
    index.write_text("<html><head><title>Panels</title></head><body></body></html>")
    monkeypatch.setattr(server, "_WEB_INDEX", index)

    assert (
        '<meta name="panels-app-sha" content="0123456789abcdef0123456789abcdef01234567">'
        in server.svelte_index_html(sha)
    )
