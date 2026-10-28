# Owner-controlled updates

Panels offers a checked, retained application for the owner to install. A merge to
`main` publishes that release without changing the running workspace.
The existing deployment runner installs it only after an owner request.
[Production deployment](deployment.md) describes the archive checks and service operations.

```text
retained release → owner chooses now or idle → durable pinned request
                                                   ↓
                           runner validates → final admission → local replacement
                                                   ↓
                                local app healthy → post-health workflow result
```

## Owner choices

**Update now** requests the selected release. Active or unknown workspace activity
requires explicit confirmation. Active turns can stop during this choice.
Existing interruption records account for stopped work without replay of uncertain sends.

**Update when idle** waits until workspace activity is known and idle. The existing
readiness loop processes the request. It does not create a second scheduler.
The runner checks activity again at final admission under the same barrier that
controls conversation start, send, and background start. If activity changes, the
idle request returns to pending without stopping the service.

**Cancel pending update** cancels a pending or dispatched request before runner claim.
Cancellation and claim are exclusive. Once the runner claims it, cancellation reports
that application already began.

Each request pins its publication, archive, proof, source SHA, and digests. It never
silently changes to a newer release. A missing or expired publication requires a new
selection. The owner can select a newer retained release directly without installing
intermediate releases.

The request record stays outside database snapshots. A database restore cannot replay
its claim. A failed or uncertain dispatch does not automatically repeat that dispatch.
The owner inspects repository runner evidence before a new request.

A local **applied** result means that the app is installed. The deployment status
separately reports the final workflow result. Failed skill or published-route checks
leave the installed app in place and report that the workflow is incomplete.
The browser offers a separate page refresh when its loaded app differs from the server.

Code paths: `web/src/components/UpdateActions.svelte`, `web/src/lib/updates/`,
`src/planner/environments/update_service.py`, `update_requests.py`, `update_api.py`.

## Retained release and schema support

Availability comes from a successful trusted `main` push through the publication workflow.
Both its executable artifact and proof artifact must remain available and unexpired.
The archive retains executable modes and safe links. Adoption checks its exact bytes,
manifest, runtime, and pinned receipt. It neither rebuilds nor reruns publication suites.
Artifacts request 30-day retention. Repository retention and deletion can remove them
earlier. Such a release stops appearing as available.

The supported schema floor is `video_upload_ledger`. Adoption refuses an unknown
schema ancestry or a database before that floor before service shutdown.
Cumulative migrations support direct upgrades from the floor and later supported states.
This floor does not prove universal data safety. Existing stopped-service snapshots
and the recovery boundary still apply.

The executable payload is `panels-app.tar.gz`. The separate publication receipt binds
the run, executable artifact, manifest, archive hash, schema floor, and expiry.
Trusted GitHub artifact metadata supplies the proof artifact identity.
Installation checks current workspace preconditions independently of publication-time
live state.

Code paths: `src/planner/environments/update_contracts.py`, `update_provider.py`,
`update_artifacts.py`, `update_runner.py`, `.github/workflows/deploy.yml`.

## Operator configuration and authority

The operator supplies `PLAN_UPDATE_REPOSITORY` as `owner/repository` and
`PLAN_UPDATE_TOKEN_FILE` as a protected file containing the repository dispatch credential.
Credential creation and installation remain separate operator work. This feature does
not provision credentials or change repository protections.
Without configuration, Panels reports that owner updates are unavailable.
Repository access failures produce an unavailable state without a fabricated release.

Update HTTP actions use the existing owner-principal check. Named Chief, Manager,
and Ticket Worker principals receive a refusal. Missing actor headers currently mean
owner under the existing trusted-caller contract. This is not spoof-resistant owner
authentication. It adds no login, GitHub confirmation, or new identity system.
Workers receive no dispatch credential or authorized restart operation.

The dispatch pins the request and release to the existing workflow's adoption operation.
Only that manual operation on `main`, without the rehearsal flag, selects the dedicated
self-hosted production runner. Main pushes run publication on hosted machines.
The runner uses the built-in workflow token only for read-only artifact access.
Workflow permissions are `contents: read` and `actions: read`.
The server's dispatch credential remains separate from this runner token.

Code paths: `src/planner/core/authctx.py`, `src/planner/core/trusted_ingress.py`,
`src/planner/environments/update_provider.py`, `.github/workflows/deploy.yml`.

## Startup admission and runner loss

The runner stops old writers before the verified snapshot and application replacement.
A candidate starts with health and status available, plus controlled startup and
migration writes. Ordinary HTTP and CLI work, conversation recovery, background loops,
tail writes, and file cleanup stay closed until the matching local commit.
The lifecycle observer opens ordinary work once after that commit.

Restore remains possible only before normal candidate work opens. After local commit,
a status publication failure never restores the database. Skill and HTTP/2 failures
report the installed SHA and incomplete workflow instead of undoing admitted work.
New snapshots exclude uploads and Ticket artifacts. Adoption leaves those files untouched.

If the runner disappears before commit, normal work stays closed. Expired lifecycle
evidence becomes a visible recovery-required state. Expiry grants neither admission
nor permission to restore. The owner recovers through the deployment runner with an
explicit snapshot and fallback selection when those are needed.
No automatic continuation or maximum outage is promised after runner loss.

The existing direct `app-deploy` operator tool has no resume command. Its same-SHA
path checks health only. It does not finish a lost startup commit or reconstruct
missing recovery choices. A failed recovery needs explicit operator continuation.

Code paths: `src/planner/environments/update_admission.py`, `deployment.py`,
`deployment_lifecycle.py`, `src/planner/core/server.py`.

---

_Last verified: 2026-10-28 against source. No hosted publication or production adoption claimed._
