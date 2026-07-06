# W2 codex diff-review — dispositions

Codex (gpt-5.5, high, read-only) on the W2 implementation diff. Raw output: `impl-review.out`.
Verdict **FAIL** (3 category-A items); clean on B (tests) / C (schema/migration) / D (W3 breaches) /
E (boundary guard) — all "No findings".

- **A3 — `ErrorCode.frozen_write` orphaned → ACCEPTED, FIXED.** The freeze surface's dedicated error
  code, already dead at the W1 baseline (`63152e5`, zero refs) and part of the freeze surface W2
  removes — leaving it was an inconsistent half-removal. Removed from `core/contracts.py`; re-ran
  ruff/mypy/unit → green. Deviation from plan §7 (which enumerated only `db_not_empty` for
  `ErrorCode` removal) — an enumeration gap (same class as the `config.yaml` A1 miss in plan-review),
  closed. **Integrator: accepted.**
- **A1 — `skills/planning-boundary.md` still documents the removed plan_tree/replan contract**
  (root/child replans) and is loaded live by `real.py` (`--skills planning-boundary`). → ACCEPTED
  valid, **DEFERRED** to the rollover-rebuild wave.
- **A2 — `skills/planner-main.md` still says `plan day show` returns a "plan tree" and references
  accept/invalidate/accept-all/reject-all day-plan actions.** → ACCEPTED valid, **DEFERRED**.

**A1/A2 deferral rationale (integrator-confirmed):** `skills/*.md` are Hermes agent-prompt content,
not in W2's owned-file set, not flagged by the plan-review, and not verify-blocking (the real
boundary adapter isn't test-exercised; `real.py`'s judgment prompt already self-describes its 4
fields regardless of the stale skill). The boundary/rollover skill is owned by the later
**rollover-rebuild wave**, which rewrites it — editing now would exceed W2's mechanical-deletion
scope and risk conflicting with that wave. Tracked as a rollover-wave follow-up.
