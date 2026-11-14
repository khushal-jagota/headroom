# TypeScript CLI

`node server/cli/main.ts` is the source executable for the migration. The installed
`panels` command remains Python until cutover.

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
commands. Transport forwards the caller's environment identity without inventing
another principal.

Goal and holder-page schema guidance lives in `server/goals/cli/guidance.json` and
`server/managers/page_cli/guidance.json`. Their commands import those JSON documents
only when needed. Measure-source schemas and runtime guidance come from the shared
domain exports. The CLI holds no second JavaScript example or helper signature.

During migration, `tests/compare/direct/unit20_metadata.py` reads the current Python
command and schema contracts without invoking commands or overwriting TypeScript
source. Its command-line entry prints reference JSON. The offline drift check maps
the native Commander declarations to the same functional contract in test code.
It compares command and parameter names, option spellings, types, defaults, choices,
path rules, cardinality, and environment variables. Help wording and layout are
outside that contract. The goal and holder-page JSON documents still compare with
fresh Python schema exports. Existing canonical validators check their examples.

The exhaustive help check invokes Click in-process and batches the TypeScript
parser in one test-only process. Startup checks compare these Python results with
real executable results. Populated comparisons still run both executables for every
step and compare JSON fields, types, and exit codes. Existing focused parser coverage
checks exact integers that a response with an empty page cannot prove.

The shared comparison runner accepts `kind: cli` steps with argv, stdin, fixture
files, explicit synthetic identity, expected exit code, and captures. It accepts
neither a shell command nor an environment map. Before network or subprocess access,
`tests/compare/direct/unit20_cli.py::CliHost.check` refuses the live server address,
port 8767, non-loopback addresses, and databases outside the private temporary root.
It checks that the listener belongs to the fixture's exact process ID. Before a CLI
starts, the driver also requires test-mode metadata from that admitted fixture.
Synthetic homes and state replace inherited provider and Panels configuration.

The test driver captures content-decoded HTTP document bytes before CLI formatting.
It retains the original compressed response bytes too. Comparisons use parsed JSON
fields and numeric values, with named ID and bounded opaque-value rules. The accepted
attention digest and goal catalog aliases stay explicit in the rule file.

The HTTP client keeps Node's default TLS roots and honors `SSL_CERT_FILE`,
`SSL_CERT_DIR`, and Node's startup `NODE_EXTRA_CA_CERTS` behavior. Hash-directory
lookup requires `openssl`. Connection, header progress, and body-read gaps each use
30 seconds in production. Direct checks inject short phase limits. Accepted
trust-store and diagnostic differences remain in `../DEVIATIONS.md`.

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

This package does not switch the installed wrapper or deploy an application.
