---
name: panels-worker-exploration
description: Stage-by-stage guidance for an exploration Worker type Panels Ticket.
---

# Exploration ticket stages

An exploration is a worker for exploring something undefined and making it clearer. It investigates the underlying mechanism rather than rushing toward the current implementation or deciding on the user's behalf.

Adjust the depth of Understanding, Research, and Answer to the ticket. Keep discussion top-down, concise, and proportionate; do not overwhelm the user.

### The stages

The sequence is **Kickoff → Understanding → Research Plan → Research → Answer → Follow-up → Closeout → Done**.

- **needs_kickoff** — preserve the premise and intake context without inventing the solution.
- **needs_understanding** — establish the shared frame and purpose through collaborative discussion.
- **needs_research_plan** — decide what evidence will answer the real questions and when research is sufficient.
- **needs_research** — discover, vet, and synthesize the evidence.
- **needs_answer** — reach and record the answer through collaborative discussion.
- **needs_follow_up** — propose only consequences supported by the approved answer.
- **needs_closeout** — perform and verify exactly the approved follow-up.
- **done** — finished.

### needs_kickoff — preserve the premise

A good **kickoff** keeps the user's wording, source context, boundaries, and uncertainty intact. State what prompted the exploration and why it matters, but do not silently narrow an ambiguous question, assume a preferred route, or manufacture a solution. The premise is ready when a later discussion can begin from it without mistaking an agent guess for user intent.

### needs_understanding — establish the shared frame

This is user-owned collaborative work. Use a small, purposeful set of questions to establish:

- the root problem and the user's actual intent;
- relevant constraints and what is already known;
- the important known unknowns; and
- enough shared frame to plan research without pretending all ambiguity is resolved.

Work top-down and one question at a time. Let each answer shape the next question rather than asking everything at once. If a question needs research to answer well, leave it for Answer after Research.

Ask only questions whose answers materially change the exploration. Reflect corrections and distinguish user facts from tentative interpretations. A good **understanding** is a concise statement of the agreed frame, including unresolved uncertainty that research must address; it is not an early answer.

### needs_research_plan — choose the evidence path

A good **research plan** defines:

- prioritized questions and the comparison axes needed to answer them;
- methods, source strategy, and relevant internal evidence;
- useful analogues selected for mechanism relevance, not superficial similarity; and
- stop or coverage conditions that say when the evidence is sufficient.

Abstract from the current implementation to the underlying mechanism so the research can reveal better routes rather than merely validate what exists. Keep the plan decision-level: concrete enough to execute and review, but not a speculative findings document. Research may be online, in the codebase, or both; do not force research into Understanding.

### needs_research — discover, vet, and synthesize

Execute the approved plan, adapting the search when evidence exposes a real gap. Research owns source discovery, corpus curation, provenance, and synthesis together; do not invent a separate corpus step.

Prefer first-hand evidence and mechanism-relevant comparables. Preserve where each important claim came from, what could not be confirmed, and meaningful source limitations. Separate **fact**, **inference**, and **hypothesis**. Synthesize rather than inventory: surface patterns, counterexamples, implications, contradictions, new unknowns, viable options, and useful decomposition of the problem.

Use sketches, prototypes, or tests only when the plan calls for them as evidence. They are investigative probes, not permission to implement downstream work. A good **research** field leaves the answer discussion with traceable evidence, explicit gaps, and the real tradeoffs exposed.

### needs_answer — reach the durable answer

This is user-owned collaborative work. Use the research to facilitate the user's judgment: explain tradeoffs, test interpretations, and keep unresolved choices visible. Do not choose for the user or turn the first recommendation into a foregone conclusion. Continue the discussion until the shared answer is ready to preserve formally.

A good **answer** states:

- the final understanding of the problem;
- what the user wants and the decisions reached;
- the rationale and evidence behind those decisions;
- routes considered but parked;
- remaining uncertainty; and
- the breakdown of the answer into meaningful parts.

The formal answer must be durable enough to guide later work without reconstructing the chat.

### needs_follow_up — propose consequences

Derive follow-up only from the approved answer. It may propose tickets, durable files, record updates, another exploration, or no action. Give each proposed consequence enough destination and scope to approve, and explain how it follows from the answer. Do not create records, modify destinations, publish artifacts, or take any other side effect before approval.

A good **follow-up** is the smallest complete, answer-supported package of consequences; it does not turn every interesting observation into work.

### needs_closeout — apply the approved follow-up

Perform exactly the approved follow-up and nothing broader. Verify created IDs, destinations, placements, and links; preserve the exploration's durable artifacts and provenance where they belong. Report what was actually applied and how it was checked.

When the approved follow-up creates a Ticket, load and follow
`panels-ticket-creation`. The approved answer and follow-up remain the authority for
what may be created, its provenance, and its destination.

Closeout is application and bookkeeping, not a hidden implementation stage. Do not perform substantive downstream work inside this ticket; create or route that work through the approved destination instead.

### Exploration disciplines

- Keep the stages distinct: Understanding frames the problem, Research Plan chooses the evidence path, Research produces the evidence, and Answer records the user's decision.
- Preserve uncertainty when evidence does not resolve it; confidence must not outrun provenance.
- Do not let a familiar implementation, a large source list, or a polished prototype substitute for mechanism-level understanding.
- Follow `panels-worker` for shared ownership, proposal, approval, Chat, scope, and reconciliation mechanics; this skill only defines exploration-specific work.

## How to think

### Extract the transferable problem

Do not begin research with only the product name, current implementation, or first proposed solution. Move beneath them and describe:

- who is trying to achieve what;
- the mechanism or interaction that makes it difficult;
- the important constraints, tensions, and failure modes; and
- what must become understood before choosing a direction.

Turn that description into research language. Search the direct product category, but also ask which other domains face the same mechanism under different names. For every cross-domain precedent, identify **what transfers**, **why it is relevant**, and **where the analogy stops**.

For example, “Vylo onboarding” is only the surface topic. The transferable problem includes delivering value from automatic setup that takes time, setting expectations during delayed processing, deciding what must be requested upfront, and keeping the waiting period useful. Relevant precedents may therefore exist in data imports, cloud synchronization, photo indexing, security scans, financial aggregation, and other asynchronous systems—not only competing AI products.

This is how exploration escapes the first local framing. Research that stays inside the product's existing vocabulary will systematically miss solutions developed elsewhere. A Research Plan is not ready until it includes a concise statement of the transferable problem, several cross-domain search angles, and a way to test whether each analogy genuinely applies.
