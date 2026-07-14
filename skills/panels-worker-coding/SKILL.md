---
name: panels-worker-coding
description: Stage-by-stage guidance for a coding Worker type Panels Ticket.
---

# Coding ticket stages

### The stages

Each stage is named for what the ticket needs next; your step is to give it that. The visible sequence is **Success → Approach → Plan → Implementation → Closeout → Done**.

- **needs_success** — needs its **success**: what "done" would mean.
- **needs_approach** — needs its **approach**: how it will be done.
- **needs_plan** — needs its **plan**: the concrete steps.
- **needs_implementation** — needs its **implementation**. Implementation follows the approved plan, performs the work, and proposes a concise, reviewable package with concrete evidence.
- **needs_closeout** — needs its **closeout**. Closeout begins only after Implementation is approved. It performs only the applicable merge, deploy, follow-up, and bookkeeping, then proposes a concise, verified report.
- **done** — finished.
- **dropped** — abandoned.

### How to complete ticket stages effectively

- **needs_success** — a good **success** says plainly what "done" means for this ticket, grounded in the real work. Keep it human-readable and outcome-level; avoid turning it into an implementation checklist.
- **needs_approach** — a good **approach** names the method and the real routes. Keep it short enough to compare and approve; avoid burying the choice in technical detail.
- **needs_plan** — a good **plan** is concrete enough that the work can start from it. It should be concise and sequenced, not an exhaustive engineering spec.
- **needs_implementation** — do the actual work, then package it so the user can review the result rather than read a work log. Make three things clear:
  - **Intent:** the outcome of the ticket this work fulfills.
  - **How it was done:** the important work and choices that produced the result. Include diagrams or pictures when they materially improve understanding.
  - **Evidence of completion:** show or link the final artifact where there is one, plus the relevant verification. For visual or interactive work, show screenshots or PNGs of the real output when appropriate rather than only saying it was checked.

  Keep the package proportionate to the work. These are review principles, not mandatory headings or a mechanical checklist.
- **needs_closeout** — perform only the merge, deploy, follow-up, and bookkeeping steps that actually apply; a good **closeout** is a concise, verified report of what was closed out and how it was checked.
