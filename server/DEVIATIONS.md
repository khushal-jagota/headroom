# Accepted differences from Python

This file records intentional differences that the migration Manager accepted. No new difference enters this file without that acceptance. Close an entry when Python adopts the same behavior.

## Video chunk writes outside the write door

The migration Manager accepted this difference for Unit 19. Python writes each chunk
inside its write transaction. TypeScript checks capacity and state, writes the chunk
outside the write door, then commits the progress count in a short transaction.
Neither hashing, media probing, nor file copying occurs under that door.

The direct upload sequence compares Python and TS answers, rows, and exact file bytes
after every operation. It includes identical repeated chunks, an interrupted stream,
and a retry with the same bytes. Repeated bytes append on both sides. A failed or
interrupted transfer does not become resumable. The proof is
`tests/compare/direct/test_unit_19_uploads.py`, with its export-only companion under
`conversation/video_uploads/testing/`. The real HTTP list also transfers a playable
MP4, probes it, and sends it through Ticket and Chief doors.

During a TS write, another reader can see the previous progress count, and another
writer can cancel the upload. Python serializes other writers until its chunk commit.
TS checks the state again before its progress commit. A cancellation follows the same
failure cleanup path and cannot restore a ready upload.

A crash between the TS file write and progress commit can leave temporary bytes ahead
of the stored count. Python can also leave bytes ahead if its transaction rolls back
after the kernel receives a write. Its buffered output can leave bytes behind a
committed count until a buffer flush. Both flush the complete file before probing.
At the next start, both retain a nonexpired `transferring` row, refuse a fresh transfer,
and retain the quota reservation. Neither resumes it or declares it ready. At expiry,
cleanup removes the temporary file and marks the row cancelled. The detailed ordering
analysis is in [the upload README](conversation/video_uploads/README.md).

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

The Manager accepted prior authenticated stop intent followed by the exact supervisor signal instead of Python's kernel-sender and verified-manager check. This preserves accidental bare-signal refusal without a compiled wrapper. Foreground behavior stays ordinary. Implementation remains pending, with the capability, restart-race, and hosted failure proofs listed in [the lifecycle deviation](lifecycle/DEVIATIONS.md).

## Claude SDK identity in the child environment

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

`conversation/backends/claude_sdk_environment.test.ts` pins the two TS values through the real
pinned SDK and the catalog's public spawn hook. It compares the entire child environment,
including inherited values and the removal of `CLAUDECODE`. Every process API is blocked before
SDK import, and the synthetic launch stops at the hook without a process.

Source inspection found no Panels storage, view, usage, catalog, or session-path consumer of
either variable. Panels reads usage and cost from result fields, models from the handshake,
and the session cursor from `session_id`. Provider-private session-file effects remain unverified;
no provider session file or owner executable was inspected for this check. The adapter follows
this accepted policy in Unit 18's next part.
