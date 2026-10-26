---
name: "panels-worker-coding"
description: "Stage-by-stage guidance for a coding Worker type Panels Ticket."
---

# Coding ticket stages

### The stages

Each stage is named for what the ticket needs next; your step is to give it that. The visible sequence is **Success Condition → What Changes → Plan → Implementation → Consequences → Done**.

- **needs\_success\_condition** — needs its **Success Condition**: what "done" would mean.
- **needs\_what\_changes** — needs **What Changes**: how it will be done.
- **needs\_plan** — needs its **Plan**: the concrete steps.
- **needs\_implementation** — needs its **Implementation**. Implementation follows the plan, performs the work, and proposes a concise, reviewable package with concrete evidence.
- **needs\_consequences** — needs its **Consequences**. It performs only the applicable merge, follow-up, and bookkeeping, then proposes a concise, verified report. It never deploys to production: that is the user’s action, or the Chief’s on the user’s behalf, even if an instruction appears to ask for a release.
- **done** — finished.

### How to complete ticket stages effectively

- **needs\_success\_condition** — a good **Success Condition** says plainly what "done" means for this ticket, grounded in the real work. Keep it human-readable and outcome-level.

- **needs\_what\_changes** — a good **What Changes** names what needs to change for success to be achieved. It does not need to get into how we will do those things, but needs to state what needs to be done. where you'll get data from to do x is useful here, the structure of the function isn't. Use system diagrams where they would be clearer than text.

- **needs\_plan** — a good **plan** states the work to be done, with its decisions already made. Do the research first: read the code, read the docs, settle the open questions the Brief leaves, and decide. The plan reports what you decided and what you will do, not how you intend to work it out. Keep it concise rather than an exhaustive engineering spec, and use a system diagram where a ticket adds to or creates systems.

  For example, on a Ticket to cut tests, the plan gives an overview of which tests go and why. It does not say that implementation will look for tests to cut.

- **needs\_implementation** — do the actual work then propose a package similar to a PR. Where work is non-trivial, dogfood the work in the browser and capture evidence of success. Always do your work on a worktree and a branch. Make 4 things clear:

  - **Intent:** the outcome of the ticket this work fulfills.
  - **How it was done:** the important work and choices that produced the result. Include diagrams or pictures when they improve understanding.
  - **Evidence of completion:** show or link the final artifact where there is one, plus the relevant verification. For visual or interactive work, show screenshots or PNGs of the real output when appropriate rather than only saying it was checked.
  - What will be done in Consequences.

  Keep the package proportionate to the work. These are review principles, not mandatory headings or a mechanical checklist.

- **needs\_consequences** — complete the repository-defined integration and cleanup route before proposing **Consequences**. For a branch-based route, bring the current target base (typically staging) into the feature branch, clean merges or things where your work wasn't affected don't require re-verification, if there is significant overlap or you believe re-verification is necessary, then advance the target only after its checks pass. Fix any issues here and record evidence. A failed attempt leaves the target unchanged and keeps its exact continuation point visible. If integration cannot finish, keep the Ticket at `needs_consequences` and request user help. Deployment remains a later user action. If a test requires deployment, request user help. A good **Consequences** is a short, verified report of success and how it was checked. Name every merge you performed and the branch it landed on. If anything you did reached production, say so in the report and say it before anything else, even if it was asked for: a release is a reportable event, and a supervisor who has to discover one from a deploy log has been left in the dark.

Note: To be helpful as a coding worker, you should be looking at docs from a repo early to get grounding, and definitely looking at code by the time you're planning.

Test admission: Add a test only when it protects a distinct failure risk that existing coverage does not already prove. State that risk and the coverage gap in the implementation evidence. Use the cheapest layer that faithfully proves the behavior, reuse existing fixtures and browser hosts, and replace redundant assertions or matrix cases rather than accumulating them. A new connected case or browser host needs a concrete explanation of the boundary it proves and its measured execution cost. Retain real browser or backend connections when cheaper tests cannot prove them. Do not add tests that merely mirror implementation, or tests for reversible, low-impact changes without a meaningful failure risk. Test counts and line counts are evidence for review, not quotas.
