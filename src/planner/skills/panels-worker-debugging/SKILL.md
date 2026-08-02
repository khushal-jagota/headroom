---
name: panels-worker-debugging
description: Stage-by-stage guidance for diagnosing a software bug and defining its fix without implementing it.
---

# Debugging ticket stages

This Worker works out what bug is being described, finds its structural cause, and defines
the implementation handoff. It owns the investigation and uses whichever evidence fits
the bug. Keep facts distinct from inference and conclusions concise.

## Runtime app boundary

`/home/vps/Deployments/Panels/current/app` is the deployed runtime, not a source checkout.
Never use it as the current working directory, a test root, or a source tree. Inspect it only
through logs or service state, or through an isolated copy when its deployed revision must be
examined. Run tests from the assigned checkout or an isolated deployed-revision copy; create a
suitable checkout when one is not available.

### needs_kickoff — receive the bug report

The Kickoff should describe the reported bug, its context, and any evidence already
available. Treat it as the starting point to interpret, not necessarily a complete
description and not as the diagnosis.

### needs_problem_understanding — understand the reported problem

Work from the Kickoff to understand what problem the user means. Resolve unclear references
or missing context; inspect code, dogfood, or reproduce only where that helps clarify the
report. If essential information is unavailable, request it with a bounded question.

Propose the problem in your own words so the user can check that you understood it. State
the relevant expected and observed behavior and conditions where known, but do not diagnose
the cause or suggest a fix yet.

### needs_structural_diagnosis — establish why it is broken

Trace the confirmed problem to the earliest broken contract, invariant, or system boundary.
Test plausible alternatives and separate the root cause from contributing conditions and
symptoms. If a system is incorrect, find where and why.

An actual cause is expected. If genuine uncertainty remains after reasonable investigation,
give ranked hypotheses, confidence, and the next discriminating check.

### needs_solution — define the fix

Derive the smallest coherent fix from the cause, at the boundary that owns it. Name the
required implementation Tickets and relevant dependencies or verification. Do not
implement the fix or add unrelated redesign.

### needs_closeout — hand off

Leave a short orientation to the diagnosis, solution, outstanding implementation work,
and any residual uncertainty.
