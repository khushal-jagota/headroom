# TypeScript CLI

`node server/cli/main.ts` is the source executable for the migration. The installed
`panels` command remains Python until cutover.

The CLI implements 100 HTTP commands and five offline commands.
`work_commands.ts` owns the 54 work commands: `send-message`, `project`, `ticket`,
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
JSON output preserves fields, types, and exact integers. Human text, help layout, and errors use native TypeScript formatting.

`ticket request-recap ID` and `manager request-recap ID` ask a subordinate for a
fresh recap through the ordinary system-message route. Ticket summaries, Manager
summaries, and Manager workspaces show the recap age, unknown age, or absence.
Ticket displays also identify a stage change since the last recap.
Proposal accept and revise commands read the current proposal revision and pass
that revision with a stable decision ID to the canonical decision route.

The import-only exporter `tests/compare/direct/unit20_metadata.py` regenerates
command declarations from current Click objects, including UUID parameter types. Run it with synthetic homes and
no inherited `PLAN_*` variables, then format the generated TypeScript. The ordinary
proof instead invokes both CLIs through the guarded synthetic-host driver. It
compares JSON fields and exit codes against fresh Python. An offline drift check compares
the complete fresh Python declarations with `commands.ts` without writing repository
files or starting another server. The exporter rejects unsupported parameter types.
The same exporter refreshes the goal, holder-page and measure offline declarations.
Their readable TypeScript data is checked against fresh Python exports during migration.
Pure canonical chart, page and measure validators also accept a printed example from
each area; this checks the contract without another application host.
Help lists the declared commands and options without reproducing Click layout.


The TypeScript declaration file is the runtime authority. While Python remains in the
migration, regenerate it from Python and check for drift. After Python removal, edit
these declarations with TypeScript command changes. The cutover retires the Python
exporter and its drift comparison. TypeScript command and shared domain declarations
then become the editing source of truth, including goal/page/measure schema data.
The CLI requires no Python at startup. Source-author guidance selects named canonical
note records; adding a note cannot silently change an override by array position.

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

The test driver captures content-decoded HTTP document bytes before CLI formatting.
It keeps the original compressed response bytes too. The comparisons use parsed JSON fields and numeric values, with named ID and bounded opaque-value rules.
The accepted attention digest and goal catalog aliases appear explicitly in the rule file.

The client keeps Node's default TLS roots and honors `SSL_CERT_FILE`,
`SSL_CERT_DIR`, and Node's startup `NODE_EXTRA_CA_CERTS` behavior. Hash-directory
lookup requires `openssl`. Connection, header progress, and body-read gaps each
use 30 seconds in production. Direct checks inject short phase limits.
Accepted trust-store and diagnostic differences are recorded in `../DEVIATIONS.md`.
Malformed JSON reports the native parser error through the existing validation envelope.

UUID options accept compact, hyphenated, braced, and URN text. Numeric options use
ordinary decimal input and preserve exact integers. Python-only Unicode digit,
underscore, and hexadecimal UUID quirks are not supported. Invalid values return
a usage error. Identity remains an independent environment fact.

`operator_commands.ts` binds the lifecycle table after parsing. It passes the
selected name and invocation environment to the production dependency factory.
The executable assembles no services. Parsed JSON flags and the watchdog database
option map to the handler's typed argument names. Raw failure messages pass through
the same error renderer once, after the separate warning prefix. The hidden task
diagnostic keeps its literal unsupported transcript.

The entry awaits output writes before normal process completion. An interrupted
operator returns only after its factory joins actual cleanup. SIGINT uses Python's
caller-stopped transcript. SIGTERM writes any retained prefix and then retains
native signal termination. App-build, run, serve, and restart retain their accepted
temporary refusals until the release-side prerequisites merge in a later Ticket.
Executor socket transport binds the same command handlers when `PLAN_EXECUTOR_ROOT`
is set. Reads and registered mutations use the Unix client, including lookup-only
retry and exit 3 for unconfirmed acceptance. `executor health` and `quiesce` use that
client too. `executor run` and `permissions` still await the host/platform slices.
This package does not switch the installed wrapper or deploy an application.
