# TypeScript CLI

`node server/cli/main.ts` is the source executable of the `panels` command. Releases
name it as their public CLI entry point.

`commands.ts` declares the command tree with Commander. It is the source of truth
for TypeScript command names, options, arguments, defaults, and validation.
Commander supplies command selection, option parsing, usage errors, and help.
The CLI keeps the existing command surface, JSON fields and types, exact integers,
and exit codes. Human text and help use native TypeScript presentation.

`main.ts` imports only the selected handler after parsing. Help uses the command
declarations and does not initialize a server, database, or provider.
`work_commands.ts` owns `send-message`, `project`, `ticket`, `manager`, `worker`,
`worker-type`, and `skill`. Other groups use handlers under their domains.
Operator and executor commands share the same declaration tree, including hidden
commands. Transport sends the private launch credential automatically. Role variables
remain context. The hidden `api` command shares that transport for routes without
a dedicated command.

Goal and holder-page schema guidance lives in `server/goals/cli/guidance.json` and
`server/managers/page_cli/guidance.json`. Their commands import those JSON documents
only when needed. Measure-source schemas and runtime guidance come from the shared
domain exports. The CLI holds no second JavaScript example or helper signature.

The HTTP client keeps Node's default TLS roots and honors `SSL_CERT_FILE`,
`SSL_CERT_DIR`, and Node's startup `NODE_EXTRA_CA_CERTS` behavior. Hash-directory
lookup requires `openssl`. Connection, header progress, and body-read gaps each use
30 seconds in production. Tests inject short phase limits. Accepted
trust-store and diagnostic differences from the earlier Python CLI are in `../DEVIATIONS.md`.

`operator_commands.ts` binds the lifecycle table after parsing. The executable
assembles no services. The entry awaits output writes before normal process
completion, and interrupted operators join actual cleanup. SIGTERM retains native
signal termination after the retained warning prefix.

Executor socket transport uses the same handlers when `PLAN_EXECUTOR_ROOT` is set.
Reads and registered mutations use the Unix client, including lookup-only retry
and exit 3 for unconfirmed acceptance. `executor health` and `quiesce` use that client
too. `executor permissions` previews lexical scopes and accesses directories only
with `--check`. `executor run` binds the runtime supervisor or versioned Mac host
child from the shared runtime settings.
`executor credential --file FILE` installs a private machine credential through the shared credential file helpers without an HTTP or IPC request.

This package does not deploy an application.
