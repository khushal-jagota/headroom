# TypeScript CLI

`node server/cli/main.ts` is the source executable for the migration. The installed
`panels` command remains Python until cutover.

The first two migration PRs implement all 98 HTTP commands and five offline commands.
`work_commands.ts` owns the 52 work commands: `send-message`, `project`, `ticket`,
`manager` (including artifacts and conversation), `worker`, `worker-type`, and `skill`.
The remaining groups use handlers under their domains: `goal`, `page`, `credential`,
`measure`, `attention`, `measure-source`, `preview`, and `dataset`.
`commands.ts` contains the complete current Python declaration tree, including hidden
operator and executor commands whose behavior remains with their owners.

`main.ts` imports only the selected handler after parsing. Help imports declarations,
the parser, and the formatter; it does not initialize a server, database, or provider.
Measure-source schemas and runtime guidance come from the shared domain guidance
exports. The CLI holds no second JavaScript example or helper signature.
Transport forwards the environment identity without inventing a principal. The
shared numeric JSON codec preserves Python integer and float representation.

The import-only exporter `tests/compare/direct/unit20_metadata.py` regenerates
command declarations from current Click objects, including UUID parameter types. Run it with synthetic homes and
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

UUID options normalize the forms that Click accepts through Python's `uuid.UUID`:
compact, hyphenated, braced, and lower-case URN wrappers, plus the underlying hexadecimal
integer grammar. Invalid values retain Click's usage error and the stripped value's
shared Python string representation. Identity remains an independent environment fact.

The lifecycle command table will be bound in the third PR. Executor transport and
its second command table remain with the executor unit. This package does not
switch the installed wrapper or deploy an application.
