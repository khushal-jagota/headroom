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

(`needs_kickoff`, `done`, and `dropped` are the universal bookends every worker shares.)

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
- An **ownership mode for every non-terminal Stage** — `worker` or `user`. User ownership includes the worker as a collaborator. Terminal `done` and `dropped` have no ownership mode.
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
- Its **Worker type definition** — the `WorkerTypeDefinition`: Stages with an ownership mode on every non-terminal Stage, ordered fields, ceiling range, and worker profile. Novel ids are plain strings; reuse the shared `needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`, `done`, `dropped`.

Copy the approved Runtime Defaults values into the definition's `WorkerProfile`.

### needs\_closeout — land it

The mechanical recipe for adding a worker to the running system:

First locate the Panels Git checkout in the assigned workspace and make every source
change there, normally in an isolated worktree. Never land a worker by editing
`~/Deployments/Panels/current/app` or another deployed app artifact.

1. Place the `SKILL.md` under `src/planner/skills/<name>/`.
2. Add the `WorkerTypeDefinition` module under `src/planner/worker_types/`.
3. Register it once in `src/planner/worker_types/configuration.py`: add its skill to the known-skills catalog and its definition to the production configuration tuple.
4. Provision it: add the skill dir to the planner skill list. On restart this **symlinks the skill into the worker's Hermes home — the step that lets a worker `skill_view` it**. The file must exist before the restart, or startup fails.
5. Confirm it validates at build and the skill is shipped + provisioned.
6. Confirm that ordinary Ticket creation lists the Worker type. The base Worker discovers its specialist from `panels worker my-ticket`; there is no second list to update.

Before activation, confirm the managed settings bootstrap contains the same backend, model, and reasoning effort approved in Runtime Defaults.

A good **closeout** is a short, verified report: what was placed and registered, and how you confirmed the worker is live.

### Working disciplines

- **Ground before you opine** — read this skill, `panels-worker-coding`, and a shipped definition before shaping a new one.
- **Everything earns its place** — a Stage, field, or ownership decision exists only if you can say why. A padded lifecycle is worse than a tight one.
- **Name things for what they are** — the new worker's names are read by people who didn't design it.
- **Keep proposals decision-level** — each field is something a human approves, not a spec to excavate.
- **Explain your judgment in chat** after each proposal — the direction, the alternatives ruled out, the calls made.
