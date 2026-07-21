# Isolated Panels runtime environments — implementation contract

Ticket: `t_2r1u7f39`

## Outcome

The repository can prepare and run three runtime shapes through one contract:

- **live** — a production-shaped instance whose state and credentials remain operator-owned;
- **staging** — one stable non-production identity and URL, with resettable fake state;
- **preview `<id>`** — a disposable non-production instance for one repository/worktree.

Staging and every preview materialize separate SQLite databases and managed-file trees from one canonical, versioned fake-data seed. They never share a writable database and never accept a production database as seed input.

Local verification proves process/configuration isolation on macOS. Checked-in Linux assets express the real production OS-account boundary, but only the final VPS Ticket may install or claim that boundary.

## Existing contracts to preserve

- `panels serve` remains the single foreground server entry point and the operator-owned supervisor.
- The server continues to bind `127.0.0.1`; this Ticket adds no ingress, proxy, deployment, or public URL behavior.
- `Config` remains the source of `PLAN_*` runtime values. Managed files continue to derive from the configured database directory.
- `PLAN_HERMES_HOME` remains the explicit Hermes-home selector, and repository skill provisioning copies no credentials.
- The current running Panels instance, its database, its Hermes home, and its supervisor are never restarted or mutated.
- Production promotion, backups/recovery, monitoring/cleanup, immutable release layout, and final VPS/Tailscale application remain separate Tickets.

## Repository-owned environment contract

Create a semantic `src/planner/environments/` domain with framework-free contracts and validation.

An instance resolves, at minimum:

- kind: `live`, `staging`, or `preview`;
- stable instance id (`live`, `staging`, or a validated preview id);
- absolute environment root and instance root;
- SQLite database and therefore managed-file root;
- Hermes home;
- logs directory;
- dispatcher lock and server control socket;
- port;
- credentials/environment-file reference;
- one or more allowed repository roots;
- expected Linux account (`panels-live` for live, `panels-worker` otherwise).

The contract must reject:

- unsafe or ambiguous instance ids and non-absolute roots;
- duplicate or overlapping instance roots, state paths, Hermes homes, control sockets, or ports;
- any preview/staging path or credential reference nested under the live instance;
- a repository path that does not resolve to an allowed existing repository/worktree;
- malformed, duplicate, unknown, or forbidden keys in credentials/environment files;
- any non-production environment file carrying production-only settings;
- environment values that attempt to override contract-owned `PLAN_*`, `HERMES_HOME`, `PYTHONPATH`, process identity, or launch-root keys.

The distinction is explicit: live gets an OS security boundary on Linux; staging and previews share the non-production OS identity and are configuration/state-isolated, not hostile sandboxes from one another.

## Layout and instance metadata

Use one caller-selected absolute environment root so the same contract works under a local temporary/data directory and under Linux. The canonical shape is:

```text
<environment-root>/
  live/
  staging/
  previews/<preview-id>/
```

Each instance owns its manifest and runtime subdirectories. Manifest writes are atomic and contain resolved paths/settings but never credential contents.

Use conventional defaults that callers may override before materialization:

- live port `8767`;
- staging port `8768`;
- previews allocated under an exclusive environment-root registry lock from a documented configurable range, never by a check-then-write race.

The stable staging URL comes from its stable port. Preview ids and ports are durable in their instance manifests until removal.

## Public CLI

Add a `panels environment` group with concise help and JSON support where existing CLI conventions require it.

The supported lifecycle is:

- `inspect` — resolve/read one instance and show paths, port, account, repository roots, fixture version, and prepared/running state without exposing credential values;
- `prepare` — validate the whole environment-root registry, atomically materialize one instance, initialize staging/preview fake state, and create an empty live layout without fake data;
- `run` — require a prepared instance, build a scrubbed allowlisted process environment, then replace itself with the same interpreter running `python -m planner serve`; no second daemon/supervisor is introduced;
- `reset` — only staging/preview, only while that instance's port lifecycle lease is free; rebuild its SQLite database and managed files from the canonical seed while preserving the instance identity, port, Hermes home, and credential reference;
- `remove` — only while stopped; never live; remove the selected staging/preview root and release preview registry allocation.

Exact Click argument/option spelling is an implementation detail, but commands must be scriptable, deterministic, and documented with copyable examples.

`run` begins from a small safe ambient allowlist needed to execute the process (for example locale, terminal, PATH, and temporary-directory basics), then adds only validated credential-file values plus contract-owned runtime values. It never forwards arbitrary ambient `PLAN_*`, `HERMES_*`, provider credentials, `PYTHONPATH`, or ticket/actor identity.

