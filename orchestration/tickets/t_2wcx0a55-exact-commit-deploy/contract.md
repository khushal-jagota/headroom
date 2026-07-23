# Contract — exact-commit GitHub production deployment

## Accepted outcome

The user's `staging → main` merge is the production approval. GitHub deploys only the exact resulting
`main` SHA. A failed release gate deploys nothing.

Live is a versioned, host-native application release, not a Git checkout or development workspace.
The live release contains no `.git`, branch, remote, or writable source workflow. Persistent database,
managed files, Hermes/provider state, configuration, credentials, logs, and backup storage remain
outside every release.

The same release and deployment contract runs on the current macOS host and the later Linux VPS.
Host configuration selects the service manager and production runner; it does not change release
semantics.

## Required behavior

1. Build one complete host-native release from a source checkout whose `HEAD` exactly equals the
   requested full Git SHA. Export tracked source without Git metadata, build the frontend, install the
   Python and agent-backend runtime dependencies inside the release, and record the exact SHA plus a
   digest of the exported source input in a typed manifest.
2. Publish only complete releases under an operator-owned versioned release root. An existing release
   with the same SHA is accepted only when its manifest and digest validate. The running service uses
   one stable operator-owned `current` pointer.
3. Before changing `current` or live state, invoke the existing verified database backup operation
   with the currently deployed revision. A build, validation, or backup failure leaves `current` and
   the running service unchanged.
4. After atomically selecting the candidate release, restart the supervised service and poll a bounded
   local health endpoint. Success requires both application readiness and the exact requested running
   SHA. Record attempted, successful, failed, and rollback results without secrets.
5. If restart or health proof fails, atomically restore the prior code pointer, restart it, and prove
   the prior SHA is healthy within a separate bounded cutoff. Never restore production state
   automatically; report the backup and operator restore path.
6. Expose operator start, stop, restart, and status through checked-in macOS `launchd` and Linux
   `systemd` inputs. Services start at boot, restart after crashes, run as the live identity, write only
   persistent runtime paths, and cannot write the release root or deployment controls.
7. Preserve the existing `staging → main` PR. A PR workflow reports canonical verification. Because
   the current private-repository plan cannot enforce required checks, a `main` push runs its own
   verification gate and cannot reach deployment on failure. Green `main` deploys only `github.sha`,
   serially, through one self-hosted production runner whose OS permissions expose only the operator
   deployment boundary.
8. The local Mac is the first target. Moving to the VPS changes configured paths, runner registration,
   and service-manager inputs only. Current live remains untouched until operator-approved Closeout.

## Explicit non-goals

- No Release Worker type or additional approval surface.
- No direct deployment from `staging` and no deployment of a moving branch tip.
- No live Git checkout, `git pull`, GitHub credential, or development workspace.
- No automatic state restore, off-host backup, monitoring product, ingress change, or general PR
  repair workflow.
- No claim that repository assets alone prove installed OS ownership or permissions.

## Verification contract

- Focused unit tests cover exact-SHA rejection, Git-free release output, manifest/digest validation,
  idempotent publication, backup-before-switch ordering, no-change failures, serialized deployment,
  bounded health, successful release recording, code rollback, failed rollback reporting, and no
  automatic state restore.
- Runtime tests prove `/api/meta` reports the configured release SHA and that health proof rejects a
  healthy process running the wrong SHA.
- Asset tests prove GitHub trigger/gate/concurrency/SHA flow, launchd and systemd supervision, stable
  `current` launch, and read-only release permissions.
- One isolated local exercise builds a real release, deploys it against a fake service boundary,
  proves rollback, and confirms the release has no Git metadata.
- Focused Ruff, strict Mypy, relevant tests, independent review, and one final canonical `./verify`
  pass in the Ticket worktree before Implementation is proposed.
