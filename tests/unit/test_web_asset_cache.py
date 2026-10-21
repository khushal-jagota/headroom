"""Only immutable filenames can survive a release without revalidation."""

from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from planner.core.web_assets import WebAssets


def test_hashed_asset_cache_and_revalidation(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app-1234abcd.js").write_text("export default 1")
    (tmp_path / "assets/app.css").write_text("body{}")
    (tmp_path / "index.html").write_text("hello")
    client = TestClient(
        Starlette(routes=[Mount("/_app", WebAssets(directory=tmp_path, html=True))])
    )
    hashed = client.get("/_app/assets/app-1234abcd.js")
    assert hashed.headers["cache-control"] == "public, max-age=31536000, immutable"
    cached = client.get(
        "/_app/assets/app-1234abcd.js", headers={"If-None-Match": hashed.headers["etag"]}
    )
    assert cached.status_code == 304
    assert cached.headers["cache-control"] == hashed.headers["cache-control"]
    for path in ("/_app/index.html", "/_app/assets/app.css"):
        assert client.get(path).headers["cache-control"] == "no-cache, must-revalidate"
