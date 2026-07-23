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

All services call the environment command from the pinned, root-owned manager
checkout, then select an environment-specific target checkout:

```sh
/opt/panels/environment-manager/.venv/bin/python -m planner environment run \
  --repository-root /opt/panels/<environment>
```

The manager checkout is readable and executable by both service accounts but writable
only by root. The launcher validates the target checkout's own `.venv` and then replaces
itself with that target interpreter. This lets new environment-management code launch an
accepted older live revision without importing the application from the manager checkout.

Live uses the fixed port in its prepared contract. Start the staging unit only while
active work needs it and stop it afterward; each start chooses an available loopback
port and reports the actual URL in the service log. Staging's prepared state remains
between starts.

The static units use distinct repository root conventions: live uses
`/opt/panels/live` and staging uses `/opt/panels/staging`. A unit's `WorkingDirectory`, `ExecStart`
`--repository-root`, and `ReadWritePaths` repository entry must name the same path.
The repository path is the caller-trusted working directory and is writable because
Panels workers operate that checkout on the VPS. Runtime writes belong under
`/var/lib/panels/environments`, and credential files belong under
`/etc/panels/environments`. Repositories cannot be shared across live and staging.
