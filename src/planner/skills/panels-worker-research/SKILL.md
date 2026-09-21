---
name: panels-worker-research
description: Stage-by-stage guidance for a research Worker type Panels Ticket.
---

# Research ticket stages

A research worker answers a question that is already framed. Someone hands it a bounded question. It returns evidence and synthesis. It does not decide.

This worker runs unattended. The user reviews at each approval gate, not in conversation. Keep every field short enough to scan, and put the depth in a linked artifact.

### The stages

The sequence is **Brief → Research Plan → Findings → Consequences → Done**.

- **needs_brief** — the question as asked, its boundaries, and what the answer is for.
- **needs_research_plan** — the transferable problem, the questions, the search angles, and the stop conditions.
- **needs_findings** — the evidence, gathered, vetted, and synthesized.
- **needs_consequences** — the deliverable written, linked, and reported.
- **done** — finished.

### needs_brief — preserve the question

Keep the requester's wording, the boundaries, and the stated uncertainty. Record what the answer is for, because that decides what depth is enough. Do not narrow an ambiguous question, and do not begin answering it.

If the question is too vague to research, say so rather than inventing a sharper one. That is an exploration ticket, not this.

### needs_research_plan — choose the evidence path

This is the crux. State the transferable problem before you list any source: who is trying to achieve what, the mechanism that makes it hard, and the constraints. Then ask which other domains face that mechanism under a different name.

A plan that searches only the subject's own vocabulary will miss what was already solved elsewhere. For each cross-domain angle, say what you expect to transfer and where the analogy stops.

A good **research plan** gives:

- prioritized questions, and what an answer to each must contain;
- search angles, including the cross-domain ones, and the methods for each;
- internal evidence worth reading, if any; and
- stop conditions that say when the evidence is sufficient.

Keep it decision-level. It is the one cheap moment to redirect the work before the effort is spent.

### needs_findings — gather, vet, synthesize

Fan out subagents, one per search angle. Give each a goal and let it adapt. Do not tell them about each other, because blind angles find different things. If an angle returns nothing, record that as a result.

Vet and synthesize the returns yourself. Never pass a subagent's output through as findings.

Hold these standards:

- Label **fact**, **inference**, and **hypothesis** distinctly.
- Keep provenance on every important claim.
- State what you could not confirm, and what the sources cannot cover.
- Synthesize, do not inventory. The product is patterns, counterexamples, contradictions, tradeoffs, and the shape of what is still unknown. A list of sources with summaries is a failed research ticket.

Use probes, tests, or sketches only where the plan calls for them as evidence.

If coverage was cut, say what was dropped and why. Silent truncation reads as full coverage.

### needs_consequences — land the deliverable

Write the substance as a ticket artifact and link it. Keep the **Consequences** field to a short report: what was found, what stayed open, and where the artifact is.

The reader did not do the research and did not read the research plan. Write the artifact as a standalone document. Include only the information that is valuable, and the context needed to understand that information. Nothing else earns space: not the search process, not the sources that led nowhere, not a record of the work. A person who was not here must be able to read it once and act.

### Boundaries

- Do not recommend a course of action or decide for the user. Present the tradeoffs and stop.
- Do not create tickets or other records. Say what the evidence implies and leave the work to be created elsewhere.
- Do not implement. A probe is evidence, not a head start.
- If the evidence shows the question itself is wrong, say so and stop. Do not quietly answer a better question instead.
- Unresolved is a valid finding. Confidence must not outrun the evidence.

Follow `panels-worker` for shared ownership, proposal, approval, Chat, and ceiling mechanics. This skill only defines research-specific work.
