"""Fetch the immediately preceding public home and the exact candidate Actions artifacts.

The installed previous client reads a local catalog with the normal release schema. Only the
catalog IDs and test repository are synthetic. Both revisions' archives stay byte-for-byte intact.
"""
import base64
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
PUBLISH_WORKFLOW = ".github/workflows/publish-typescript-release.yml"
SERVER_ASSET = "mac-home.zip" if sys.platform == "darwin" else "app.zip"
MAC = sys.platform == "darwin"
MACHINE_MEMBER = "panels-mac-runtime.tar.gz" if MAC else "panels-linux-executor.tar.gz"
MACHINE_RECEIPT = "mac-runtime-publication.json" if MAC else "linux-executor-publication.json"
PLATFORM = "darwin-arm64" if MAC else "linux-x86_64"


def github(path, to=None):
    media_type = "application/octet-stream" if path.startswith("releases/assets/") else "application/vnd.github+json"
    command = ["gh", "api", "-H", f"Accept: {media_type}", f"repos/{REPO}/{path}"]
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


def public_predecessor():
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
    if predecessor["target_commitish"] != old_sha:
        raise ValueError("Expected distinct predecessor and candidate revisions")
    server_asset = SERVER_ASSET
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
    return old_sha, {"release_id": predecessor["id"], "tag": predecessor["tag_name"],
                     "home_sha256": old_home["archive_sha256"], "machine_sha256": old_machine["archive_sha256"]}


def run_artifacts(run_id):
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("A native release workflow run ID is required")
    run = github(f"actions/runs/{run_id}")
    if (run["path"] != PUBLISH_WORKFLOW or run["head_repository"]["full_name"] != REPO
            or run["event"] not in ("pull_request", "push")):
        raise ValueError("Manual replay requires a build from this repository's release workflow")
    artifacts = github(f"actions/runs/{run_id}/artifacts?per_page=100")["artifacts"]
    names = (["panels-mac-home", "panels-mac-home-build", "panels-mac-runtime"]
             if MAC else ["panels-app", "panels-publication-build", "panels-linux-executor"])
    found = {a["name"]: a for a in artifacts if not a["expired"]}
    # The native boot receipts are emitted before their immutable ZIP IDs.
    return [str(found[name]["id"]) for name in names], run


def actions_build(ids, dest):
    metadata_name = "mac-home-build.json" if MAC else "linux-publication-build.json"
    for asset, artifact_id in zip([SERVER_ASSET, "metadata.zip", "machine.zip"], ids, strict=True):
        github(f"actions/artifacts/{artifact_id}/zip", dest / asset)
    extract(dest / "metadata.zip", metadata_name, dest / metadata_name)
    extract(dest / "metadata.zip", "boot-proof.json", dest / "boot-proof.json")
    extract(dest / "machine.zip", MACHINE_RECEIPT, dest / MACHINE_RECEIPT)
    build = json.loads((dest / metadata_name).read_bytes())
    machine = json.loads((dest / MACHINE_RECEIPT).read_bytes())
    boot = json.loads((dest / "boot-proof.json").read_bytes())
    home_digest = build["archive_sha256"] if MAC else build["archive"]["sha256"]
    if (boot["requested_sha"] != build["manifest"]["app_sha"] or boot["archive_sha256"] != home_digest
            or machine["application_revision"] != boot["requested_sha"] or not boot["health"]["ready"]):
        raise ValueError("Native boot and machine receipts must match the target")
    return build["manifest"], home_digest, machine, boot


def verify_run_commit(run, sha):
    commit = github(f"git/commits/{sha}")
    if sha != run["head_sha"] and not (run["event"] == "pull_request" and
            len(commit["parents"]) == 2 and commit["parents"][1]["sha"] == run["head_sha"]):
        raise ValueError("Native artifact revision is not the recorded run's tested merge")


