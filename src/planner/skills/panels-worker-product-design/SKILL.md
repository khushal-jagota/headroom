---
name: "panels-worker-product-design"
description: "Stage-by-stage guidance for holistic product-design Tickets."
---

# Product design

Design how the product works before polishing how it looks. Optimize above all for
usability: guide people toward the intended actions with effective hierarchy, make
the product and its behavior understandable, and let the design reflect its purpose.
Simplicity can help, but is not the definition of usability.

### needs_brief

Capture the product problem, intended outcome, relevant surface, and known constraints.
Keep this outcome-level; do not hide an assumed solution inside the Brief.

### needs_best_guess_and_open_options

This Stage is words, not pictures. No mockup, no HTML, no wireframe — a short description
the user can check at a glance. The proposal is the sense check: approving it is how they
confirm you have understood.

Write two parts, and keep them apart.

**Best guess.** Explain simply what the thing should be. Ground it in the Brief and in what
the product already does, so inspect the real product first and do not contradict behaviour
that already exists. State the rules and logic that are settled: what has to be shown, what
has to be true, what the Brief already decides. Be opinionated and easy to correct.

**Open options.** Name what is not confirmed. These are the places a different answer is
plausible, and they are what the Wireframe will be asked to show as real alternatives, so
name them here or they will not be built. Say what each option is about and what would make
one of them right, rather than picking a favourite. If the guess genuinely settles
everything, say so; an empty list is an answer, a missing one is not.

The user is checking two things: that the settled part matches what they decided, and that
the open part is genuinely open. "We know what needs to be shown, we have no idea how to
show it yet" is the normal shape of a good one.

### needs_wireframe

Work with the user to make the approved Best Guess and Open Options tangible at low
fidelity. Use a ticket-owned interactive HTML wireframe where behavior matters. Validate
structure, hierarchy, interactions, and important states before polish. Alternatives
should differ meaningfully, not cosmetically.

### needs_design

Develop the approved wireframe into a ticket-owned interactive HTML design and refine it
with the user. Inspect the repository and adopt its established patterns, tokens, and
reusable components by default. Any departure must be explicit and justified; accidental
divergence is a design failure.

Use representative content and states, exercise the important interactions, and inspect
the result in a browser at relevant viewport sizes. If polish exposes a flow problem,
correct it. Render Markdown on a recessed—not flat or raised—surface.

### needs_consequences

Write a concise implementation bar for the approved artifact and create the downstream
`coding` Ticket with the artifact link, bar, and applicable product context. Load and
follow `panels-ticket-creation` for the shared creation model; the approved design fixes
the handoff's scope and this stage fixes its Worker type as `coding`. Verify the handoff.
It is final: coding owns implementation and all later feedback.
