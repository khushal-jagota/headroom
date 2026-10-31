---
name: "panels-worker-coding"
description: "Stage-by-stage guidance for a coding Worker type Panels Ticket."
---

# Coding ticket stages

### The stages

Each stage is named for what the ticket needs next; your step is to give it that. The visible sequence is **Brief → What Changes → Implementation → Consequences → Done**.

- **needs\_brief** — needs its **Brief**: the job, its purpose, and its boundaries.
- **needs\_what\_changes** — needs **What Changes**: what needs to change.
- **needs\_implementation** — needs its **Implementation**. Implementation performs the approved work, and proposes a concise, reviewable package with concrete evidence.
- **needs\_consequences** — needs its **Consequences**. It performs only the applicable merge, follow-up, and bookkeeping, then proposes a concise, verified report. It never deploys to production: that is the user’s action, or the Chief’s on the user’s behalf, even if an instruction appears to ask for a release.
- **done** — finished.

### How to complete ticket stages effectively

- **needs\_what\_changes** — a good **What Changes** names what needs to change to achieve what the Brief outlines, in plain language. It does not need to explain how we will do those things. Where the data comes from is useful here, the structure of the function is not. Use system diagrams where they are clearer than text.

  Settle anything whose answer changes what we do or guard. Investigate, do not build. Leave anything that only changes how the work gets done. Mention guards, added machinery, commitments, or limits where relevant, with their reasons. Say plainly what the Ticket cannot establish. Include one sentence on what we will prove and why.

- **needs\_implementation** — do the actual work then propose a package similar to a PR. Where work is non-trivial, dogfood the work in the browser and capture evidence of success. Always do your work on a worktree and a branch. Make 4 things clear:

  - **Intent:** the outcome of the ticket this work fulfills.
  - **How it was done:** the important work and choices that produced the result. Include diagrams or pictures when they improve understanding.
  - **Evidence of completion:** show or link the final artifact where there is one, plus the relevant verification. For visual or interactive work, show screenshots or PNGs of the real output when appropriate rather than only saying it was checked.
  - What will be done in Consequences.

  Keep the package proportionate to the work. These are review principles, not mandatory headings or a mechanical checklist.

- **needs\_consequences** — complete the repository-defined integration and cleanup route before proposing **Consequences**. For a branch-based route, bring the current target base (typically staging) into the feature branch, clean merges or things where your work wasn't affected don't require re-verification, if there is significant overlap or you believe re-verification is necessary, then advance the target only after its checks pass. Fix any issues here and record evidence. A failed attempt leaves the target unchanged and keeps its exact continuation point visible. If integration cannot finish, keep the Ticket at `needs_consequences` and request user help. Deployment remains a later user action. If a test requires deployment, request user help. A good **Consequences** is a short, verified report of success and how it was checked. Name every merge you performed and the branch it landed on. If anything you did reached production, say so in the report and say it before anything else, even if it was asked for: a release is a reportable event, and a supervisor who has to discover one from a deploy log has been left in the dark.

Note: Read the repository documentation early for context, and read the relevant code before the What Changes proposal.

Test admission: Add a test only when it protects a distinct failure risk that existing coverage does not already prove. State that risk and the coverage gap in the implementation evidence. Use the cheapest layer that faithfully proves the behavior, reuse existing fixtures and browser hosts, and replace redundant assertions or matrix cases rather than accumulating them. A new connected case or browser host needs a concrete explanation of the boundary it proves and its measured execution cost. Retain real browser or backend connections when cheaper tests cannot prove them. Do not add tests that merely mirror implementation, or tests for reversible, low-impact changes without a meaningful failure risk. Test counts and line counts are evidence for review, not quotas.