def main():
    FEED.mkdir(parents=True)
    for part in ["server", "machine"]:
        (ROOT / "build" / part).mkdir(parents=True)
    manual = bool(os.environ.get("REPLAY_TO"))
    if (manual or os.environ.get("REPLAY_FROM")) and os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise ValueError("Only an explicit manual rehearsal can reuse another build")
    if os.environ.get("REPLAY_FROM"):
        if not manual:
            raise ValueError("A manual starting build requires a candidate run")
        baseline = ROOT / "baseline"
        baseline.mkdir()
        prior_ids, prior = run_artifacts(os.environ["REPLAY_FROM"])
        manifest, home_digest, machine, _ = actions_build(prior_ids, baseline)
        old_sha = manifest["app_sha"]
        verify_run_commit(prior, old_sha)
        prior_evidence = {"native_build_run": prior["id"], "artifact_ids": prior_ids,
                          "home_sha256": home_digest, "machine_sha256": machine["archive_sha256"]}
        for archive, part, member, expected in [
                (SERVER_ASSET, "server", "panels-app.tar.gz", home_digest),
                ("machine.zip", "machine", MACHINE_MEMBER, machine["archive_sha256"])]:
            dest = ROOT / "build" / part / member
            extract(baseline / archive, member, dest)
            if digest(dest) != expected:
                raise ValueError("Historical package differs from its native receipt")
            (baseline / archive).unlink()
        metadata = "mac-home-build.json" if MAC else "linux-publication-build.json"
        (baseline / metadata).rename(ROOT / "build/server" / metadata)
        (baseline / MACHINE_RECEIPT).rename(ROOT / "build/machine" / MACHINE_RECEIPT)
    else:
        old_sha, prior_evidence = public_predecessor()
    if manual:
        ids, candidate_run = run_artifacts(os.environ["REPLAY_TO"])
    else:
        ids = [os.environ[name] for name in ["HOME_ARTIFACT_ID", "HOME_BUILD_ARTIFACT_ID", "MACHINE_ARTIFACT_ID"]]
    manifest, home_digest, machine, boot = actions_build(ids, FEED)
    sha = manifest["app_sha"]
    if manual:
        verify_run_commit(candidate_run, sha)
        # The harness can diagnose an earlier build without rebuilding or borrowing policy from a new checkout.
        policy_root = ROOT / "candidate-policy"
        policy_root.mkdir()
        source_archive = ROOT / "candidate-source.tar.gz"
        github(f"tarball/{sha}", source_archive)
        subprocess.run(["tar", "-xzf", source_archive, "--strip-components=1", "-C", policy_root], check=True)
        source_archive.unlink()
    elif sha != os.environ["GITHUB_SHA"]:
        raise ValueError("Candidate build differs from workflow commit")
    if sha == old_sha:
        raise ValueError("Distinct installed revisions are required")
    script = github(f"contents/scripts/panels_install.py?ref={old_sha}")
    (ROOT / "predecessor-install.py").write_bytes(base64.b64decode(script["content"]))
    machine_asset = "mac.zip" if MAC else "linux-executor.zip"
    app = {"manifest": manifest, "archive_sha256": home_digest, "artifact_id": 2,
           "artifact_source": "github-release"}
    source = {"workflow_path": PUBLISH_WORKFLOW, "event": "push", "ref": "refs/heads/main",
              "repository": "qualification/panels", "source_sha": sha,
              "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
    source["workflow_ref"] = "qualification/panels/" + source["workflow_path"] + "@refs/heads/main"
    policy = json.loads(subprocess.check_output([
        "node", "--input-type=module", "-e",
        "import {CLIENT_COMPATIBILITY} from './server/client_versions/policy.ts';"
        "import {SUPPORTED_UPDATE_SCHEMA_FLOOR} from './server/environments/updates/configuration.ts';"
        "console.log(JSON.stringify({compatibility:CLIENT_COMPATIBILITY,schema_floor:SUPPORTED_UPDATE_SCHEMA_FLOOR}));"
    ], cwd=policy_root if manual else None))
    target = {**app, **policy, "publication_run_id": int(source["run_id"]), "expires_at": None,
              "source_run": source,
              "parts": {"server": {"app_sha": sha, "artifact_digest": manifest["artifact_digest"]},
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
    tag = f'server-{sha}-{source["run_id"]}-{source["run_attempt"]}'
    (FEED / "releases.json").write_text(json.dumps([{"tag_name": tag, "target_commitish": sha,
        "published_at": "2026-01-01T00:00:00Z", "draft": False, "prerelease": False, "assets": assets}]))
    (FEED / "publication.json").write_text(json.dumps(target))
    (ROOT / "predecessor.sha").write_text(old_sha)
    (ROOT / "candidate.sha").write_text(sha)
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "qualification-inputs.json").write_text(json.dumps({
        "mode": "manual rehearsal" if manual else "public predecessor release gate",
        "platform": PLATFORM, "harness_sha": os.environ["GITHUB_SHA"],
        "predecessor": {"sha": old_sha, **prior_evidence},
        "candidate": {"sha": sha, "native_build_run": os.environ.get("REPLAY_TO") or os.environ["GITHUB_RUN_ID"],
                      "artifact_ids": ids, "home_sha256": home_digest, "machine_sha256": machine["archive_sha256"]}
    }, indent=2))
    print(f"Predecessor {old_sha}, candidate {sha}, home {home_digest}, machine {machine['archive_sha256']}")


if __name__ == "__main__":
    main()
