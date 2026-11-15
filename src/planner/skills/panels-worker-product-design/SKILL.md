---
name: "panels-worker-product-design"
description: "Stage-by-stage guidance for holistic product-design Tickets."
---

# Product design

Design how the product works before polishing how it looks. Optimize above all for usability: guide people toward the intended actions with effective hierarchy, make the product and its behavior understandable, and let the design reflect its purpose. Simplicity can help, but is not the definition of usability.

## Use Anthropic design craft when necessary

Use `frontend-design` as necessary for hierarchy, rhythm, restraint, and interface craft. Preserve the approved direction and the house style.

`assets/tokens.css` takes precedence over `frontend-design` guidance for every color, radius, duration, spacing step, border width, size, and font value.

## Experience the work, at every Stage

Inspecting the repository tells you what exists. It does not tell you how the thing feels, and feel is what you are designing. Open the real surface, use it, and watch it — before you write the Brief, before you build options, and before you refine the chosen one.

- Use the actual product, at the actual size, with real content and real states.
- Exercise the interaction you are changing. Click it, type into it, let it load, let it fail.
- Where the work is motion or timing, watch it over time. A check of the markup passes on an animation that looks wrong.
- When you cannot reach the real surface, say so in the proposal. Never describe a feel you have not felt.

## Placeholders, in the Wireframe and the Design

Build what matters to experiencing the thing being designed. Place everything else. The test is how much something matters to the experience, not fidelity, and it is the same test at both Stages.

The user's own examples carry the rule:

- If the conversation is what you are designing, the sidebar does not need to be built. A black placeholder block in the animation is likely fine.
- If the model picker is out of scope, clicking the model opens a blocked-out popover with placeholder text saying something like "as in the main product".

So hold the things being judged, and use placeholders for what is out of scope, according to how much each one matters to experiencing the wireframe or the design.

## Copy is not the deliverable

The artifact is there to be experienced, not read. Writing in it is a failure, not a service. The
user has said this plainly: he does not want to read your designs, he wants to experience them.

Write only what he needs in order to know what he is looking at, and at most one line for a
decision you need from him. Then stop. As a working target, aim for a tenth of the words you
would naturally write, and treat any paragraph in the artifact as a fault to be removed.

Cutting is not moving. Most of what comes out of the artifact should be deleted, not relocated
into the proposal, because the proposal is read by the same person and is also too long today.
Rationale, what you considered, how you arrived at it, what you measured, how an option differs,
what you are unsure about — he is not going to read any of it in either place, and writing it
somewhere else is the same failure with a different filename.

Three homes, and most things have none of them:

- **The artifact** — what he needs in order to know what he is looking at. Nothing else.
- **The proposal** — a few lines. What this is, what you need him to decide, and anything that
  would actually change that decision. If it would not change his answer, leave it out.
- **The handoff at Consequences** — the detail an implementer needs, written after he has
  decided, for a reader who is not him.

A reviewer who has to read the artifact to understand the design is being asked to do the
design's job for it. A reviewer who has to read a page of proposal to decide between two
options is being asked to do it twice.

One distinction, so this is not applied destructively. Text that is part of the product — labels,
headings, button names, real message content, anything a person would see in the shipped thing —
is design, not copy, and it must be real and accurate. What is being cut is your writing about
the design: annotations, legends, explanatory blocks, option descriptions, instructions to the
reader, and narrative framing.

If a design needs a paragraph to be understood, the design is not finished.

## Say what changed, and represent the rest honestly

Know every way your proposal differs from today, and never let one arrive unannounced. A
difference the user finds that nobody declared is a defect, not a detail.

The list itself does not go in the artifact, and it does not go in the proposal either. Both are
read by the person who has told us he does not want to read. It is written at Consequences, in
the handoff, for the implementer who needs it. The obligation is to know and to declare; the
place to declare it is the handoff.

Two exceptions keep the obligation honest. A difference big enough that he would object to
discovering it is a decision, and a decision gets its one line in the proposal before he
approves. And if he asks what changed, you answer in full, immediately, however long that is.

Whatever you do draw of today has to be the real product. The artifact is the specification the
coding Worker executes, so an inaccurate today is built as though somebody chose it.

You do not have to represent everything. Block out whatever the change does not concern, as a
placeholder roughly the size of the real thing with placeholder text naming it: designing a
Ticket page, the sidebar can be a black block that says sidebar. The choice is accurate or
blocked out. Never approximate.

### needs\_brief

Capture the product problem, intended outcome, relevant surface, and known constraints. Keep this outcome-level; do not hide an assumed solution inside the Brief.

### needs\_wireframe

This Stage decides what the thing is. It is not a lower-fidelity Design.

Hold true to the Brief, and to the code. What the Brief settles is not open, and what the product already does is the starting point unless the Brief says otherwise. Nothing checks your understanding before this artifact, so this is where the user confirms you understood them. A wireframe that drifts from the Brief fails here, however good it looks.

Then give options on what is genuinely open. An option earns its place by being a different answer to a real question — not a cosmetic variant, and not a second opinion on something the Brief already decided. Two or three real options beat five near-identical ones.

Make each option good enough and different enough to decide from. The user picks roughly what they want and can take elements from several options into one, so keep the options separable. An option built as one indivisible whole cannot be taken apart.

Build a ticket-owned interactive HTML artifact. Behavior is part of the decision, so the interactions that carry the decision must work, and you must exercise them yourself. Then read the artifact itself end to end, as the user will: open it, scroll the whole page, and look at it rather than at the parts you built. Exercising a control is not reading a page, and a layout fault that destroys what is being judged survives every measurement you can run on it. Say in the proposal that you did this and what you saw. Ground what you reuse in the current codebase and do not reinvent anything outside the Brief's scope.

### needs\_design

This Stage optimizes what was chosen. The decision is made. Do not reopen it and do not offer new directions here.

Take the chosen thing, including any combination the user assembled out of several options, and make it right: hierarchy, spacing, type, color, states, transitions, and the content that does not fit. Adopt the repository's established patterns, tokens, and reusable components by default. Any departure must be explicit and justified; accidental divergence is a design failure.

Build the proper HTML of the version that will actually be used as the design artifact. The coding Ticket implements this file, so it has to be the real thing and not a sketch of it.

Use representative content and states, exercise the important interactions, and inspect the result in a browser at relevant viewport sizes. If optimizing exposes a flow problem, correct it and say that you did. Render Markdown on a recessed—not flat or raised—surface.

### needs\_consequences

Write a concise implementation bar for the approved artifact and create the downstream `coding` Ticket with the artifact link, bar, and applicable product context. Load and follow `panels-ticket-creation` for the shared creation model; the approved design fixes the handoff's scope and this stage fixes its Worker type as `coding`. Verify the handoff. It is final: coding owns implementation and all later feedback.
