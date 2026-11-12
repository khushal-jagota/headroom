# Accepted differences from Python

This file records intentional differences that the migration Manager accepted. No new difference enters this file without that acceptance. Close an entry when Python adopts the same behavior.

## Backend discovery in test mode

The migration Manager accepted this safer rule during Unit 19's backend review.
With `PLAN_TEST_MODE=1`, Python's default backend service still probes the machine.
TypeScript composes an empty recording environment and no production providers.
Without explicit recording fixtures, each backend card therefore reports not installed.
Production subprocess and HTTP doors also refuse test contexts before external I/O.
The focused backend composition check proves this refusal. Scripted comparison hosts
inject their declared catalogs and recording actions explicitly.

## Provider allowance scopes with an empty model display name

The migration Manager accepted this difference during review of Unit 19's backend package.
Python's `backend_state._catalogue_model_id` indexes the first word of each display name
after an exact match fails. If a display name is empty or absent, that lookup raises
`IndexError`. The explicit allowance refresh then returns HTTP 500 before it can replace
the cached observation.

TypeScript treats an empty display name as no display-family match. It can still resolve
a unique model-ID family. It drops an unresolved or ambiguous scoped window and retains
the account-wide windows, then commits a successful observation through the usual short
transaction. It does not reproduce Python's exception. This difference concerns only the
empty-display fallback. Nonempty model names and ordinary ambiguity retain the Python rules.

## Closed: accepted held prompts survive a secondary Ticket-model error

The migration Manager accepted this difference during review of PR 712 on November 12, 2026.
Python commit `27e893ad8` now supplies the same correction in the migration comparison baseline.

Both cores commit the accepted receipt, settle the running phase and held sender identities,
emit the change signal, then invoke the secondary Ticket-model writer. The reservation resolves
in `finally`, including when that writer raises. The same secondary error propagates without
another provider write or committed append. This is no longer an intentional difference.

`server/tests/conversation_core_delivery.test.ts` pins this order in
`publication failure after receipt commit does not append or deliver twice`. It also proves
that a repeated sender identity returns its receipt and later backend news still persists.

