# ACP-02 plan review — final orchestrator disposition

The focused round-1 review found eight blockers. The narrow correction check marked findings 1, 2,
4, 5, 7, and 8 resolved and left only findings 3 and 6 open. The repository's two-round review cap
is now exhausted, so the orchestrator inspected and dispositioned those two exact items rather than
commissioning another review.

## Finding 3 — resolved

The plan now installs an exact employee/binding/child/record/capture-transaction planned-retirement
token before closing generation N. The matching intentional-close callback consumes that token,
settles only the private capture-retirement future, and does not enter ordinary unexpected-child-
death cleanup. A non-null error or mismatched token remains unexpected. The capture actor awaits this
settlement before spawning N+1, and the test map delivers N's callback before N+1 publication.

The allowed existing-file scope now names `employee_registry.py` and the exact lease, planned-
retirement, capture-replacement, and source-aware permission operations. It no longer describes the
registry work as a one-line delegation.

## Finding 6 — resolved

Hermes summary extraction now accepts every pinned replay placement: standalone user, standalone
assistant, merged user tail, and merged assistant tail. Standalone and merged candidates require the
exact pinned prefix/delimiter/end-marker structure. Exactly one structural candidate across both
roles is required; zero or multiple candidates fail visibly. The plan does not claim ACP replay
carries Hermes' private `_compressed_summary` provenance.

## Verdict

**READY** for ACP-02 implementation after ACP-01 integration. No ACP-02 source work may begin before
the corrected ACP-01 runtime passes its narrow implementation-review disposition.
