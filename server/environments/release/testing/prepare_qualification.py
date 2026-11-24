"""Fetch the immediately preceding public home and the exact candidate Actions artifacts.

The installed previous client reads a local catalog with the normal release schema. Only the
catalog IDs and test repository are synthetic. Both revisions' archives stay byte-for-byte intact.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(os.environ["FIRST_INSTALL_ROOT"])
FEED = ROOT / "feed"
REPO = os.environ["GITHUB_REPOSITORY"]
SHA = os.environ["GITHUB_SHA"]
MAC = sys.platform == "darwin"
MACHINE_MEMBER = "panels-mac-runtime.tar.gz" if MAC else "panels-linux-executor.tar.gz"
MACHINE_RECEIPT = "mac-runtime-publication.json" if MAC else "linux-executor-publication.json"
PLATFORM = "darwin-arm64" if MAC else "linux-x86_64"


def github(path, to=None):
    command = ["gh", "api", "-H", "Accept: application/octet-stream", f"repos/{REPO}/{path}"]
    if to is None:
        return json.loads(subprocess.check_output(command))
    with to.open("xb") as dest:
        subprocess.run(command, check=True, stdout=dest)


def extract(archive, name, dest):
    with zipfile.ZipFile(archive) as source:
        if source.namelist().count(name) != 1:
            raise ValueError(f"Expected one {name} in {archive.name}")
        with source.open(name) as member, dest.open("xb") as out:
            shutil.copyfileobj(member, out)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    FEED.mkdir(parents=True)
    for part in ["server", "machine"]:
        (ROOT / "build" / part).mkdir(parents=True)
    releases = []
    page = 1
    while True:
        batch = github(f"releases?per_page=100&page={page}")
        releases.extend(item for item in batch if not item["draft"] and not item["prerelease"]
                        and re.fullmatch(r"server-[a-f0-9]{40}-\d+-\d+", item["tag_name"]))
        if len(batch) < 100:
            break
        page += 1
    predecessor = max(releases, key=lambda item: item["published_at"])
    ids = {item["name"]: item["id"] for item in predecessor["assets"]}
    receipt = github(f"releases/assets/{ids['publication.json']}")
    old_sha = receipt["source_run"]["source_sha"]
    if old_sha == SHA or predecessor["target_commitish"] != old_sha:
        raise ValueError("Expected distinct predecessor and candidate revisions")
    server_asset = "mac-home.zip" if MAC else "app.zip"
    machine_asset = "mac.zip" if MAC else "linux-executor.zip"
    old_home = receipt["mac_home"] if MAC else receipt
    old_machine = receipt["mac" if MAC else "linux_executor"]
    for public_name, info, part, member in [
        (server_asset, old_home, "server", "panels-app.tar.gz"),
        (machine_asset, old_machine, "machine", MACHINE_MEMBER),
    ]:
        if info["artifact_id"] != ids[public_name]:
            raise ValueError("Predecessor asset ID differs from its release")
        path = ROOT / public_name
        github(f"releases/assets/{ids[public_name]}", path)
        archive = ROOT / "build" / part / member
        extract(path, member, archive)
        if digest(archive) != info["archive_sha256"]:
            raise ValueError("Predecessor package differs from its release")
        if part == "machine":
            extract(path, MACHINE_RECEIPT, ROOT / "build/machine" / MACHINE_RECEIPT)
        path.unlink()
    # The proven first-install harness reads the original platform build metadata.
    metadata_name = "mac-home-build.json" if MAC else "linux-publication-build.json"
    old_build_zip = ROOT / "predecessor-build.zip"
    github(f"releases/assets/{ids['mac-home-build.zip' if MAC else 'build.zip']}", old_build_zip)
    extract(old_build_zip, metadata_name, ROOT / "build/server" / metadata_name)
    old_build_zip.unlink()
    old_script = ROOT / "predecessor-install.py"
    with old_script.open("xb") as stream:
        subprocess.run(["git", "show", old_sha + ":scripts/panels_install.py"], stdout=stream, check=True)

    # The GitHub artifact IDs come from this workflow's build outputs (not a search by branch).
    github("actions/artifacts/" + os.environ["HOME_ARTIFACT_ID"] + "/zip", FEED / server_asset)
    github("actions/artifacts/" + os.environ["MACHINE_ARTIFACT_ID"] + "/zip", FEED / "machine.zip")
    meta = ROOT / "candidate-build.zip"
    github("actions/artifacts/" + os.environ["HOME_BUILD_ARTIFACT_ID"] + "/zip", meta)
    extract(meta, metadata_name, ROOT / metadata_name)
    extract(meta, "boot-proof.json", ROOT / "boot-proof.json")
    boot = json.loads((ROOT / "boot-proof.json").read_text())
    build = json.loads((ROOT / metadata_name).read_text())
    manifest = build["manifest"]
    if manifest["app_sha"] != SHA:
        raise ValueError("Candidate build differs from workflow commit")
    home_digest = build["archive_sha256"] if MAC else build["archive"]["sha256"]
    extract(FEED / "machine.zip", MACHINE_RECEIPT, ROOT / MACHINE_RECEIPT)
    machine = json.loads((ROOT / MACHINE_RECEIPT).read_text())
    if boot["requested_sha"] != SHA or boot["archive_sha256"] != home_digest or machine["application_revision"] != SHA:
        raise ValueError("Native boot and machine receipts must match the target")
    app = {"manifest": manifest, "archive_sha256": home_digest, "artifact_id": 2,
           "artifact_source": "github-release"}
    source = {**receipt["source_run"], "repository": "qualification/panels", "source_sha": SHA,
              "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
    source["workflow_ref"] = "qualification/panels/" + source["workflow_path"] + "@refs/heads/main"
    target = {**app, "publication_run_id": int(source["run_id"]), "expires_at": None,
              "source_run": source, "schema_floor": build.get("schema_floor", receipt["schema_floor"]),
              "parts": {"server": {"app_sha": SHA, "artifact_digest": manifest["artifact_digest"]},
                        "screens": {"sha256": boot["screens_sha256"]},
                        "machines": {PLATFORM: {"archive_sha256": machine["archive_sha256"],
                                                "build_id": machine["build_id"]}}},
              "mac" if MAC else "linux_executor": {"artifact_id": 3, "artifact_source": "github-release",
                        "archive_sha256": machine["archive_sha256"], "archive_size": machine["archive_size"]}}
    assets = [{"name": "publication.json", "id": 1}, {"name": "app.zip", "id": 2},
              {"name": machine_asset, "id": 3}]
    if MAC:
        assets.append({"name": "mac-home.zip", "id": 4})
        target["mac_home"] = {**app, "artifact_id": 4}
    tag = f'server-{SHA}-{source["run_id"]}-{source["run_attempt"]}'
    (FEED / "releases.json").write_text(json.dumps([{"tag_name": tag, "target_commitish": SHA,
        "published_at": "2026-01-01T00:00:00Z", "draft": False, "prerelease": False, "assets": assets}]))
    (FEED / "publication.json").write_text(json.dumps(target))
    (ROOT / "predecessor.sha").write_text(old_sha)
    print(f"Predecessor {old_sha}, candidate {SHA}, home {home_digest}, machine {machine['archive_sha256']}")


if __name__ == "__main__":
    main()