- Projection rebuild uses TEMP tables on the single writer connection in batches of 500 events. Python uses a second connection. Results match. The Manager accepted 373.7 seconds in TS versus 94.5 seconds in Python for 200,000 events (about 3.96 times as long). The follow-up must precede the first projection-version change after cutover ([measured profile](https://github.com/khushal-jagota/headroom/blob/a201941e82dde385572a87602f127e0ae18e379d/server/lifecycle/REBUILD_PROFILE.md)). Sustained-load latency is not measured.

## Fixed measure and Goal depth budgets

The migration Manager accepted this policy on November 12, 2026, for Unit 12.

TypeScript uses fixed named depth limits and weights in `server/measures/configuration.ts`:

- Evaluation budget: 991. Each reference costs four units. Each expression edge costs one. Goal actuals start with two units.
- Formula parser budget: 990. Reference leaves add zero, date checks add one, and number/period checks add two. Graph traversal contributes its depth.
- Graph and demand limits: 991.

These values were calibrated on Python's HTTP call path. They are TypeScript policy, not a model of an interpreter stack. TypeScript reads no interpreter setting and retains these limits when Python's boundary moves.

The investigation used Python 3.13.5 with recursion limit 1000. The live server runs Python 3.12. Comparison CI also uses Python 3.12, on both Node 22 and Node 24. Python has no fixed depth contract. Its boundary depends on the interpreter, recursion limit, and call path. In the measured plain-reference case, the shallow helper accepts 248 edges. HTTP reads accept 247 and limit at 248. Twenty extra helper wrappers reduce the shallow boundary to 243.

The gating proof compares Python and TypeScript well inside the budgets, for plain and mixed expressions and independent Goal roots. At the boundary and its neighbors, it asserts the TypeScript outcome only. It records Python's answer as non-gating information. A Python stack change does not invalidate the accepted TypeScript boundary. The direct proof regenerates the Python observations on every run.

The first applicable TypeScript budget determines the boundary error. At total graph depth 992, the measured Python path reports graph nesting while TypeScript reports expression nesting. Both return validation status 400 without a new row. This error-choice difference is recorded with the informational Python boundary observations.

Python also crashes during response serialization for a sufficiently deep accepted formula. In the measured JsonDict response, AST depth 253 succeeds. Depth 254 commits the definition, then returns HTTP 500. TypeScript preserves the committed row and returns the same status, content type, and text through a deliberate, handled response-depth error. It does not reproduce an unhandled Pydantic exception. Request-validation errors retain their separate response path.

See [the measure README](measures/README.md) for the fixed policy and proof commands. Unit 12's Part 2 still supplies the Goal HTTP routes and populated Manager/Chief page checks. The independent-root seam proof does not claim those routes exist in Part 1.

## Managed Linux supervisor stop

The Manager accepted the following stop policy on November 12, 2026.
The acceptance closes the design condition, not the implementation or hosted proofs.

Python on managed Linux checks the kernel signal sender, the unchanged launch parent, and the verified systemd manager identity.
TS will instead require a prior authenticated stop intent for the current supervisor incarnation, then the exact supervisor signal.
A bare signal fails closed. Foreground launches keep ordinary signals, as Python does.

The reason is the accidental-stop threat and simpler packaging for Electron and a Mac-only server.
The intent guard refuses accidental bare signals without a compiled wrapper, platform binary, signing step, or extra relocation target.
Prior intent replaces signal-sender identity. It does not authenticate the signal sender itself.
Neither design protects a broad kill of the application child or same-user SIGKILL.

Implementation remains pending. It will use a 30-second application shutdown budget and a 60-second new-unit stop timeout, including a 30-second margin.
The total real-core settlement bound remains open until its proof completes.
No existing unit, Mac executor, or LaunchAgent changes form part of this decision.
A managed Mac-only server requires a later decision.

Required proof, not yet completed:

- Isolated processes: bare-signal refusal, valid intent, wrong capability, expiry, old-incarnation replay, foreground stop, and child-plus-supervisor signal behavior.
- Hosted runner: actual new-unit `ExecStop`, installer stop, failed control socket through stop timeout, and startup failure before activation.
- Hosted runner: service restart with a new incarnation/capability, and stop during a concurrent application restart through the existing socket.
- Capability: file mode 0600, parent mode 0700, absence from application and every agent environment, and exclusion from all backup roots.
- Packaged supervisor: clean Node-only startup and relocation without a compiler or Python.

See [the accepted design](lifecycle/SUPERVISOR_DESIGN.md) for the protocol and failure limits.

## VPS metrics integer configuration

The migration Manager accepted this admission boundary for Unit 21 Part 2.
It applies to these ten integer environment overrides:

- `PLAN_VPS_METRICS_METRICS_PAYLOAD_BYTES`
- `PLAN_VPS_METRICS_PROCESS_PAYLOAD_BYTES`
- `PLAN_VPS_METRICS_PROCESS_SNAPSHOT_BYTES`
- `PLAN_VPS_METRICS_TRANSACTION_PAYLOAD_BYTES`
- `PLAN_VPS_METRICS_DATABASE_BYTES`
- `PLAN_VPS_METRICS_WAL_BYTES`
- `PLAN_VPS_METRICS_READ_WORKERS`
- `PLAN_VPS_METRICS_PROCESS_PAGE_ROWS`
- `PLAN_VPS_METRICS_HISTORY_POINTS`
- `PLAN_VPS_METRICS_SERVICE_OUTPUT_BYTES`

Python parses these overrides with `int()` and can preserve values beyond JavaScript's exact integer range, subject to existing configuration checks.
For example, Python accepts and retains `9007199254740993` for `history_points`.
TS explicitly refuses values outside ±9,007,199,254,740,991 with `ValueError: <field> exceeds the supported exact integer range`.
It does not silently round the example to `9007199254740992`.

The boundary exceeds realistic worker/page counts and memory/database budgets.
Explicit refusal keeps operational limits exact without spreading BigInt configuration through number-based runtime APIs.
Existing positive-value and consistency checks still apply.

The existing [configuration proof](../tests/compare/direct/test_unit21_metrics_config.py),
`test_unit21_metrics_configuration_and_sidecar_paths`, asserts the TS refusal as a gating result.
It records Python's different answer as informational evidence. The ten keys share the same admission predicate.
This policy acceptance does not approve the remaining Part 2 source or its merge.

## JavaScript source execution limits

The migration Manager accepted these limits for the new JavaScript script contract on November 12, 2026:

- 8 MiB combined control, stdout, and stderr output per run.
- 64 MiB aggregate decoded HTTP response bodies per run.
- 256 MiB V8 old-space through the child Node process option. This is not a total resident-memory guarantee.

Python enforces only the wall-time limit. Its configured default is 30 seconds (`src/planner/core/config.py` and `measure_sources/configuration.py`), equal to the TypeScript default. The configured wall-time value remains authoritative.

`measure_sources/runtime/runtime.test.ts` proves each limit with actual children:

- `stdout and private control frames share the output byte budget` proves combined output enforcement, including buffered output before completion.
- `decoded HTTP bytes accumulate across responses and exhaust the run budget` proves aggregate response enforcement even if a script catches the failure.
- `V8 old-space exhaustion is a safe budget failure and the child is reaped` proves heap exhaustion with a smaller test cap.
- `good child sees frozen data and the exact environment allowlist; parent retains numeric candidates` checks the actual default child option `--max-old-space-size=256`.

The byte/heap failure cases use smaller configured limits to avoid unnecessary VPS load. Default constants retain the accepted values above.

### Accepted script-contract differences after source review

The Manager accepted these differences on November 12, 2026:

- Python inherits the full server environment. TypeScript constructs a filtered environment with private `HOME`/`TMPDIR` and fixed `LANG`. It retains only `TZ`, `PATH`, `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, their lower-case forms, `SSL_CERT_FILE`, `SSL_CERT_DIR`, and `NODE_EXTRA_CA_CERTS`. No `PLAN_*` variable or provider home passes through. The environment test pins this exact list.
- Non-wall limit notices say "The run exceeded an execution limit." Python's existing wall-time notice stays unchanged.
- JavaScript diagnostics retain only `SyntaxError`, `TypeError`, `ReferenceError`, and `RangeError`. Python retains fourteen exception names.
- A script's explicit `process.exit(0)` is a runtime failure because it omits the completion protocol. Python treated `SystemExit(0)` as success.

The child runs as the same operating-system user and can read what the server can read, as in Python. Environment filtering is not a sandbox. The author prohibitions are policy, not filesystem or process isolation.

## Claude SDK identity and argument serialization

The migration Manager accepted these two differences on November 12, 2026, for Unit 18:

| Variable | Python SDK | TypeScript SDK |
| --- | --- | --- |
| `CLAUDE_CODE_ENTRYPOINT` | `sdk-py` | `sdk-ts` |
| `CLAUDE_AGENT_SDK_VERSION` | `0.2.152` | `0.3.316` |

The child identifies the SDK that actually runs it. TypeScript does not claim to be the Python SDK.
These are the only accepted launch-environment differences. All other variables compare exactly;
there is no blanket SDK-variable normalization. The public spawn hook preserves the Python
inherited-environment policy, including removal of `CLAUDECODE`, rather than forwarding the TS
SDK's additional flags or filtered environment.

The Manager also accepted these SDK argument facts on November 12, 2026:

- TS adds `--allow-dangerously-skip-permissions`, required for its bypass-permissions mode.
- Python separates the values of `--model`, `--effort`, `--permission-prompt-tool`, and
  `--permission-mode`. TS uses the equals form for those four options.
- The SDKs use different fixed option orders. The paired check asserts each complete
  argument template, with only the correlated session UUID substituted. Both use `--resume=X`.
- TS prefixes a script executable with `node`. Python executes its shebang path directly.

Raw argument arrays remain in the evidence. No generic option parser, sorting, wildcard,
or unknown argument is accepted by the comparison.

`conversation/backends/claude_sdk_environment.test.ts` pins the two TS values through the real
pinned SDK and the catalog's public spawn hook. It compares the entire child environment,
including inherited values and the removal of `CLAUDECODE`. Every process API is blocked before
SDK import, and the synthetic launch stops at the hook without a process. The same check
asserts that import and query construction leave the server environment unchanged.

Source inspection found no Panels storage, view, usage, catalog, or session-path consumer of
either variable. Panels reads usage and cost from result fields, models from the handshake,
and the session cursor from `session_id`. Provider-private session-file effects remain unverified;
no provider session file or owner executable was inspected for this check. The adapter follows
this accepted policy.
