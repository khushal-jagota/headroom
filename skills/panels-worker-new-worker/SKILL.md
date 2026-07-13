---
name: panels-worker-new-worker
description: Stage-by-stage guidance for a new-worker ticket — designing and landing another worker.
---

# Designing a new worker

This ticket's deliverable is **another worker**: a new ticket type and the specialist skill that guides it. A worker is a short lifecycle — ordered stages, each needing one thing a human approves — plus the skill that teaches an agent to do each stage well.

The hard part isn't writing files; it's the **thinking** — what the worker is for, what shape its lifecycle takes, what "good" means at each stage, and who does the work. Each stage below is a thinking beat the human reviews before you move on.

### The stages

**Stages → Thinking → Drafting → Closeout → Done**

- **needs_stages** — the new worker's lifecycle: its ordered stages and what each one needs.
- **needs_thinking** — the design substance: what a good result looks like, who does the work, the standard each stage holds.
- **needs_drafting** — the artifacts: the new worker's `SKILL.md` and its type definition.
- **needs_closeout** — landing it: files placed, registered, provisioned; a restart makes it live.

(`needs_kickoff`, `done`, and `dropped` are the universal bookends every worker shares.)

### needs_stages — shape the lifecycle

Decide the new worker's stages before touching any files. A good **stages** proposal gives:

- A **best-guess lifecycle** — ordered `needs_<x>` stages, each gating a same-named `<x>` field, each with one line on what it needs.
- The **alternatives you weighed**, and why you chose this shape — longer or shorter, a stage split or merged.
- What you **included or excluded, and why** — a stage earns its place only as a real, separately-reviewable beat; if two always get approved together, they're one.

Match the stages to how the work actually breaks. Most work also splits **doing the core thing** from **making it live / its downstream effects** — this very ticket does: draft the artifacts, then register and provision them. That seam is usually a natural stage boundary; look for it in the worker you're designing.

### needs_thinking — the design substance (the crux)

This is where the worker's value is decided; a thin pass here makes a worthless worker. Work out three things, each grounded in the specific work — not the abstract:

1. **The "good result" bar.** What a strong deliverable at each stage actually contains, and how a reviewer tells strong from weak. Be concrete — *"cites primary sources and states what it couldn't confirm,"* not *"high quality."* If you can't state the bar, the skill can't hold anyone to it.
2. **Who does each stage, and why** — human or agent. Judgment, access, or accountable ownership → human; bounded, tool-completable work → agent. Where a stage hands accepted work to a human to execute, that becomes a transition hook (a durable takeover, as coding does at plan → implementation).
3. **The standard each stage's skill enforces** — the specific discipline the new skill teaches, per stage. This is the raw material drafting turns into the skill.

Genericity test: any sentence equally true of a different worker isn't done — replace it with the specific truth about this one.

### needs_drafting — write the artifacts

From the approved thinking, write the two files:

- The new worker's **`SKILL.md`** — front matter plus one guidance section per stage, mirroring this skill and `panels-worker-coding`.
- Its **type definition** — the `WorkflowDefinition`: stages, ordered fields, ceiling range, worker profile, any handoff. Novel ids are plain strings; reuse the shared `needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`, `done`, `dropped`.

Point the reviewer at both files and note briefly how they realize the thinking.

### needs_closeout — land it

The mechanical recipe for adding a worker to the running system:

1. Place the `SKILL.md` under `skills/<name>/`.
2. Add the definition module under `src/planner/ticket_types/`.
3. Register it: the definition into the production registry, its skill into the known-skills catalog.
4. Provision it: add the skill dir to the planner skill list. On restart this **symlinks the skill into the worker's Hermes home — the step that lets a worker `skill_view` it**. The file must exist before the restart, or startup fails.
5. Confirm it validates at build and the skill is shipped + provisioned.
6. Announce the type to the agent front doors: add the new specialist to `panels-worker`'s worker list, and add the type (with what it's for) to `panels-chief-of-staff` so it can create and reconcile it.

A restart activates the type. A good **closeout** is a short, verified report: what was placed and registered, and how you confirmed the worker is live.

### Working disciplines

- **Ground before you opine** — read this skill, `panels-worker-coding`, and a shipped definition before shaping a new one.
- **Everything earns its place** — a stage, field, or handoff exists only if you can say why. A padded lifecycle is worse than a tight one.
- **Name things for what they are** — the new worker's names are read by people who didn't design it.
- **Keep proposals decision-level** — each field is something a human approves, not a spec to excavate.
- **Explain your judgment in chat** after each proposal — the direction, the alternatives ruled out, the calls made.
