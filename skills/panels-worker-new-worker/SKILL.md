---
name: panels-worker-new-worker
description: Stage-by-stage guidance for a new-worker ticket — designing and landing another worker.
---

# Designing a new worker

This Ticket's deliverable is **another worker**: a new Worker type and the specialist
skill that guides it. A worker is a short lifecycle — ordered Stages, each with one
gated field and one default ownership mode — plus the skill that teaches an agent to
do each Stage well.

The hard part isn't writing files; it's the **thinking** — what the worker is for, what shape its lifecycle takes, what "good" means at each stage, and who does the work. Understanding is the paired beat after universal Kickoff: use the same Ticket Chat conversation to learn enough with the human before you design the lifecycle. Each later stage is a thinking beat the human reviews before you move on.

### The stages

**Understanding → Stages → Thinking → Drafting → Closeout → Done**

- **needs_understanding** — the short paired conversation that captures what this worker is for, where the hard judgment lives, and the boundaries the lifecycle must respect.
- **needs_stages** — the new worker's lifecycle: its ordered stages, what each one needs, and who owns each Stage by default.
- **needs_thinking** — the design substance: what a good result looks like, who does the work, the standard each stage holds.
- **needs_drafting** — the artifacts: the new worker's `SKILL.md` and its Worker type definition.
- **needs_closeout** — landing it: files placed, registered, provisioned; a restart makes it live.

(`needs_kickoff`, `done`, and `dropped` are the universal bookends every worker shares.)

### needs_understanding — understand the worker before designing it

This is paired work. Stay in the ordinary Ticket Chat conversation with the same Employee.
Do not recreate or restate chat, session, approval, Take over, Release, or dispatch
machinery here; panels-worker owns those shared rules.

Begin with a small purposeful core set. Cover:

- **purpose/outcome** — what the worker is for, who uses it, and what a successful run leaves behind.
- **hard, ambiguous, or risky work and human judgment** — where the worker needs judgment, accountability, external access, or a reviewer decision.
- **constraints, examples, and boundaries** — concrete examples, non-goals, source or tool limits, wording/style constraints, and what must never happen.

Follow up only when an answer exposes a material gap. Do not ask for polish, preference, or
extra detail that will not change the lifecycle. Stop when those categories are sufficiently understood to design the lifecycle and its ownership defaults.

Propose a concise durable Understanding result. It should carry forward only the facts
the later Stages need: the worker's purpose/outcome, the hard judgment points, the
constraints/examples/boundaries, and any open risk that should shape the lifecycle.

### needs_stages — shape the lifecycle

Decide the new worker's stages before touching any files. A good **stages** proposal gives:

- A **best-guess lifecycle** — ordered `needs_<x>` stages, each gating a same-named `<x>` field, each with one line on what it needs.
- A **default ownership mode for every non-terminal Stage** — `worker`, `user`, or
  `paired`. Terminal `done` and `dropped` have no ownership mode.
- The **alternatives you weighed**, and why you chose this shape — longer or shorter, a stage split or merged.
- What you **included or excluded, and why** — a stage earns its place only as a real, separately-reviewable beat; if two always get approved together, they're one.

Match the stages to how the work actually breaks. Most work also splits **doing the core thing** from **making it live / its downstream effects** — this very ticket does: draft the artifacts, then register and provision them. That seam is usually a natural stage boundary; look for it in the worker you're designing.

### needs_thinking — the design substance (the crux)

This is where the worker's value is decided; a thin pass here makes a worthless worker. Work out three things, each grounded in the specific work — not the abstract:

1. **The "good result" bar.** What a strong deliverable at each stage actually contains, and how a reviewer tells strong from weak. Be concrete — *"cites primary sources and states what it couldn't confirm,"* not *"high quality."* If you can't state the bar, the skill can't hold anyone to it.
2. **The default ownership of each non-terminal Stage, and why.** Choose `worker` when
   the Employee can complete the Stage independently with available tools. Choose `user`
   when the Stage normally requires the user's access, judgment, external action, or
   accountable manual ownership; completed work will be recorded through Chief
   external-work reconciliation. Choose `paired` when the work should advance through
   user-originated Ticket Chat with the same Employee and every resulting proposal needs
   approval. Ownership is not scope. Do not recreate Take over,
   Release, dispatch, Chat, approval, or reconciliation mechanics in the specialist;
   `panels-worker` owns those shared rules.
3. **The standard each stage's skill enforces** — the specific discipline the new skill teaches, per stage. This is the raw material drafting turns into the skill.

Genericity test: any sentence equally true of a different worker isn't done — replace it with the specific truth about this one.

### needs_drafting — write the artifacts

From the approved thinking, write the two files:

- The new worker's **`SKILL.md`** — front matter plus one guidance section per stage, mirroring this skill and `panels-worker-coding`.
- Its **Worker type definition** — the `WorkerTypeDefinition`: Stages with a default
  ownership mode on every non-terminal Stage, ordered fields, ceiling range, worker
  profile, and reconciliation support. Novel ids are plain strings; reuse the
  shared `needs_kickoff`/`kickoff`, `needs_closeout`/`closeout`, `done`, `dropped`.

Point the reviewer at both files and note briefly how they realize the thinking.

### needs_closeout — land it

The mechanical recipe for adding a worker to the running system:

1. Place the `SKILL.md` under `skills/<name>/`.
2. Add the `WorkerTypeDefinition` module under `src/planner/worker_types/`.
3. Register it once in `src/planner/worker_types/configuration.py`: add its skill to the
   known-skills catalog and its definition to the production configuration tuple.
4. Provision it: add the skill dir to the planner skill list. On restart this **symlinks the skill into the worker's Hermes home — the step that lets a worker `skill_view` it**. The file must exist before the restart, or startup fails.
5. Confirm it validates at build and the skill is shipped + provisioned.
6. Announce the Worker type to the agent front doors: add the new specialist to
   `panels-worker`'s worker list, and add the Worker type (with what it's for) to
   `panels-chief-of-staff` so it can create and reconcile it.

A restart activates the Worker type. Finish the merge, verification, durable recap, and
every other pre-restart write before requesting it.
Run `panels restart` only after every pre-restart write is durable, and only when that
command is available in `panels --help`.
Treat it as the final pre-restart operation; after restart recovery, confirm the Worker
type is live and then propose Closeout.

Never discover or signal a server PID, and never launch `panels serve` from this Ticket's
worktree. If `panels restart` is unavailable, report that activation needs an operator
restart and stop without attempting live verification. A good **closeout** is a short,
verified report: what was placed and registered, and how you confirmed the worker is live.

### Working disciplines

- **Ground before you opine** — read this skill, `panels-worker-coding`, and a shipped definition before shaping a new one.
- **Everything earns its place** — a Stage, field, or ownership default exists only if
  you can say why. A padded lifecycle is worse than a tight one.
- **Name things for what they are** — the new worker's names are read by people who didn't design it.
- **Keep proposals decision-level** — each field is something a human approves, not a spec to excavate.
- **Explain your judgment in chat** after each proposal — the direction, the alternatives ruled out, the calls made.
