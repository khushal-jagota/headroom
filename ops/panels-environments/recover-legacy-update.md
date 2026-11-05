# Recover an accepted legacy owner update

Use only after operator approval, with this repair merged to `main`. Do not edit
request/lifecycle JSON or restart the service by hand. This path is for an old
server which accepted an Update now claim but whose runner rejected its receipt
before shutdown. Ordinary adoption remains unchanged.

Recovery requires all of these together: the original confirmed Update now request
is still `applying` without attempt/deadline fields, its pinned publication remains
trusted and retained, the matching lifecycle is `unknown` with
`admission_evidence_uncertain` and no serving proof, the old app still passes its
exact-SHA health check, and the live status confirms work is paused on that target.
Changed evidence refuses recovery before service shutdown or recovery-record writes.

## Run the existing isolated adoption job

From an operator terminal, save the current request as evidence and pass its exact
pins. The workflow checks out current `main` for the repaired control code, but
installs the **originally approved publication**, not a newly selected release.
For the November 5 incident the failed attempt is `42063206591-1`, and the prior SHA
is `64a86a47ba871edce9a31300a23d19d256eb1764`.

```sh
receipt=/absolute/task-evidence/owner-update-request.json
ssh root@panels-vps \
  'cat /home/vps/Deployments/Panels/current/data/owner-update-request.json' > "$receipt"

gh-axi workflow run deploy.yml --repo khushal-jagota/headroom --ref main \
  --field operation=recover-legacy --field rehearsal=false \
  --field failed_deployment_id=42063206591-1 \
  --field baseline_sha=64a86a47ba871edce9a31300a23d19d256eb1764 \
  --field "request_id=$(jq -er .request_id "$receipt")" \
  --field "publication_run_id=$(jq -er .release.publication_run_id "$receipt")" \
  --field "artifact_id=$(jq -er .release.artifact_id "$receipt")" \
  --field "proof_artifact_id=$(jq -er .release.proof_artifact_id "$receipt")" \
  --field "commit_sha=$(jq -er .release.manifest.app_sha "$receipt")" \
  --field "artifact_digest=$(jq -er .release.manifest.artifact_digest "$receipt")" \
  --field "archive_sha256=$(jq -er .release.archive_sha256 "$receipt")"

gh-axi run list --repo khushal-jagota/headroom --workflow deploy.yml --event workflow_dispatch
# Read the new recovery run id from the result, then:
gh-axi run view RECOVERY_RUN_ID --repo khushal-jagota/headroom --log
```

The job uses the existing production-deploy concurrency group, separate installer
user service, artifact proof, runtime and schema admission, backup, hard cutover,
health commit, rollback, skill provisioning, published-route check, and finalizer.
It does not build a new release, bypass admission, or send a second claim to the old
server. The deployment lock covers the final evidence checks and recovery binding.
A fresh attempt id and finite deadline are recorded through canonical writers.
If a check or write fails, stop and inspect the receipts; do not repeat the command
with altered records. A failure after the health commit never restores the database.

## Verify and then request a newer release, if approved

```sh
ssh root@panels-vps '
  curl -fsS http://127.0.0.1:8767/api/meta
  curl -fsS http://127.0.0.1:8767/api/deployment-status
  curl -fsS http://127.0.0.1:8767/api/goals > /dev/null
'
```

Require the original pinned SHA in `/api/meta`, successful deployment status,
`installation_confirmed=true`, `work_paused=false`, and ordinary API success. A
failed skill or route check can leave the app installed with an incomplete job;
report that distinction. Check the web app separately in an agent-owned background
browser; never use the owner's open browser tab as an automation surface.

If the owner has also approved current `main`, wait for its successful publication
and request that release through the normal owner API. Do not retarget the accepted
legacy request. Save a fresh `GET /api/updates` response, require its available SHA
to equal the approved merged SHA, and construct the request from those offered pins:

```python
import httpx

base = 'http://127.0.0.1:8767'
approved_sha = 'FULL_APPROVED_MERGED_MAIN_SHA'
with httpx.Client(timeout=60) as client:
    status = client.get(base + '/api/updates')
    status.raise_for_status()
    release = status.json()['available']
    assert release is not None and release['manifest']['app_sha'] == approved_sha
    body = {key: release[key] for key in (
        'publication_run_id', 'artifact_id', 'proof_artifact_id', 'archive_sha256'
    )}
    body.update(app_sha=approved_sha, artifact_digest=release['manifest']['artifact_digest'],
                mode='now', confirm_activity=True)
    response = client.post(base + '/api/updates/requests', json=body)
    response.raise_for_status()
    print(response.json()['state'])
```

Run that script on the VPS as the operator, only after recovery succeeded and the
new publication is available. Track the resulting ordinary adoption job, then
repeat exact-SHA health, deployment-status, ordinary API, and background web checks
against the approved merged SHA. Do not call publication success installation proof.
