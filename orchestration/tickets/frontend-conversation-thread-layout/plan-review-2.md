# Plan re-review: Frontend conversation thread layout

## Standards

**PASS.** The two amendments preserve the locked deep-module interface and keep that
interface as the test surface. The Standards verdict from the first review is
unchanged.

## Spec

**PASS.**

- Steps 1 and 3 now explicitly remove the two local `ThreadItem` aliases and derive
  the turn-time suite's `TurnItem` from the imported locked type. The initial RED can
  therefore be the missing module alone, and the suites can become GREEN through the
  new interface without duplicate declarations.
- The settled audit now lists every `export` declaration and requires the complete
  result to be exactly `ThreadItem` and `threadItems`. A forbidden third export can no
  longer pass the audit unseen.

Both findings from `plan-review.md` are resolved. There are no unresolved Standards
or Spec findings.
