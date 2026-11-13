# Mac runtime archives

The update host gives archive work its own `FilesService` instance.
The existing worker serializes archive extraction, later requests, and stop cleanup on one async chain.

`extract_runtime` checks the approved size, digest, and revision, then publishes under `updates/releases/<revision>-<sha256>`.
The ordinary Node package contains `mac-runtime.json`, executable `node/bin/node`, and `server/executor/mac/entrypoint.ts`.
Regular files, directories, and contained relative symlinks use the existing bounded TAR parser.
A first gzip pass checks the member graph. A second writes through retained directory descriptors into a private stage.
Files and directories flush before the stage replaces the release entry. Failed stages receive descriptor-relative cleanup.
Existing releases with the matching manifest and required executable files can be reused.

The producer format excludes hardlinks, alias-parent members, cycles, special modes, and backslash member names.
The shared parser's limits and refusals remain unchanged. VPS archive inspection does not change.
Darwin ABI and native durability still require the later Mac check.
