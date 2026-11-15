"""The next release is downloaded to disk, checked once and staged beside the live app."""

from __future__ import annotations

import zipfile
from pathlib import Path

import httpx
import pytest
from tests.support.update_apps import SHA_B, build_app, release

from planner.environments import update_provider
from planner.environments.app import AppManifest, validate_app_manifest
from planner.environments.update_artifacts import create_app_archive
from planner.environments.update_contracts import PublishedUpdate
from planner.environments.update_prefetch import stage_release


class ZipProvider:
    def __init__(self, artifact: Path) -> None:
        self.artifact = artifact
        self.downloads: list[Path] = []

    def available(self) -> PublishedUpdate | None:
        return None

    def download_artifact(self, artifact_id: int, destination: Path) -> None:
        self.downloads.append(destination)
        destination.write_bytes(self.artifact.read_bytes())


@pytest.fixture
def published(tmp_path: Path) -> tuple[ZipProvider, PublishedUpdate]:
    app = build_app(tmp_path / "built", SHA_B, "new")
    manifest = validate_app_manifest(app / "manifest.json", require_runtime=True)
    archive = tmp_path / "panels-app.tar.gz"
    checksum = create_app_archive(app, archive)
    artifact = tmp_path / "artifact.zip"
    with zipfile.ZipFile(artifact, "w") as zipped:
        zipped.write(archive, "panels-app.tar.gz")
    return ZipProvider(artifact), release(manifest, archive_sha256=checksum)


def test_stage_keeps_one_checked_release_and_no_download(
    tmp_path: Path, published: tuple[ZipProvider, PublishedUpdate]
) -> None:
    """The staged folder is the release's own manifest, admitted, with the zip removed."""
    provider, receipt = published
    current = tmp_path / "current"
    (current / ".app-next-old").mkdir(parents=True)
    (current / ".app-download-left-by-a-stopped-server").mkdir()
    admitted: list[AppManifest] = []
    staged = stage_release(
        provider, receipt, current, admit=lambda root, manifest: admitted.append(manifest)
    )
    assert staged == current / f".app-next-{SHA_B}" and (staged / "new").is_file()
    assert admitted == [receipt.manifest]
    assert provider.downloads[0].parent.parent == current  # same filesystem as the app
    assert sorted(path.name for path in current.iterdir()) == [staged.name]
    # A restart finds the staged release without downloading it again.
    assert stage_release(provider, receipt, current, admit=lambda root, manifest: None) == staged
    assert len(provider.downloads) == 1


@pytest.mark.parametrize("change", ["checksum", "manifest", "admission"])
def test_stage_refuses_a_release_that_differs_from_its_receipt(
    tmp_path: Path, published: tuple[ZipProvider, PublishedUpdate], change: str
) -> None:
    """A wrong checksum, manifest or runtime leaves nothing staged and nothing behind."""
    provider, receipt = published
    if change == "checksum":
        receipt = release(receipt.manifest, archive_sha256="0" * 64)
    if change == "manifest":
        receipt = release(AppManifest(SHA_B, "1" * 64, "2" * 64, receipt.manifest.runtime_contract))

    def admit(root: Path, manifest: AppManifest) -> None:
        if change == "admission":
            raise ValueError("runtime probe failed")

    current = tmp_path / "current"
    current.mkdir()
    with pytest.raises(ValueError):
        stage_release(provider, receipt, current, admit=admit)
    assert list(current.iterdir()) == []


def test_provider_streams_the_artifact_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 1 GB artifact goes straight to a new file, in chunks, never one response body."""
    body = b"x" * (3 * 1024 * 1024 + 7)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/zip"):
            return httpx.Response(200, stream=httpx.ByteStream(body))
        return httpx.Response(200, json={"expired": False})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    token = tmp_path / "token"
    token.write_text("secret\n")
    provider = update_provider.GitHubUpdateProvider("owner/panels", token)
    destination = tmp_path / "artifact.zip"
    provider.download_artifact(5, destination)
    assert destination.read_bytes() == body
    with pytest.raises(FileExistsError):
        provider.download_artifact(5, destination)
