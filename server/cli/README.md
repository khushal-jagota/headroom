# TypeScript CLI

`node server/cli/main.ts` is the source executable for the migration. The installed
`panels` command remains Python until cutover.

The first migration PR implements the 52 HTTP commands in `work_commands.ts`:
`send-message`, `project`, `ticket`, `manager` (including artifacts and conversation),
`worker`, `worker-type`, and `skill`. `commands.ts` contains the complete current
Python declaration tree so that help and usage comparisons include hidden commands.
Declarations for later groups do not claim that their handlers exist yet.

`main.ts` loads work handlers only after parsing. Help imports declarations, the
parser, and the formatter; it does not initialize a server, database, or provider.
Transport forwards the environment identity without inventing a principal. The
shared numeric JSON codec preserves Python integer and float representation.

The import-only exporter `tests/compare/direct/unit20_metadata.py` regenerates
command declarations from current Click objects. Run it with synthetic homes and
no inherited `PLAN_*` variables, then format the generated TypeScript. The ordinary
proof instead invokes both CLIs through the guarded synthetic-host driver. It
compares fresh Python output, not frozen help snapshots. An offline drift check compares
the complete fresh Python declarations with `commands.ts` without writing repository
files or starting another server. The exporter rejects unsupported parameter types.

The TypeScript declaration file is the runtime authority. While Python remains in the
migration, regenerate it from Python and check for drift. After Python removal, edit
these declarations with TypeScript command changes. The cutover retires the Python
exporter and its drift comparison; the CLI does not require Python at startup.

Parsing first checks the whole command line for option syntax, then processes eager
help, then converts parameters in Click invocation order. Required parameters fail
before extra arguments. Declared environment variables supply parameter values before
defaults. A shared callback-level Ticket resolver preserves Python's additional
fallback and the dash sentinel without duplicate environment handling in domain code.
The complete declaration tree includes the UUID parameter grammar used by credentials;
its handler remains with the later command groups.

The exhaustive help check invokes Click in-process and batches the TypeScript
parser in one test-only process. The duplicate same-host replay uses Click
in-process too. Startup checks compare these Python results with real executable
results. The shared populated list still runs both executables for every step.

The shared comparison runner accepts `kind: cli` steps with argv, stdin, fixture
files, explicit synthetic identity, expected exit code, and captures. It accepts
neither a shell command nor an environment map. The driver checks the fixture's
loopback address, private temporary database, live-address exclusions, running
process and test-mode metadata before a CLI subprocess starts.
It also verifies that the listener belongs to the fixture's exact process ID.

The client keeps Node's default TLS roots and honors `SSL_CERT_FILE`,
`SSL_CERT_DIR`, and Node's startup `NODE_EXTRA_CA_CERTS` behavior. Hash-directory
lookup requires `openssl`. Connection, header progress, and body-read gaps each
use 30 seconds in production. Direct checks inject short phase limits.
Accepted trust-store and diagnostic differences are recorded in `../DEVIATIONS.md`.
Malformed JSON diagnostics target CPython 3.12, the hosted reference version.
The fresh Python oracle compares error messages and positions. Later local Python
versions skip that oracle because their decoder messages differ.

The lifecycle command table will be bound in the third PR. Executor transport and
its second command table remain with the executor unit. This package does not
switch the installed wrapper or deploy an application.
