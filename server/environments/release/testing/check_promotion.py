"""Select the immutable packages which qualified the pull request that reached main."""
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import zipfile


ARTIFACTS = {
    "app": "panels-app", "build": "panels-publication-build",
    "mac": "panels-mac-runtime", "linux-executor": "panels-linux-executor",
    "mac-home": "panels-mac-home", "mac-home-build": "panels-mac-home-build",
    "machine-compatibility-linux": "panels-machine-compatibility-linux",
    "machine-compatibility-darwin": "panels-machine-compatibility-darwin",
}


def read_api(repository, path, *, binary=False):
    data = subprocess.check_output(["gh", "api", f"repos/{repository}/{path}"])
    return data if binary else json.loads(data)


def artifact_id(artifacts, name):
    matches = [a for a in artifacts if a["name"] == name and not a["expired"]]
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous qualification artifact {name}")
    return matches[0]["id"]


def packages(artifacts):
    return {key: artifact_id(artifacts, name) for key, name in ARTIFACTS.items()}


def record():
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    run = os.environ["GITHUB_RUN_ID"]
    artifacts = read_api(repo, f"actions/runs/{run}/artifacts?per_page=100")["artifacts"]
    pull = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    return {"source_sha": sha, "head": pull["pull_request"]["head"]["sha"], "pr": pull["number"],
            "tree": read_api(repo, f"git/commits/{sha}")["tree"]["sha"], "run": run,
            "attempt": os.environ["GITHUB_RUN_ATTEMPT"], "artifacts": packages(artifacts)}


def main():
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    ref = ".github/workflows/publish-typescript-release.yml"
    pull = next((p for p in read_api(repo, f"commits/{sha}/pulls?per_page=100")
                 if p["merged_at"] and p["base"]["ref"] == "main" and
                 p["merge_commit_sha"] == sha and p["base"]["repo"]["full_name"] == repo
                 and p["head"]["repo"]["full_name"] == repo), None)
    if not pull:
        raise ValueError("The main commit must have entered through a merged release PR")
    head = pull["head"]["sha"]
    runs = read_api(repo, f"actions/workflows/publish-typescript-release.yml/runs?event=pull_request&head_sha={head}&per_page=100")
    run = next((r for r in runs["workflow_runs"] if r["path"] == ref
                and r["head_branch"] == pull["head"]["ref"]
                and r["head_repository"]["full_name"] == repo
                and (not r["pull_requests"] or any(
                    pr["number"] == pull["number"] for pr in r["pull_requests"]))), None)
    if not run or run["conclusion"] != "success":
        raise ValueError("The latest main PR qualification did not succeed")
    attempt = run["run_attempt"]
    artifacts = read_api(repo, f"actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]

    receipt = artifact_id(artifacts, f"qualified-merge-tree-{attempt}")
    with zipfile.ZipFile(io.BytesIO(read_api(repo, f"actions/artifacts/{receipt}/zip", binary=True))) as archive:
        proof = json.loads(archive.read("qualified.json"))
    candidate = proof.get("source_sha", "")
    if not re.fullmatch("[0-9a-f]{40}", candidate):
        raise ValueError("Missing qualified commit")
    tree = read_api(repo, f"git/commits/{sha}")["tree"]["sha"]
    candidate_tree = read_api(repo, f"git/commits/{candidate}")["tree"]["sha"]
    expected = {"tree": tree, "source_sha": candidate, "pr": pull["number"], "head": head,
                "run": str(run["id"]), "attempt": str(attempt), "artifacts": packages(artifacts)}
    if candidate_tree != tree or proof != expected:
        raise ValueError("The qualified PR tree differs from this publication")
    return {**proof, "branch": pull["head"]["ref"]}


if __name__ == "__main__":
    if sys.argv[1:] == ["record"]:
        print(json.dumps(record()))
    else:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("qualified=" + json.dumps(main(), separators=(",", ":")), file=output)
