# Deploy interruption evidence

`manifest_root` expands the supplied database path's user home and resolves its
filesystem symlinks before it selects the parent. Evidence lives at
`<resolved-parent>/deploy-interruptions`. A DB-file symlink therefore selects the
target's parent. Usage shares the filesystem resolver. Video uploads keep
a different bare-parent policy.
