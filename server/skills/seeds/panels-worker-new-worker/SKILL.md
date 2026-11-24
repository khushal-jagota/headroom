---
name: "panels-worker-new-worker"
description: "Stage-by-stage guidance for a new-worker ticket \u2014 designing and landing another worker."
---

# Designing a new worker

This Ticket's deliverable is **another worker**: a new Worker type and the specialist skill that guides it. A worker is a short lifecycle — ordered Stages, each with one gated field and one ownership mode — plus the skill that teaches an agent to do each Stage well.

The hard part isn't writing files; it's the **thinking** — what the worker is for, what shape its lifecycle takes, what "good" means at each stage, and who does the work. Purpose and Boundaries is the collaborative beat after the universal Brief: use the same Ticket Chat conversation to learn enough with the human before you design the lifecycle. Each later stage is a thinking beat the human reviews before you move on.\
\
Importantly, remember agents are really smart. You do not need to spell everything out. less is more when drafting.

### The stages

**Purpose and Boundaries → Stages → What Good Looks Like at Each Stage → Model and Effort → Drafting → Consequences → Done**

- **needs\_purpose\_and\_boundaries** — the short collaborative conversation that captures what this worker is for, where the hard judgment lives, and the boundaries the lifecycle must respect.
- **needs\_stages** — the new worker's lifecycle: its ordered stages, what each one needs, and who owns each Stage.
- **needs\_what\_good\_looks\_like\_at\_each\_stage** — the design substance: what a good result looks like, who does the work, the standard each stage holds.
- **needs\_model\_and\_effort** — the collaborative choice of explicit backend, model, and reasoning effort used when this worker starts.
- **needs\_drafting** — the artifacts: the structured preset and its generated specialist skill preview.
- **needs\_consequences** — finish the authorized repository work and record the verified result. The owner or Chief saves the preset during the final Drafting review.

(`needs_brief` and `done` are the universal bookends every worker shares.)

### needs\_purpose\_and\_boundaries — understand the worker before designing it

This is collaborative work. Do not recreate or restate panels-worker shared rules.

Begin with a small purposeful core set. Cover:

- **purpose/outcome** — what the worker is for, who uses it, and what a successful run leaves behind.
- **hard, ambiguous, or risky work and human judgment** — where the worker needs judgment, accountability, external access, or a reviewer decision.
- **constraints, examples, and boundaries** — concrete examples, non-goals, source or tool limits, wording/style constraints, and what must never happen.

Follow up only when an answer exposes a material gap. Do not ask for polish, preference, or extra detail that will not change the lifecycle. Stop when those categories are sufficiently understood to design the lifecycle and its ownership decisions.

Propose a concise durable result for Purpose and Boundaries. It should carry forward only the facts the later Stages need: the worker's purpose/outcome, the hard judgment points, the constraints/examples/boundaries, and any open risk that should shape the lifecycle.

### needs\_stages — shape the lifecycle

Decide the new worker's stages before touching any files. The work in Purpose and Boundaries should make this simple. A good **stages** proposal gives:

- A ordered `needs_<x>` stages, each gating a same-named `<x>` field, each with one line on what it needs.
- An **ownership mode for every non-terminal Stage** — `worker` or `user`. User ownership includes the worker as a collaborator. Terminal `done` has no ownership mode.
- The **alternatives you weighed**.
- What you **included or excluded, and why** — a stage earns its place only as a real, separately-reviewable beat; if two always get approved together, they're one.

Match the stages to how the work actually breaks. Put the final decision before worker-owned Consequences, then Done. Complete required owner-only actions during the final review. Consequences performs only the authorized follow-through and records its verified result.

### needs\_what\_good\_looks\_like\_at\_each\_stage — the design substance (the crux)

This is where the worker's value is decided; a thin pass here makes a worthless worker. Cover what is non-obvious, what the user determines makes good work here, preferences, things that make execution better or more aligned with what the user desires.

### needs\_model\_and\_effort — choose how the worker starts

Recommend one explicit runtime tuple from the approved design and runtime catalog:

- a registered backend;
- a model advertised by that backend; and
- a reasoning effort supported by that backend/model, using explicit `None` only when reasoning effort is unsupported.

### needs\_drafting — write the artifacts

From the approved What Good Looks Like at Each Stage, prepare the structured preset as a Ticket artifact.

The record holds `worker_type`, `label`, `guidance`, `stages`, `fields`, and `profile`. Each stage holds its instructions beside its name, ownership, and result field. Use `guidance` for rules across the workflow. Novel stage and field ids remain plain strings. Preserve the shared structural rules.

Copy the approved Model and Effort values into `profile`. Include a `skill` block with the specialist description. Panels generates the specialist body from the structured record. Do not provide an independently edited Markdown body. Include the generated preview for review.

During the final Drafting review, hand the exact record and generated skill preview to the owner or Chief. They review the content and save the approved preset with `panels worker-type save`. Ticket Workers cannot write presets or skills.

Read the record back with `panels worker-type show <type>`. Confirm that creation offers it and that its generated skill matches the reviewed preview. The owner or Chief accepts Drafting only after these checks succeed. No restart is necessary.

### needs\_consequences — finish the follow-through

Update the repository seed through the ordinary branch route for fresh installations. Existing Tickets keep their saved workflows when a preset changes.

Record what was saved, what the live store reports, and the completed repository work. Submit the verified report with `panels ticket propose`. If the ceiling is Done, the report settles automatically. Otherwise the ceiling holder reviews it. If the approved work cannot finish, request help and preserve the actual result.

### Working disciplines

- **Ground before you opine** — read this skill, `panels-worker-coding`, and a shipped definition before shaping a new one.
- **Everything earns its place** — a Stage, field, or ownership decision exists only if you can say why. A padded lifecycle is worse than a tight one.
- **Name things for what they are** — the new worker's names are read by people who didn't design it.
- **Keep proposals decision-level** — each field is something a human approves, not a spec to excavate.
