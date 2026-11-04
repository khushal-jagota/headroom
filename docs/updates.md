# Updates

An update is the owner installing a release that publication already sealed. Merging to `main` publishes. It does not change the running workspace. The deployment runner installs a release only after an owner request, and only that pinned release. The mechanics of the archive, the health budgets, and the restore boundary are [Deployment](deployment.md).

```text
retained release → owner chooses now or idle → durable pinned request
                                                  ↓
                          runner checks archive and schema → replace app
                                                  ↓
                               local app healthy → post-health result
```

## The owner actions

The shell offers **Update when idle** and **Start update** when a usable release is waiting. Start update is the immediate install. If the workspace is active, or if activity cannot be known, the screen asks for an explicit confirmation: work is active and the update can interrupt it, or activity is unknown and the update can interrupt work. Proceed with update continues. Active turns can stop. Existing interruption records account for stopped work. They do not replay an uncertain send.

Update when idle waits until workspace activity is known and idle. While it waits, the shell says the update waits for idle. The existing readiness loop notices the request. There is no second scheduler. The request stays requested until that idle check permits dispatch. Dispatch changes it to installing before the external call, so an uncertain response cannot replay it.

Cancel update cancels a requested update before dispatch. The request becomes failed with a cancellation detail that says the previous version stayed active. The archive keeps at most 32 canceled requests for reports. Once dispatch starts installation, cancellation reports that application already began. Requested and installing requests block a new request. Done and failed requests permit a new selection. An expired release has no retry action.

Each request pins the publication, the archive, the proof, the source SHA, and the digests. It never slides forward to a newer commit. A missing or expired publication needs a new selection. The owner can pick a newer retained release directly and skip the ones in between.

The request record lives outside database snapshots. Restoring a database does not replay an install. A failed or uncertain dispatch does not retry itself. The owner looks at the runner evidence and asks again if they still want it.

A local done result means the app is installed. Deployment status separately reports whether skill reconciliation and the published HTTP/2 check finished. Those can fail and leave the new app in place. A failed skill or route check does not mean the app is unhealthy. Missing admission or installation evidence stays an explicit uncertainty. The finalizer reports each of those checks apart from application health, and a stale finalizer cannot rewrite a replacement attempt, even when two requests name the same SHA. The browser offers a refresh when the app it loaded differs from the server. That refresh does not replace the push service worker by itself.

## Who can ask, and what the runner will accept

Update actions use the owner principal check. Chief, Managers, and Ticket Workers are refused. A missing actor header is treated as the owner under the current trusted-caller contract. That is not spoof-resistant authentication. It adds no login. Workers receive no dispatch credential and no restart operation.

The operator supplies `PLAN_UPDATE_REPOSITORY` as `owner/repository` and `PLAN_UPDATE_TOKEN_FILE` as a protected file holding the dispatch credential. Without them, Headroom says owner updates are unavailable. It does not invent a release. The dispatch credential is separate from the runner’s read-only workflow token. Only the manual adoption operation on `main`, without the rehearsal flag, selects the production runner. Pushes run publication on hosted machines.

The owner API selects from the provider's successful publications on main. The runner reads that exact request, downloads its archive, and checks its fingerprint once. It checks the candidate migration history against the live database once under the exclusive deploy lock. It does not ask the live server to claim the install.

The supported schema floor is `video_upload_ledger`. Adoption refuses an unknown ancestry, or a database older than that floor, before it stops the service. The floor is not a promise that every historical database is safe to migrate. Cancelled-Ticket recovery in [Deployment](deployment.md) still applies. New candidates also need the runtime contract in [Archive runtime](archive-runtime.md). A legacy installed app can remain the fallback. A legacy candidate without the contract cannot be installed. It has to be republished.

## If the runner is lost

Before the local commit, writers are stopped and ordinary work stays closed. Restore is possible only in that window. After the commit, a failed status publication does not restore the database.

If the runner vanishes before commit, work stays closed. The status file retains its last recorded step until the next deploy replaces it. No expiry opens the app, restores a snapshot, or triggers a restart. Panels sends no unfinished-deploy alert.

If the recorded commit matches the running app and admission is open, the screen confirms that the app is installed and available. An unconfirmed final report needs no owner action. Its notice ends five minutes after the last status update or an explicit unknown report. Refresh does not reset that deadline. The status file retains the evidence. Holds, failures, rollbacks, and revision mismatches never disappear through this deadline.

## Where it shows up

The actions sit in the shell status pill, not on a Settings page. Deployment outcome text also appears in the smaller VPS status summary. The machine graphs on the VPS page are a different system. See [VPS](vps.md).

## Pointers

`web/src/components/UpdateActions.svelte`, `web/src/lib/updates/`, and `src/planner/environments/update_service.py`, `update_requests.py`, `update_admission.py`.

_Last verified: 2026-10-30 against source. No hosted publication or production adoption is claimed from this checkout._
