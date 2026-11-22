Linux executor archives reuse the native Linux Node builder and the shared application archive checks. The marker keeps the `mac-runtime.json` filename, with Linux, x86_64, executor role, schema version 1, and the approved revision. The manifest binds the native version 2 contract and inventory. Home admission refuses executor markers.

Linux uses admitted host Node 22, at least 22.18.0. The archive contains the shared executor CLI and executable `bin/panels`. It contains no private Node. The producer hides its original build tree before extraction, runtime admission, and executor CLI help. Its publication evidence binds the archive hash and size. These are synthetic build and admission operations, with no service or provider connection.

The default join root is `${XDG_DATA_HOME:-$HOME/.local/share}/panels/executor-typescript`. Its `data/` directory holds credentials and executor state. Its `installation/releases/<revision>-<archive-sha256>` directories hold versioned runtimes. An explicit `PLAN_EXECUTOR_ROOT` selects the data directory. Linux default paths are shared with P2 through `logic/paths.ts`.

The user unit is `panels-executor-typescript.service`. It launches the absolute admitted host Node and `<runtime>/server/executor/mac/entrypoint.ts`, followed by the existing `executor run` arguments. That entrypoint enters the shared CLI. PATH contains runtime bin, agent package tools, the host Node directory, the account's local bin, then the standard system bins. The unit sets HOME and explicit executor/app identity. It clears inherited Node and Linux loader overrides before exec, as the existing native launchers do. Credentials remain in the private file.

The low-level installer admits the archive and checks its actual ExecStart Node before credential or unit writes. It refuses existing data, releases, or service state. Existing empty directories remain valid. Failure removes only the resources that the attempt creates and restores an existing data directory's mode. It writes a user unit but does not enable or start it. The private join entry consumes this installer, creates the user unit exclusively, enables it, and waits for a fresh accepted protocol-26 lease. Failed activation stops its own unit before cleanup. Uncertain stop retains the installation and names its path. Interrupts wait through rollback and child settlement before exit.

The existing files worker accepts the expected target for retained reads and extraction. Linux checks keep the root descriptor, bind revision, contract and inventory, and require the shared entrypoint without a private Node check. Undefined target preserves the existing Darwin behavior. The runtime supervisor's Linux launch branch belongs to P2.

The shared install recipe selects the artifact by system. A Linux join checks host Node and the sealed executor role before credential input, then passes the credential through private JSON stdin. An Apple Silicon join takes the same route to `server/environments/mac_executor/`, on the Node inside its release. Home installation keeps its own path. `machine join-command NAME --home URL` issues a machine credential and prints the release-pinned bootstrap command. The credential stays outside that command.

The publication receipt admits the optional Linux artifact only after a successful native build and matching GitHub artifact identity. Linux archive preparation shares the existing process owner, runs only for registered Linux machines, and does not block home or Mac offers. Permanent byte failures stay rejected until restart. Transient failures retry after 60 seconds. The cache retains two Linux archives, and shutdown waits for actual inspection cleanup.

The URL in the unit bootstraps the shared machine link. After a successful HTTPS contact, the
executor root keeps `server-link.json` with the server identity, highest move number, and current
address. That address takes precedence over the unit argument after restart. The shared command
`panels executor server-address --url https://server.example` checks and saves another address
without a service-file edit or a reset of the identity and move number.
