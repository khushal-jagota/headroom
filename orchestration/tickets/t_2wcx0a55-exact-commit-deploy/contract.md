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
   requested 40-character Git SHA. The GitHub workflow checks out `github.sha` explicitly and proves
   `git rev-parse HEAD` equals that same value before export. Export tracked source without Git
   metadata, build the frontend, install the Python and agent-backend runtime dependencies inside the
   release, and record the exact SHA plus a digest of the exported source input in a typed manifest.
2. Publish only complete releases under an operator-owned versioned release root. An existing release
   with the same SHA is accepted only when its manifest and digest validate. The running service uses
   one stable operator-owned `current` pointer.
3. Before changing `current` or live state, read the currently deployed revision from the validated
   current release manifest and invoke the existing verified database backup operation with that
   revision. Remove the old live-repository/Git revision lookup. A build, validation, or backup
   failure leaves `current` and the running service unchanged.
4. After atomically selecting the candidate release, restart the supervised service and poll a bounded
   local health endpoint. Success requires both application readiness and the exact requested running
   SHA. Record attempted, successful, failed, and rollback results without secrets.
5. If restart or health proof fails, atomically restore the prior code pointer, restart it, and prove
   the prior SHA is healthy within a separate bounded cutoff. Never restore production state
   automatically; report the backup and operator restore path.
6. The stable release launcher validates the selected release manifest, injects that manifest's exact
   SHA as the authoritative runtime identity, and refuses a production release with a missing or
   malformed identity. `/api/meta` returns it, and health rejects a missing or different SHA.
7. Expose operator start, stop, restart, and status through checked-in macOS `launchd` and Linux
   `systemd` inputs. Services start at boot, restart after crashes, run through the stable `current`
   launcher as the live identity, and receive only external persistent runtime paths. The release root
   is read-only to the service; database, managed files, Hermes/provider state, config, credentials,
   logs, and backups are writable only at their external operator-selected paths. Release roots and
   deployment controls remain writable only by the operator/deploy identity. Assets express install
   intent; Closeout establishes and verifies actual host ownership and permissions.
8. Preserve the existing `staging → main` PR. A PR workflow reports canonical verification. Because
   the current private-repository plan cannot enforce required checks, a `main` push runs its own
   verification gate and cannot reach deployment on failure. Green `main` deploys only `github.sha`,
   serially, through one self-hosted production runner whose OS permissions expose only the operator
   deployment boundary.
9. The local Mac is the first target. Moving to the VPS changes configured paths, runner registration,
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
- Runtime tests prove the validated manifest is the only production release-identity source,
  `/api/meta` reports that SHA, and health proof rejects missing, malformed, or wrong SHA values.
- Asset tests prove GitHub trigger/gate/concurrency/SHA flow, launchd and systemd supervision, stable
  `current` launch, and read-only release permissions.
- One isolated local exercise builds a real release, deploys it against a fake service boundary,
  proves rollback, and confirms the release has no Git metadata.
- Focused Ruff, strict Mypy, relevant tests, independent review, and one final canonical `./verify`
  pass in the Ticket worktree before Implementation is proposed.
