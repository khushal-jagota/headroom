"""Prepare an isolated OCI root without a container daemon or host package changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
import urllib.request
from pathlib import Path

TARGET_REPOSITORY = "library/node"
TARGET_IMAGE_DIGEST = "sha256:b0ea3309e53992217c31c10aea3ef3ce47c3fffba7416a1e4a5a60b60373b1ed"
TARGET_ROOT_INVENTORY_SHA256 = "b198dbf848cfcaf11c61ecf3eee475aeaf5acfdaae94c1edbd94e2358dd7d9c2"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def root_inventory(root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        item: dict[str, object] = {
            "path": path.relative_to(root).as_posix(),
            "mode": stat.S_IMODE(path.lstat().st_mode),
        }
        if path.is_symlink():
            item["link"] = os.readlink(path)
        elif path.is_file():
            item["sha256"] = file_hash(path)
        elif path.is_dir():
            item["directory"] = True
        else:
            raise ValueError(f"unsupported target root entry: {path}")
        result.append(item)
    return result


def inventory_digest(inventory: list[dict[str, object]]) -> str:
    return hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def apply_layer(layer: Path, root: Path) -> None:
    """Apply whiteouts and keep all extracted links within the isolated root."""
    with tarfile.open(layer, "r:*") as bundle:
        members = bundle.getmembers()
        for member in members:
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe OCI layer path")
            target = root / relative
            if relative != Path(".") and not target.parent.resolve().is_relative_to(root.resolve()):
                raise ValueError("OCI layer path leaves its root")
            if relative.name == ".wh..wh..opq":
                for child in target.parent.iterdir():
                    _remove(child)
            elif relative.name.startswith(".wh."):
                _remove(target.with_name(relative.name[4:]))
        for member in members:
            if Path(member.name).name.startswith(".wh."):
                continue
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError("unsupported OCI layer entry")
            if member.issym() and member.linkname.startswith("/"):
                # Absolute links inside a rootfs become equivalent relative links.
                target = root / member.linkname.lstrip("/")
                member.linkname = os.path.relpath(target, (root / member.name).parent)
            checked = tarfile.data_filter(member, str(root))
            if checked is None:
                continue
            checked.mode = member.mode & 0o1777
            bundle.extract(checked, root, filter=lambda entry, _destination: entry)


def pull_oci_root(repository: str, digest: str, root: Path) -> dict[str, object]:
    """Pull an exact Docker Hub platform manifest into a new root."""
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError("an exact platform manifest SHA256 is required")
    root.mkdir(parents=True, exist_ok=False)
    token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repository}:pull"
    with urllib.request.urlopen(token_url, timeout=60) as response:
        token = json.load(response)["token"]

    def download(kind: str, identity: str, destination: Path) -> None:
        request = urllib.request.Request(
            f"https://registry-1.docker.io/v2/{repository}/{kind}/{identity}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": (
                    "application/vnd.oci.image.manifest.v1+json, "
                    "application/vnd.docker.distribution.manifest.v2+json"
                ),
            },
        )
        with (
            urllib.request.urlopen(request, timeout=120) as response,
            destination.open("xb") as output,
        ):
            shutil.copyfileobj(response, output)
        if "sha256:" + file_hash(destination) != identity:
            raise ValueError("OCI object hash mismatch")

    cache = root.parent / (root.name + "-oci")
    cache.mkdir(exist_ok=False)
    manifest_path = cache / "manifest.json"
    download("manifests", digest, manifest_path)
    manifest = json.loads(manifest_path.read_text())
    config_path = cache / "config.json"
    download("blobs", manifest["config"]["digest"], config_path)
    config = json.loads(config_path.read_text())
    if (config.get("os"), config.get("architecture")) != ("linux", "amd64"):
        raise ValueError("target image must be Linux amd64")
    for index, layer in enumerate(manifest["layers"]):
        path = cache / f"layer-{index}.tar.gz"
        download("blobs", layer["digest"], path)
        apply_layer(path, root)
    return {
        "repository": repository,
        "image_digest": digest,
        "manifest": manifest,
        "config": config,
    }


def prepare_target(root: Path) -> Path:
    source = pull_oci_root(TARGET_REPOSITORY, TARGET_IMAGE_DIGEST, root)
    # Node is the only non-system external executable. Remove package installers.
    for relative in (
        "usr/local/lib/node_modules",
        "opt/yarn-v1.22.22",
        "usr/local/bin/npm",
        "usr/local/bin/npx",
        "usr/local/bin/corepack",
        "usr/local/bin/yarn",
        "usr/local/bin/yarnpkg",
        "usr/bin/apt",
        "usr/bin/apt-get",
        "usr/bin/apt-cache",
        "usr/bin/apt-cdrom",
        "usr/bin/apt-config",
        "usr/bin/apt-mark",
        "usr/bin/dpkg",
        "usr/bin/dpkg-deb",
        "usr/bin/dpkg-query",
        "usr/bin/dpkg-split",
        "usr/bin/dpkg-trigger",
        "var/lib/apt/lists",
        "var/cache/apt",
        "root/.npm",
    ):
        _remove(root / relative)
    for path in (root / "opt").glob("yarn-*"):
        _remove(path)
    (root / "usr/bin/node").symlink_to("../local/bin/node")
    for name in ("input", "evidence", "work"):
        (root / name).mkdir(exist_ok=True)
        (root / name).chmod(0o755)
    inventory = root_inventory(root)
    if inventory_digest(inventory) != TARGET_ROOT_INVENTORY_SHA256:
        raise ValueError("prepared target root differs from its pinned final inventory")
    document = {
        **source,
        "root_inventory_sha256": inventory_digest(inventory),
        "inventory": inventory,
    }
    receipt = root.parent / (root.name + "-inventory.json")
    receipt.write_text(json.dumps(document, sort_keys=True) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(prepare_target(args.root.resolve()))


if __name__ == "__main__":
    main()
