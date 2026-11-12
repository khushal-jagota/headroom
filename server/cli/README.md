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
compares fresh Python output, not frozen help snapshots.

The exhaustive help check invokes Click in-process and batches the TypeScript
parser in one test-only process. The duplicate same-host replay uses Click
in-process too. Startup checks compare these Python results with real executable
results. The shared populated list still runs both executables for every step.

The shared comparison runner accepts `kind: cli` steps with argv, stdin, fixture
files, explicit synthetic identity, expected exit code, and captures. It accepts
neither a shell command nor an environment map. The driver checks the fixture's
loopback address, private temporary database, live-address exclusions, running
process and test-mode metadata before a CLI subprocess starts.

The lifecycle command table will be bound in the third PR. Executor transport and
its second command table remain with the executor unit. This package does not
switch the installed wrapper or deploy an application.
