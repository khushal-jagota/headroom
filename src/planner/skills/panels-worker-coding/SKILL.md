---
name: "panels-worker-coding"
description: "Stage-by-stage guidance for a coding Worker type Panels Ticket."
---

# Coding ticket stages

### The stages

Each stage is named for what the ticket needs next; your step is to give it that. The visible sequence is **Success Condition → What Changes → Plan → Implementation → Consequences → Done**.

- **needs\_success** — needs its **Success Condition**: what "done" would mean.
- **needs\_approach** — needs **What Changes**: how it will be done.
- **needs\_plan** — needs its **Plan**: the concrete steps.
- **needs\_implementation** — needs its **Implementation**. Implementation follows the plan, performs the work, and proposes a concise, reviewable package with concrete evidence.
- **needs\_closeout** — needs its **Consequences**. It performs only the applicable merge, deploy, follow-up, and bookkeeping, then proposes a concise, verified report.
- **done** — finished.

### How to complete ticket stages effectively

- **needs\_success** — a good **Success Condition** says plainly what "done" means for this ticket, grounded in the real work. Keep it human-readable and outcome-level.

- **needs\_approach** — a good **What Changes** names what needs to change for success to be achieved. It does not need to get into how we will do those things, but needs to state what needs to be done. where you'll get data from to do x is useful here, the structure of the function isn't. Use system diagrams where they would be clearer than text.

- **needs\_plan** — a good **Plan** goes through how we will carry out What Changes. It should be concise and sequenced, not an exhaustive engineering spec.

- **needs\_implementation** — do the actual work then propose a package similar to a PR. Where work is non-trivial, dogfood the work in the browser and capture evidence of success. Always do your work on a worktree and a branch. Make three things clear:

  - **Intent:** the outcome of the ticket this work fulfills.
  - **How it was done:** the important work and choices that produced the result. Include diagrams or pictures when they improve understanding.
  - **Evidence of completion:** show or link the final artifact where there is one, plus the relevant verification. For visual or interactive work, show screenshots or PNGs of the real output when appropriate rather than only saying it was checked.

  Keep the package proportionate to the work. These are review principles, not mandatory headings or a mechanical checklist.

- **needs\_closeout** — complete the repository-defined integration and cleanup route before proposing **Consequences**. For a branch-based route, bring the current target base into the feature branch, resolve and verify the prospective result there, and advance the target only after its required checks pass. Fix any issues here and record evidence. A failed attempt leaves the target unchanged and keeps its exact continuation point visible. If integration cannot finish, keep the Ticket at `needs_closeout` and request user help. Deployment is a later user action, not a substitute for completed integration. A good **Consequences** is a short, verified report of success and how it was checked.
