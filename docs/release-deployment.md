# Exact-commit releases

Production runs a host-native release, not a Git checkout. A release manifest records the exact
40-character `main` SHA and a digest of the exported source. The stable `current` pointer selects
one validated release. Database, managed files, Hermes/provider state, configuration, credentials,
logs, and backups remain outside the release.

GitHub checks out `${{ github.sha }}` explicitly, proves that exact `HEAD`, runs the release gate,
and passes the same SHA to deployment. Deployment backs up using the current manifest, switches
`current` atomically, restarts the service, and proves the requested SHA through local health. A
failed proof rolls back code only and proves the previous release; it never restores application
state automatically.

The checked-in launchd and systemd files are install intent. The single-user Mac uses a user
LaunchAgent and a runner under the same operator account, with releases and state under
`~/Library/Application Support/Panels`. A future multi-user server may add separate service and
deployment identities without changing the release transaction.
