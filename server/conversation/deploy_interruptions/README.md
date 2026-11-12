# Deploy interruption evidence

`manifest_root` expands the supplied database path's user home and resolves its
filesystem symlinks before it selects the parent, like Python's
`Path.expanduser().resolve().parent`. Evidence lives at
`<resolved-parent>/deploy-interruptions`. A DB-file symlink therefore selects the
target's parent. Usage shares the filesystem resolver. Video uploads retain
Python's different bare-parent policy.

The existing direct capture-evidence fixture opens its tiny synthetic database
through a DB-file symlink and compares all three storage folders with Python.
It starts no host, child process, or provider.
