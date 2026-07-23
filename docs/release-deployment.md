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

The checked-in launchd and systemd files are install intent. Closeout must establish and verify
host ownership and permissions: the deploy identity writes releases and controls, while the live
service writes only the external persistent paths named by host configuration.