Destructive commands prove the instance is stopped by acquiring and releasing the same port-scoped lifecycle lease used by `panels serve`; they do not inspect or signal processes.

## Canonical representative fake data

Add a dedicated, versioned fixture builder, separate from `planner.seed` (the retired one-time markdown migration importer).

The fixture is created through current schema/domain writers and provides a small but useful workspace with representative projects, sprint/day placement, multiple Worker types/stages/statuses, managed Markdown, and a managed image/file placeholder where the current storage contract allows it. It contains obviously fictional names/content and no copied runtime ids, paths, sessions, or secrets.

- `prepare staging`, `prepare preview`, and `reset` build a new current-schema database from this fixture.
- Every materialization starts from the same canonical source but receives independent generated ids/database bytes.
- Reset is staged in a sibling temporary directory and replaces the data tree only after fixture construction and integrity checks pass; a failed reset leaves the prior data tree intact.
- Live preparation never invokes the fixture builder.

## Hermes homes and real-session smoke

Preparation creates a distinct empty Hermes home for every instance and provisions only repository-owned role-skill links through the existing provisioning contract. It never copies `auth.json`, provider configuration, session state, or credentials from another home.

Add a human-run opt-in smoke command/script that accepts two already-authenticated non-production instance homes, creates one unrelated real session in each through the real gateway, proves the stored session ids and home paths differ, closes the first ACP child, and loads each session from a fresh child before reporting it. The smoke does not delete those durable sessions; they belong to the homes used for the smoke and remain for operator inspection or later cleanup. It must fail clearly when either home is not independently configured. Canonical tests use fakes and never make model calls.

## Linux assets

Check in repository-owned deployment assets under one clearly named top-level directory:

- account/directory setup description or script for `panels-live` and `panels-worker`;
- a live systemd unit and parameterized non-production unit/template that call the same `panels environment run` entry point;
- environment-file examples containing names only, never secrets;
- ownership/mode expectations that keep live state/config/credentials unreadable and unwritable by `panels-worker` while allowing live API/CLI access through the separate ingress/runtime path.

Local tests render/parse and assert the units' users, paths, environment files, working directories, restart behavior, and command. They do not claim macOS has enforced Linux users or modes.

## RED-first acceptance seams

Use vertical RED → GREEN slices and preserve the exact decisive failure/pass output in `red-green.md`.

1. **Contract isolation:** wished-for contract resolves live/staging/preview layouts and rejects path, port, credentials, repository, and identity collisions.
2. **Fake state:** staging and two previews start with the same logical seed but independent ids/database paths/files; a write/reset/remove in one leaves the others unchanged; failed reset preserves prior state.
3. **Scrubbed launch:** the real CLI environment builder/exec seam omits hostile ambient production, actor/ticket, provider, and path values while retaining only approved basics and explicit validated instance settings.
4. **Lifecycle safety:** prepare is idempotent for the exact manifest, concurrent preview prepare allocates distinct ports, mismatched re-prepare fails, and reset/remove fail while the real port lease is held or for live.
5. **Concurrent runtime:** real test-mode subprocesses launched through `panels environment run` become healthy concurrently and expose independent writes/files/logs/locks/control sockets; termination leaves no owned child running.
6. **Linux rendering:** units and setup assets name the correct accounts, instance roots, environment references, and common runtime entry point; no secret value is checked in.
7. **Hermes smoke contract:** fake-gateway tests prove two explicit homes produce distinct stored session ids, and the official ACP path proves successful `end_turn`, exact agent text, and fresh-child session loading; the real path is opt-in and never part of `./verify`.

## Contract-scoped files

- new `src/planner/environments/` domain;
- narrow `src/planner/cli/main.py` registration for `panels environment`;
- existing config/Hermes/server-lifecycle code only where a proven contract seam requires it;
- focused unit/e2e tests and support fixtures;
- repository-owned Linux runtime assets under one new top-level directory;
- `docs/cli.md`, `docs/systems.md`, and at most one dedicated environment document if needed to keep the explanation readable;
- this ticket's orchestration artifacts, `PROGRESS.md`, and `decisions.md`.

No frontend source/build, HTTP/domain behavior, database migration, Worker-type definition, existing runtime data, global Hermes home, other repository, deployment, backup, monitoring, or server restart belongs to this Ticket.

## Completion boundary

Implementation ends with a reviewed, verified commit on `ticket/t_2r1u7f39-isolated-runtime` and an approval-ready Implementation proposal. It does not merge to `main`, install units/accounts, configure credentials, run a production service, or remove the ticket worktree. Those are Closeout or later-ticket actions.
