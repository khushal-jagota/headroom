---
name: "panels-worker-new-worker"
description: "Stage-by-stage guidance for a new-worker ticket \u2014 designing and landing another worker."
---

# Designing a new worker

This Ticket's deliverable is **another worker**: a new Worker type and the specialist skill that guides it. A worker is a short lifecycle — ordered Stages, each with one gated field and one ownership mode — plus the skill that teaches an agent to do each Stage well.

The hard part isn't writing files; it's the **thinking** — what the worker is for, what shape its lifecycle takes, what "good" means at each stage, and who does the work. Understanding is the collaborative beat after universal Kickoff: use the same Ticket Chat conversation to learn enough with the human before you design the lifecycle. Each later stage is a thinking beat the human reviews before you move on.\
\
Importantly, remember agents are really smart. You do not need to spell everything out. less is more when drafting.

### The stages

**Understanding → Stages → Thinking → Runtime Defaults → Drafting → Closeout → Done**

- **needs\_understanding** — the short collaborative conversation that captures what this worker is for, where the hard judgment lives, and the boundaries the lifecycle must respect.
- **needs\_stages** — the new worker's lifecycle: its ordered stages, what each one needs, and who owns each Stage.
- **needs\_thinking** — the design substance: what a good result looks like, who does the work, the standard each stage holds.
- **needs\_runtime\_defaults** — the collaborative choice of explicit backend, model, and reasoning effort used when this worker starts.
- **needs\_drafting** — the artifacts: the new worker's `SKILL.md` and its Worker type definition written as ticket artifacts for review\..
- **needs\_closeout** — landing it: files placed, registered, provisioned; a restart makes it live.

(`needs_kickoff` and `done` are the universal bookends every worker shares.)

### needs\_understanding — understand the worker before designing it

This is collaborative work. Do not recreate or restate panels-worker shared rules.

Begin with a small purposeful core set. Cover:

- **purpose/outcome** — what the worker is for, who uses it, and what a successful run leaves behind.
- **hard, ambiguous, or risky work and human judgment** — where the worker needs judgment, accountability, external access, or a reviewer decision.
- **constraints, examples, and boundaries** — concrete examples, non-goals, source or tool limits, wording/style constraints, and what must never happen.

Follow up only when an answer exposes a material gap. Do not ask for polish, preference, or extra detail that will not change the lifecycle. Stop when those categories are sufficiently understood to design the lifecycle and its ownership decisions.

Propose a concise durable Understanding result. It should carry forward only the facts the later Stages need: the worker's purpose/outcome, the hard judgment points, the constraints/examples/boundaries, and any open risk that should shape the lifecycle.

### needs\_stages — shape the lifecycle

Decide the new worker's stages before touching any files. The work in understanding should make this simple. A good **stages** proposal gives:

- A ordered `needs_<x>` stages, each gating a same-named `<x>` field, each with one line on what it needs.
- An **ownership mode for every non-terminal Stage** — `worker` or `user`. User ownership includes the worker as a collaborator. Terminal `done` has no ownership mode.
- The **alternatives you weighed**.
- What you **included or excluded, and why** — a stage earns its place only as a real, separately-reviewable beat; if two always get approved together, they're one.

Match the stages to how the work actually breaks. Most work also splits **doing the core thing** from **making it live / its downstream effects** — this very ticket does: draft the artifacts, then register and provision them. That seam is usually a natural stage boundary; look for it in the worker you're designing.

### needs\_thinking — the design substance (the crux)

This is where the worker's value is decided; a thin pass here makes a worthless worker. Cover what is non-obvious, what the user determines makes good work here, preferences, things that make execution better or more aligned with what the user desires.

### needs\_runtime\_defaults — choose how the worker starts

Recommend one explicit runtime tuple from the approved design and runtime catalog:

- a registered backend;
- a model advertised by that backend; and
- a reasoning effort supported by that backend/model, using explicit `None` only when reasoning effort is unsupported.

### needs\_drafting — write the artifacts

From the approved thinking, write the two files as artifacts:

- The new worker's **`SKILL.md`** — front matter plus one guidance section per stage, mirroring this skill and `panels-worker-coding`.
- Its **Worker type definition** — the `WorkerTypeDefinition`: Stages with an ownership mode on every non-terminal Stage, ordered fields, ceiling range, and worker profile. Novel ids are plain strings; reuse the shared `needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`, and `done`.

Copy the approved Runtime Defaults values into the record's `profile`.

### needs\_closeout — land it

A worker is declared in the database, so landing one is a write, not a deployment.

1. `panels worker-type save`, with the whole record on stdin — `worker_type`, `label`, `stages`, `fields`, `profile`, and a `skill` block holding `description` and `markdown_body`. Take the shape from `panels worker-type show coding` and change what differs. The `skill` block is how a new worker's skill comes into being: a Worker type may only name a skill that exists, and these arrive together or not at all.
2. Read it back with `panels worker-type show <type>`.
3. Confirm `panels worker-type list` names it, and that ordinary Ticket creation offers it. The base Worker discovers its specialist from `panels worker my-ticket`; there is no second list to update.
4. Add `src/planner/skills/<name>/SKILL.md` to the repository, so a fresh install is seeded with this worker's skill. That is an ordinary repository change through the branch and staging route, and it changes nothing on this Panels.

The `profile` carries the backend, model and reasoning effort approved in Runtime Defaults. There is no second copy of them to keep in step.

A good **closeout** is a short, verified report: what was saved, what `panels worker-type show` reports, and how you confirmed the worker is live.

### Working disciplines

- **Ground before you opine** — read this skill, `panels-worker-coding`, and a shipped definition before shaping a new one.
- **Everything earns its place** — a Stage, field, or ownership decision exists only if you can say why. A padded lifecycle is worse than a tight one.
- **Name things for what they are** — the new worker's names are read by people who didn't design it.
- **Keep proposals decision-level** — each field is something a human approves, not a spec to excavate.
- **Explain your judgment in chat** after each proposal — the direction, the alternatives ruled out, the calls made.
