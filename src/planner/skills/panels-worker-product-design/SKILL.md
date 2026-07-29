---
name: "panels-worker-product-design"
description: "Stage-by-stage guidance for holistic product-design Tickets."
---

# Product design

Design how the product works before polishing how it looks. Optimize above all for
usability: guide people toward the intended actions with effective hierarchy, make
the product and its behavior understandable, and let the design reflect its purpose.
Simplicity can help, but is not the definition of usability.

### needs_kickoff

Capture the product problem, intended outcome, relevant surface, and known constraints.
Keep this outcome-level; do not hide an assumed solution inside the kickoff.

### needs_direction

Inspect the real product, then make a concise first best guess from that evidence and
the kickoff. Note the top-level issues, the intended flow, and consequential assumptions.
Be opinionated but easy for the user to correct; this is not yet a specification or
wireframe.

### needs_wireframe

Work with the user to make the approved direction tangible at low fidelity. Use a
ticket-owned interactive HTML wireframe where behavior matters. Validate structure,
hierarchy, interactions, and important states before polish. Alternatives should differ
meaningfully, not cosmetically.

### needs_design

Develop the approved wireframe into a ticket-owned interactive HTML design and refine it
with the user. Inspect the repository and adopt its established patterns, tokens, and
reusable components by default. Any departure must be explicit and justified; accidental
divergence is a design failure.

Use representative content and states, exercise the important interactions, and inspect
the result in a browser at relevant viewport sizes. If polish exposes a flow problem,
correct it. Render Markdown on a recessed—not flat or raised—surface.

### needs_closeout

Write a concise implementation bar for the approved artifact and create the downstream
`coding` Ticket with the artifact link, bar, and applicable product context. Load and
follow `panels-ticket-creation` for the shared creation model; the approved design fixes
the handoff's scope and this stage fixes its Worker type as `coding`. Verify the handoff.
It is final: coding owns implementation and all later feedback.
