# Temporary operator for one Claude diagnostic

The operator accepts the reviewed receipt with `diagnostic_status`. It consumes the receipt once and saves only validated finite output.
A validation or storage failure ends the attempt. It does not authorize another retrieval or another arm.

The application Worker must verify matching application and operator packages before any arm.
The package handoff must retain the final revision, archive hashes, exact script, shared validator, and operator module.
The same revision must identify both packages. A later application decision still owns activation and readiness checks.
These files do not authorize a live attempt.

## Package binding

`diagnostics/run-catalog-diagnostic-once.py` is the updated entry script.
It uses `planner.executor.catalog_diagnostic_operator.run_once` and the shared receipt validator.
The entry script accepts a separate reviewed JSON file with exactly these keys:

```json
{
  "application_revision": "<full final checked Git revision>",
  "operator_revision": "<same full final checked Git revision>",
  "source_sha256": {
    "operator_script": "<SHA256 of the exact entry script>",
    "operator_module": "<SHA256 of the installed catalog_diagnostic_operator.py>",
    "receipt_contract": "<SHA256 of the installed diagnostic_contracts.py>"
  }
}
```

The placeholders are explanatory. The handoff must replace each placeholder with the final immutable identity.
Create the binding after the final checked commit and complete application package exist.
Retain the binding beside those packages. Retain archive hashes separately to avoid a circular hash inside an archive.
The binding is review metadata, not a signature or a new native protocol.

The script requires these explicit arguments:

```text
REVIEWED_PREFIX/bin/python -I run-catalog-diagnostic-once.py \
  --binding reviewed-operator-binding.json \
  --expected-prefix REVIEWED_PREFIX \
  --output NEW_FINITE_OUTPUT_DIRECTORY \
  --private-output NEW_PRIVATE_OUTPUT_DIRECTORY
```

Run that command only in a separately authorized application window.
Use the reviewed installed interpreter. Do not bypass a mismatch.
The script rejects another interpreter prefix and altered script, operator module, or validator bytes before any HTTP request.
It then verifies both application and native health revisions before reservation or arm.
The existing protocol and spool versions remain 12 and 2.
The application Worker must also verify complete installed package identity through its ordinary application procedure.
These narrow source checks do not replace that procedure or prove Mac dependencies on Linux.

The script retains the original application target, native command, workspace, Ticket preview selection, limits, and deadlines.
Any target change requires a reviewed script and new source hashes before its use.
There is no automatic provider attempt during import, package preparation, or verification.

## One-attempt behavior

The entry script follows the existing health, reservation, arm, ordinary preview, and result sequence.
The native result CLI consumes the one-use receipt through the existing socket path.
`retrieve_once` checks the closed shared contract and the 4 KiB bound before it saves finite output.
The public finite file contains no operational metadata or raw rejected payload.
The separate private directory holds ordinary output and bounded transport counts.
A separate operation receipt records completion or an inconclusive stop.

Both output directories must be new. Files use exclusive creation.
An exception ends the operation. The operation receipt marks consumption as consumed or uncertain when the operation fails.
Do not repeat a consumed or uncertain attempt. Do not rearm the runtime.
A file failure after retrieval does not restore native authority.

The operator does not convert old evidence. An old receipt lacks `diagnostic_status` and remains unclassified.
The new validator rejects that old shape and any unexpected top-level or role field.
The old generic CLI prints the new finite JSON, but its byte compatibility does not qualify mixed application packages.
The retained old operator rejects the new receipt after consumption and pins the old revision before activation.
Do not reuse or bypass it.

## Offline evidence

The focused unit tests exercise the actual `run_once` orchestration with isolated transport callbacks.
They prove that mismatched source bytes or either installed revision stops before reservation and arm.
They prove that one retrieval reaches validation and a finite output file.
They also prove that old receipts and unexpected fields do not reach that file or trigger another retrieval.
A storage-failure case protects the consumed-receipt boundary.

The historical fixtures are exact files from commit `684f29d5ca95c9807277325ce452f601da06bb3a`:

| Fixture | SHA256 |
| --- | --- |
| `tests/fixtures/catalog_diagnostic_684f29d5/diagnostics.py.txt` | `aa2761ade08b54f53dc5c91926d51b014aa8ab8542b3e9b4c1a1a3c0dc922a85` |
| `tests/fixtures/catalog_diagnostic_684f29d5/cli.py.txt` | `fa66d0d262f8fe307c1624183512841ce4e92b23ec8bf18233d0cbd972ec9f93` |

Tests check these hashes before isolated imports. No legacy validator is reconstructed by hand.
The old generic CLI test injects its request result and performs no socket operation.
The connected test uses the real native result transport and the same `retrieve_once` path to save validated output.
This additional connected proof protects native retrieval, finite validation, and preservation across their shared boundary.
No browser host is necessary.

## Removal inventory

Remove the temporary operator script, operator module, focused operator tests, and historical compatibility fixtures with the diagnostic follow-up.
Also remove the shared finite contract and its status field with the existing temporary reservation, native controller, comparator, and hooks.
Retain the reviewed immutable package, binding, recovery sources, and sanitized finite evidence as investigation artifacts.
