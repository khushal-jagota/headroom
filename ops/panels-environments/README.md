# Panels environment Linux inputs

These files are checked-in render/install inputs only. They do not install users,
write permissions, enable units, start services, configure networking, or prove VPS
enforcement by themselves.

The live runtime uses the ordinary Linux account `panels-live`. The staging runtime
uses the ordinary Linux account `panels-worker`. The live state, config, and
credentials must stay unreadable and unwritable by `panels-worker`; the worker account
is for nonproduction runtime state only.
The shared `/etc/panels/environments` directory is traversable by the service users
so each unit can open its own credential file. The live credential file remains
`0640 panels-live:panels-live`, so `panels-worker` can traverse the parent but cannot
read or write live credential content. The staging credential file is
`0640 panels-worker:panels-worker`.

Staging calls the environment command from the pinned, root-owned manager checkout. Live calls the
stable operator-owned `current` release launcher:

```sh
/opt/panels/environment-manager/.venv/bin/python -m planner environment run \
  --repository-root /opt/panels/<environment>
```

The release root and deployment controls are writable only by the operator/deploy identity. The
live service receives external state paths and cannot modify the selected release.

`setup-accounts.sh` also installs the checked-in `panels` wrapper as the root-owned,
mode-`0755` regular file `/usr/local/bin/panels`. Agent shells include `/usr/local/bin`
in their standard PATH, so the bare command works outside a release checkout. The wrapper
executes `/opt/panels/current/bin/panels-launcher` and forwards every argument unchanged.
It is deliberately a wrapper rather than a symlink: the launcher derives its release root
from its own path, so a symlink in `/usr/local/bin` would resolve the wrong root.

Deployments and rollbacks do not replace this host-level command. They switch only
`/opt/panels/current`, and the wrapper follows whichever release that pointer selects.

Live uses the fixed port in its prepared contract. Start the staging unit only while
active work needs it and stop it afterward; each start chooses an available loopback
port and reports the actual URL in the service log. Staging's prepared state remains
between starts.

The static units use a stable release launcher for live and `/opt/panels/staging` for staging.
Runtime writes belong under
`/var/lib/panels/environments`, and credential files belong under
`/etc/panels/environments`. Repositories cannot be shared across live and staging.

SQLite backup and restore setup is in [backup-restore.md](backup-restore.md).
