"""Require the merged tree's successful main PR qualification before publishing a main push."""
import io
import json
import os
import subprocess
import zipfile


def read_api(repository, path, *, binary=False):
    args = ["gh", "api", f"repos/{repository}/{path}"]
    data = subprocess.check_output(args)
    return data if binary else json.loads(data)


def main():
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    ref = ".github/workflows/publish-typescript-release.yml"
    pull = next((p for p in read_api(repo, f"commits/{sha}/pulls?per_page=100")
                 if p["merged_at"] and p["base"]["ref"] == "main" and
                 p["merge_commit_sha"] == sha and p["base"]["repo"]["full_name"] == repo), None)
    if not pull:
        raise ValueError("The main commit must have entered through a merged PR")
    head = pull["head"]["sha"]
    runs = read_api(repo, f"actions/workflows/publish-typescript-release.yml/runs?event=pull_request&head_sha={head}&per_page=100")
    run = next((r for r in runs["workflow_runs"] if r["path"] == ref and any(
        pr["number"] == pull["number"] for pr in r["pull_requests"])), None)
    if not run or run["conclusion"] != "success":
        raise ValueError("The latest main PR qualification did not succeed")
    attempt = run["run_attempt"]
    artifacts = read_api(repo, f"actions/runs/{run['id']}/artifacts?per_page=100")
    name = f"qualified-merge-tree-{attempt}"
    receipt = next((a for a in artifacts["artifacts"] if a["name"] == name and not a["expired"]), None)
    if receipt is None:
        raise ValueError("Missing immutable release gate receipt")
    with zipfile.ZipFile(io.BytesIO(read_api(repo, f"actions/artifacts/{receipt['id']}/zip", binary=True))) as archive:
        proof = json.loads(archive.read("qualified.json"))
    tree = read_api(repo, f"git/commits/{sha}")["tree"]["sha"]
    if proof != {"tree": tree, "pr": pull["number"], "head": head, "run": str(run["id"]), "attempt": str(attempt)}:
        raise ValueError("The qualified PR tree differs from this publication")
    print(f"Matched PR {pull['number']} run {run['id']}/{attempt} to tree {tree}")


if __name__ == "__main__":
    main()
