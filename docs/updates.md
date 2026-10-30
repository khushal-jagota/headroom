# Updates

An update is the owner installing a release that publication already sealed. Merging to `main` publishes. It does not change the running workspace. The deployment runner installs a release only after an owner request, and only that pinned release. The mechanics of the archive, the health budgets, and the restore boundary are [Deployment](deployment.md).

```text
retained release → owner chooses now or idle → durable pinned request
                                                  ↓
                          runner validates → final admission → replace app
                                                  ↓
                               local app healthy → post-health result
```

## The owner actions

The shell offers **Update when idle** and **Start update** when a usable release is waiting. Start update is the immediate install. If the workspace is active, or if activity cannot be known, the screen asks for an explicit confirmation: work is active and the update can interrupt it, or activity is unknown and the update can interrupt work. Proceed with update continues. Active turns can stop. Existing interruption records account for stopped work. They do not replay an uncertain send.

Update when idle waits until workspace activity is known and idle. While it waits, the shell says the update waits for idle. The existing readiness loop notices the request. There is no second scheduler. The runner checks activity again at the last moment, under the same barrier that guards conversation start and background start. If something is running, the request returns to pending and the service stays up.

Cancel update cancels a request the runner has not claimed. A confirmed cancellation before claim says the update was canceled before installation and the previous version stayed active. That is a neutral outcome. It is not a failed deployment and it is not a rollback. The archive keeps at most 32 canceled requests as reporting evidence. If an older receipt is gone, that report stays uncertain. The current request is what still controls the buttons. Once the runner has claimed a request, cancellation reports that application already began. While a request is pending, dispatched, or applying, a new one is blocked. After a failure, both actions return if the release is still usable. An expired release has no retry on that failure.

Each request pins the publication, the archive, the proof, the source SHA, and the digests. It never slides forward to a newer commit. A missing or expired publication needs a new selection. The owner can pick a newer retained release directly and skip the ones in between.

The request record lives outside database snapshots. Restoring a database does not replay a claim. A failed or uncertain dispatch does not retry itself. The owner looks at the runner evidence and asks again if they still want it.

A local applied result means the app is installed. Deployment status separately reports whether skill reconciliation and the published HTTP/2 check finished. Those can fail and leave the new app in place. A failed skill or route check does not mean the app is unhealthy. Missing admission or installation evidence stays an explicit uncertainty. The finalizer reports each of those checks apart from application health, and a stale finalizer cannot rewrite a replacement attempt, even when two requests name the same SHA. The browser offers a refresh when the app it loaded differs from the server. That refresh does not replace the push service worker by itself.

## Who can ask, and what the runner will accept

Update actions use the owner principal check. Chief, Managers, and Ticket Workers are refused. A missing actor header is treated as the owner under the current trusted-caller contract. That is not spoof-resistant authentication. It adds no login. Workers receive no dispatch credential and no restart operation.

The operator supplies `PLAN_UPDATE_REPOSITORY` as `owner/repository` and `PLAN_UPDATE_TOKEN_FILE` as a protected file holding the dispatch credential. Without them, Headroom says owner updates are unavailable. It does not invent a release. The dispatch credential is separate from the runner’s read-only workflow token. Only the manual adoption operation on `main`, without the rehearsal flag, selects the production runner. Pushes run publication on hosted machines.

The supported schema floor is `video_upload_ledger`. Adoption refuses an unknown ancestry, or a database older than that floor, before it stops the service. The floor is not a promise that every historical database is safe to migrate. Cancelled-Ticket recovery in [Deployment](deployment.md) still applies. New candidates also need the runtime contract in [Archive runtime](archive-runtime.md). A legacy installed app can remain the fallback. A legacy candidate without the contract cannot be installed. It has to be republished.

## If the runner is lost

Before the local commit, writers are stopped and ordinary work stays closed. Restore is possible only in that window. After the commit, a failed status publication does not restore the database.

If the runner vanishes before commit, work stays closed. When the lifecycle evidence expires, the state is recovery-required. Expiry does not open the app and does not restore a snapshot. The owner recovers on the deployment runner and chooses the snapshot and the fallback explicitly. The direct operator tool has no resume command. Its same-SHA path checks health. It does not finish a lost commit.

## Where it shows up

The actions sit in the shell status pill, not on a Settings page. Deployment outcome text also appears in the smaller VPS status summary. The machine graphs on the VPS page are a different system. See [VPS](vps.md).

## Pointers

`web/src/components/UpdateActions.svelte`, `web/src/lib/updates/`, and `src/planner/environments/update_service.py`, `update_requests.py`, `update_admission.py`.

_Last verified: 2026-10-30 against source. No hosted publication or production adoption is claimed from this checkout._
