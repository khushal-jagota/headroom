# Closing two-axis review

## Standards

```text
No standards findings.
```

## Spec

- `employee_step_runner.py` creates shutdown snapshot and settlement SQLite connections with the
  normal fixed busy timeout even when the absolute deadline is exhausted. A contended initial
  journal-mode operation or settlement write can therefore outlive the one configured shutdown
  deadline. Derive DB lock waits from remaining time, make them non-blocking at zero, and add a
  locked-database regression.

Summary: Standards has zero findings. Spec has one deadline-bounding finding.
