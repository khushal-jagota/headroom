# ACP-10 canonical-gate fixture closure — focused checks and disposition

Verdict: **READY for a fresh no-writer snapshot.**

The retained failed log named exactly four Ruff sites and 14 unit failures. The correction changed
only the contract's allowlisted source formatting and test fixtures:

- organized one existing package import block and wrapped three existing SQL statements;
- added the already-required `default_employee_backend` to exact expected Worker manifests;
- passed the already-installed backend catalog into two registry-reconstruction fixtures;
- added the already-required backend column/value to one legacy table fixture; and
- added `employee_backend="hermes"` to two direct `Ticket(...)` helpers.

Root inspected the combined diff. Strict manifest equality remains, no production behavior changed,
and no assertion was deleted or weakened. The earlier ACP evidence and independent settled-tree review
are not invalidated by these fixture/formatting-only corrections.

Combined focused gate:

```text
.venv/bin/ruff check <the 11 exact Lane A/B/C files>
All checks passed!

.venv/bin/pytest <the 10 exact unit files implicated by the failed run>
76 passed, 2 warnings in 0.85s

git diff --check
PASS
```

No full `./verify`, server, browser, package, dependency, or generated-asset action occurred during
the correction. The prior failed full log remains retained. Under the owner's proportional-review
rule, these observed mechanical fixture fixes need root diff inspection plus the exact focused gate,
not another broad sub-agent review.
