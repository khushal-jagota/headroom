"""Give every Stage the name that was settled for it.

A Stage carries an id and a label. The id is what the code keys on and what a worker
types; the label is the name a reader sees. These are the fifteen names the stage-name
audit settled, plus the two settled before it, and every one of them is a label. No id
moves: `closeout` is required of every Worker type by the registry, decides the
one-at-a-time Closeout lane, and keys every stored Ticket through `stage`, `ceiling` and
`field_values`. Once the screen reads the label, no id is visible anywhere, so moving one
buys nothing.

The migration that put Worker types in the database carries a frozen copy of what shipped
and is not rewritten, so every database seeds the old labels and then arrives here. A
label is rewritten only where it still reads exactly as shipped, the rule the `dropped`
removal used, so anything the owner edited survives.

Skill text is different: the seeding migration reads it live from the packaged tree, so a
fresh database picks the new wording up on its own. Only a database that already holds
skill rows needs the replacements below.

Revision ID: settled_stage_names
Revises: one_ticket_ending
"""

from __future__ import annotations

import json
from typing import Any, Final

from alembic import op

revision = "settled_stage_names"
down_revision = "one_ticket_ending"
branch_labels = None
depends_on = None

# Every rename: the field id it belongs to, the label as shipped, the settled label, and
# the Worker types it applies to. `None` means every type that declares the field. The
# Stage gated by the field carries the same label and moves with it.
RENAMES: Final[tuple[tuple[str, str, str, frozenset[str] | None], ...]] = (
    ("kickoff", "Kickoff", "Brief", None),
    ("closeout", "Closeout", "Consequences", None),
    ("approach", "Approach", "What Changes", frozenset({"coding"})),
    ("success", "Success", "Success Condition", frozenset({"coding"})),
    ("execution", "Execution", "Work Done", frozenset({"general"})),
    ("structural_diagnosis", "Structural Diagnosis", "Root Cause", frozenset({"debugging"})),
    ("solution", "Solution", "Proposed Fix", frozenset({"debugging"})),
    (
        "rough_shape",
        "Rough Shape",
        "Rough Split into Parts",
        frozenset({"initiative_planning"}),
    ),
    (
        "ticket_outlines",
        "Ticket Outlines",
        "Proposed Tickets",
        frozenset({"initiative_planning"}),
    ),
    ("understanding", "Understanding", "Purpose and Boundaries", frozenset({"new_worker"})),
    (
        "thinking",
        "Thinking",
        "What Good Looks Like at Each Stage",
        frozenset({"new_worker"}),
    ),
    ("runtime_defaults", "Runtime Defaults", "Model and Effort", frozenset({"new_worker"})),
    (
        "direction",
        "Direction",
        "Best Guess and Open Options",
        frozenset({"product_design"}),
    ),
    ("direction", "Direction", "Today's Direction", frozenset({"planning-day"})),
    ("action", "Action", "Agreed Intervention", frozenset({"planning-midday-check"})),
    ("research", "Research", "Findings", frozenset({"exploration", "research"})),
    ("follow_up", "Follow-up", "Proposed Follow-up", frozenset({"exploration"})),
)

# The stage lists and the prose that name a Stage to the reader. A skill row is the
# owner's to edit, so each block is replaced only where the row still reads exactly as
# shipped. A sentence the owner rewrote keeps their wording.
SKILL_REPLACEMENTS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "panels": (
        (
            "non-terminal Stage. For example, coding uses **Kickoff → Success → Approach → Plan →\nImplementation → Closeout → Done**, while `planning-day` uses **Kickoff → Gather →\nPlanning → Closeout → Done**, `planning-midday-check` uses **Kickoff → Action → Closeout\n→ Done**, and `planning-sprint` uses **Kickoff → Review → Next Sprint → Closeout → Done**.",
            "non-terminal Stage. For example, coding uses **Brief → Success Condition → What Changes →\nPlan → Implementation → Consequences → Done**, while `planning-day` uses **Brief → Gather\n→ Planning → Consequences → Done**, `planning-midday-check` uses **Brief → Agreed\nIntervention → Consequences → Done**, and `planning-sprint` uses **Brief → Review → Next\nSprint → Consequences → Done**.",
        ),
    ),
    "panels-chief-of-staff": (
        (
            "Preserve the user's report in Kickoff and recap text. If an existing Worker type no longer contains a live Ticket's Stage, use an explicit repository migration with that Worker change. Do not repair it through the product API.",
            "Preserve the user's report in Brief and recap text. If an existing Worker type no longer contains a live Ticket's Stage, use an explicit repository migration with that Worker change. Do not repair it through the product API.",
        ),
        (
            "For capture, create the smallest correct object. **A Kickoff is intake, not your plan,",
            "For capture, create the smallest correct object. **A Brief is intake, not your plan,",
        ),
        (
            "missing intent prevents correct capture, ask briefly; otherwise write a light Kickoff and\nlet the Ticket's conversation and notes add or correct context. Longer Kickoffs are earned",
            "missing intent prevents correct capture, ask briefly; otherwise write a light Brief and\nlet the Ticket's conversation and notes add or correct context. Longer Briefs are earned",
        ),
        (
            "before writing the Kickoff. Add the smallest factual explanation needed for a later",
            "before writing the Brief. Add the smallest factual explanation needed for a later",
        ),
        (
            "Every substantive Kickoff sentence must be one of:",
            "Every substantive Brief sentence must be one of:",
        ),
        (
            "work before closeout.",
            "work before Consequences.",
        ),
    ),
    "panels-sprint-item-supervisor": (
        (
            "  this Sprint Item becomes its ceiling holder. If creation includes a kickoff proposal,",
            "  this Sprint Item becomes its ceiling holder. If creation includes a Brief proposal,",
        ),
    ),
    "panels-ticket-creation": (
        (
            "Give the Ticket a clear outcome-oriented title and a light, faithful Kickoff. Intake is",
            "Give the Ticket a clear outcome-oriented title and a light, faithful Brief. Intake is",
        ),
        (
            "By default a new Ticket parks its Kickoff for the user's approval. That default is right",
            "By default a new Ticket parks its Brief for the user's approval. That default is right",
        ),
        (
            "user. Stating a ceiling past kickoff settles the\nKickoff and starts the Ticket at its next Stage, so work the user has already authorized",
            "user. Stating a ceiling past the Brief settles the\nBrief and starts the Ticket at its next Stage, so work the user has already authorized",
        ),
        (
            "After creation, read the Ticket back as a whole. Its title, Worker type, Kickoff,",
            "After creation, read the Ticket back as a whole. Its title, Worker type, Brief,",
        ),
    ),
    "panels-worker": (
        (
            "Ticket-owned artifacts are durable work products that make the work easier to understand; they are not a reason to bloat a gated field. Store them in the live managed tree at `/home/vps/Deployments/Panels/current/data/files/tickets/<ticket-id>/<relative-path>` and link them from the relevant proposal, note, implementation, or closeout as `/files/tickets/<ticket-id>/<relative-path>`. For example, write `/home/vps/Deployments/Panels/current/data/files/tickets/<ticket-id>/artifacts/ui-plan.html` and link it as `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`. Never use a source checkout's `data/...`, a ticket worktree's `data/...`, or another path inferred from the current directory.",
            "Ticket-owned artifacts are durable work products that make the work easier to understand; they are not a reason to bloat a gated field. Store them in the live managed tree at `/home/vps/Deployments/Panels/current/data/files/tickets/<ticket-id>/<relative-path>` and link them from the relevant proposal, note, Implementation, or Consequences as `/files/tickets/<ticket-id>/<relative-path>`. For example, write `/home/vps/Deployments/Panels/current/data/files/tickets/<ticket-id>/artifacts/ui-plan.html` and link it as `[UI plan](/files/tickets/<ticket-id>/artifacts/ui-plan.html)`. Never use a source checkout's `data/...`, a ticket worktree's `data/...`, or another path inferred from the current directory.",
        ),
    ),
    "panels-worker-amend-worker": (
        (
            "**Stages: Kickoff → Amendment → Drafting → Closeout → Done.**",
            "**Stages: Brief → Amendment → Drafting → Consequences → Done.**",
        ),
        (
            "A good closeout is short and verified: what changed, what `panels worker-type show` reports now, and what if anything is waiting on a repository change.",
            "A good Consequences is short and verified: what changed, what `panels worker-type show` reports now, and what if anything is waiting on a repository change.",
        ),
    ),
    "panels-worker-coding": (
        (
            "Each stage is named for what the ticket needs next; your step is to give it that. The visible sequence is **Success → Approach → Plan → Implementation → Closeout → Done**.",
            "Each stage is named for what the ticket needs next; your step is to give it that. The visible sequence is **Success Condition → What Changes → Plan → Implementation → Consequences → Done**.",
        ),
        (
            '- **needs\\_success** — needs its **success**: what "done" would mean.\n- **needs\\_approach** — needs its **approach**: how it will be done.\n- **needs\\_plan** — needs its **plan**: the concrete steps.\n- **needs\\_implementation** — needs its **implementation**. Implementation follows the plan, performs the work, and proposes a concise, reviewable package with concrete evidence.\n- **needs\\_closeout** — needs its **closeout**. It performs only the applicable merge, deploy, follow-up, and bookkeeping, then proposes a concise, verified report.',
            '- **needs\\_success** — needs its **Success Condition**: what "done" would mean.\n- **needs\\_approach** — needs **What Changes**: how it will be done.\n- **needs\\_plan** — needs its **Plan**: the concrete steps.\n- **needs\\_implementation** — needs its **Implementation**. Implementation follows the plan, performs the work, and proposes a concise, reviewable package with concrete evidence.\n- **needs\\_closeout** — needs its **Consequences**. It performs only the applicable merge, deploy, follow-up, and bookkeeping, then proposes a concise, verified report.',
        ),
        (
            '- **needs\\_success** — a good **success** says plainly what "done" means for this ticket, grounded in the real work. Keep it human-readable and outcome-level.',
            '- **needs\\_success** — a good **Success Condition** says plainly what "done" means for this ticket, grounded in the real work. Keep it human-readable and outcome-level.',
        ),
        (
            "- **needs\\_approach** — a good **approach** names what needs to change for success to be achieved. It does not need to get into how we will do those things, but needs to state what needs to be done. where you'll get data from to do x is useful here, the structure of the function isn't. Use system diagrams where they would be clearer than text.",
            "- **needs\\_approach** — a good **What Changes** names what needs to change for success to be achieved. It does not need to get into how we will do those things, but needs to state what needs to be done. where you'll get data from to do x is useful here, the structure of the function isn't. Use system diagrams where they would be clearer than text.",
        ),
        (
            "- **needs\\_plan** — a good **plan** goes through how we will execute an approach. It should be concise and sequenced, not an exhaustive engineering spec.",
            "- **needs\\_plan** — a good **Plan** goes through how we will carry out What Changes. It should be concise and sequenced, not an exhaustive engineering spec.",
        ),
        (
            "- **needs\\_closeout** — complete the repository-defined integration and cleanup route before proposing **closeout**. For a branch-based route, bring the current target base into the feature branch, resolve and verify the prospective result there, and advance the target only after its required checks pass. Fix any issues here and record evidence. A failed attempt leaves the target unchanged and keeps its exact continuation point visible. If integration cannot finish, keep the Ticket at `needs_closeout` and request user help. Deployment is a later user action, not a substitute for completed integration. A good **closeout** is a short, verified report of success and how it was checked.",
            "- **needs\\_closeout** — complete the repository-defined integration and cleanup route before proposing **Consequences**. For a branch-based route, bring the current target base into the feature branch, resolve and verify the prospective result there, and advance the target only after its required checks pass. Fix any issues here and record evidence. A failed attempt leaves the target unchanged and keeps its exact continuation point visible. If integration cannot finish, keep the Ticket at `needs_closeout` and request user help. Deployment is a later user action, not a substitute for completed integration. A good **Consequences** is a short, verified report of success and how it was checked.",
        ),
    ),
    "panels-worker-debugging": (
        (
            "The Kickoff should describe the reported bug, its context, and any evidence already",
            "The Brief should describe the reported bug, its context, and any evidence already",
        ),
        (
            "Work from the Kickoff to understand what problem the user means. Resolve unclear references",
            "Work from the Brief to understand what problem the user means. Resolve unclear references",
        ),
        (
            "Leave a short orientation to the diagnosis, solution, outstanding implementation work,\nand any residual uncertainty.",
            "Leave a short orientation to the Root Cause, the Proposed Fix, outstanding implementation\nwork, and any residual uncertainty.",
        ),
    ),
    "panels-worker-exploration": (
        (
            "Adjust the depth of Understanding, Research, and Answer to the ticket. Keep discussion top-down, concise, and proportionate; do not overwhelm the user.",
            "Adjust the depth of Understanding, Findings, and Answer to the ticket. Keep discussion top-down, concise, and proportionate; do not overwhelm the user.",
        ),
        (
            "The sequence is **Kickoff → Understanding → Research Plan → Research → Answer → Follow-up → Closeout → Done**.",
            "The sequence is **Brief → Understanding → Research Plan → Findings → Answer → Proposed Follow-up → Consequences → Done**.",
        ),
        (
            "A good **kickoff** keeps the user's wording, source context, boundaries, and uncertainty intact. State what prompted the exploration and why it matters, but do not silently narrow an ambiguous question, assume a preferred route, or manufacture a solution. The premise is ready when a later discussion can begin from it without mistaking an agent guess for user intent.",
            "A good **Brief** keeps the user's wording, source context, boundaries, and uncertainty intact. State what prompted the exploration and why it matters, but do not silently narrow an ambiguous question, assume a preferred route, or manufacture a solution. The premise is ready when a later discussion can begin from it without mistaking an agent guess for user intent.",
        ),
        (
            "Work top-down and one question at a time. Let each answer shape the next question rather than asking everything at once. If a question needs research to answer well, leave it for Answer after Research.",
            "Work top-down and one question at a time. Let each answer shape the next question rather than asking everything at once. If a question needs research to answer well, leave it for Answer after Findings.",
        ),
        (
            "Execute the approved plan, adapting the search when evidence exposes a real gap. Research owns source discovery, corpus curation, provenance, and synthesis together; do not invent a separate corpus step.",
            "Execute the approved plan, adapting the search when evidence exposes a real gap. The Findings stage owns source discovery, corpus curation, provenance, and synthesis together; do not invent a separate corpus step.",
        ),
        (
            "Use sketches, prototypes, or tests only when the plan calls for them as evidence. They are investigative probes, not permission to implement downstream work. A good **research** field leaves the answer discussion with traceable evidence, explicit gaps, and the real tradeoffs exposed.",
            "Use sketches, prototypes, or tests only when the plan calls for them as evidence. They are investigative probes, not permission to implement downstream work. A good **Findings** field leaves the answer discussion with traceable evidence, explicit gaps, and the real tradeoffs exposed.",
        ),
        (
            "A good **follow-up** is the smallest complete, answer-supported package of consequences; it does not turn every interesting observation into work.",
            "A good **Proposed Follow-up** is the smallest complete, answer-supported package of consequences; it does not turn every interesting observation into work.",
        ),
        (
            "Closeout is application and bookkeeping, not a hidden implementation stage. Do not perform substantive downstream work inside this ticket; create or route that work through the approved destination instead.",
            "The Consequences stage is application and bookkeeping, not a hidden implementation stage. Do not perform substantive downstream work inside this ticket; create or route that work through the approved destination instead.",
        ),
        (
            "- Keep the stages distinct: Understanding frames the problem, Research Plan chooses the evidence path, Research produces the evidence, and Answer records the user's decision.",
            "- Keep the stages distinct: Understanding frames the problem, Research Plan chooses the evidence path, Findings produces the evidence, and Answer records the user's decision.",
        ),
    ),
    "panels-worker-general": (
        (
            "**Kickoff → Execution → Closeout → Done**",
            "**Brief → Work Done → Consequences → Done**",
        ),
        (
            "- **needs_closeout** — after the execution is reviewed, apply the consequences: merge the code, send the email, publish the file. Then confirm it is done with a short cold-user summary.",
            "- **needs_closeout** — after the Work Done is reviewed, apply its Consequences: merge the code, send the email, publish the file. Then confirm it is done with a short cold-user summary.",
        ),
    ),
    "panels-worker-initiative-planning": (
        (
            "The sequence is **Kickoff → Rough Shape → Question Tree → Question Answers → Ticket\nOutlines → Closeout → Done**.",
            "The sequence is **Brief → Rough Split into Parts → Question Tree → Question Answers →\nProposed Tickets → Consequences → Done**.",
        ),
        (
            "A good **kickoff** lets the Rough Shape begin without reconstructing the intake or\nmistaking an agent assumption for settled direction. It does not contain a guessed\nsolution or premature Ticket list.",
            "A good **Brief** lets the Rough Split into Parts begin without reconstructing the\nintake or mistaking an agent assumption for settled direction. It does not contain a\nguessed solution or premature Ticket list.",
        ),
        (
            "A good **rough shape**:",
            "A good **Rough Split into Parts**:",
        ),
        (
            "Build a prioritized, nested tree under the Rough Shape's parts. Start with the questions\nwhose answers constrain later branches. Show subordinate questions only where an answer\ncreates a real follow-on choice. Distinguish questions that block coherent Ticket\noutlines from questions that can safely be carried into downstream work.",
            "Build a prioritized, nested tree under the parts from Rough Split into Parts. Start with\nthe questions whose answers constrain later branches. Show subordinate questions only\nwhere an answer creates a real follow-on choice. Distinguish questions that block\ncoherent Ticket outlines from questions that can safely be carried into downstream work.",
        ),
        (
            "settled answers by the Rough Shape, preserve the implications that downstream Tickets\nneed, and list only unresolved questions that can safely be deferred with a clear owner.",
            "settled answers by the parts from Rough Split into Parts, preserve the implications that\ndownstream Tickets need, and list only unresolved questions that can safely be deferred\nwith a clear owner.",
        ),
        (
            "Check the package against the Rough Shape. Every important part should be owned, no two\nTickets should silently own the same decision, and dependencies should not conceal an\nunanswered cross-cutting question. Do not copy the entire planning record into every\noutline, specify local implementation, or create records during this stage.",
            "Check the package against the Rough Split into Parts. Every important part should be\nowned, no two Tickets should silently own the same decision, and dependencies should not\nconceal an unanswered cross-cutting question. Do not copy the entire planning record\ninto every outline, specify local implementation, or create records during this stage.",
        ),
        (
            "the outlines as final. A good **ticket outlines** proposal is concise enough to scan and",
            "the outlines as final. A good **Proposed Tickets** field is concise enough to scan and",
        ),
        (
            "A good **closeout** lists the created Ticket ids and destinations and states what was",
            "A good **Consequences** lists the created Ticket ids and destinations and states what was",
        ),
        (
            "- Keep the sequence honest: Rough Shape identifies parts; Question Tree exposes shared\n  decisions; Question Answers settles them; Ticket Outlines derives the work; Closeout\n  creates it.",
            "- Keep the sequence honest: Rough Split into Parts identifies the parts; Question Tree\n  exposes shared decisions; Question Answers settles them; Proposed Tickets derives the\n  work; Consequences creates it.",
        ),
    ),
    "panels-worker-initiative-review": (
        (
            "The sequence is **Kickoff → Review → Feedback → Follow-ups → Closeout → Done**.",
            "The sequence is **Brief → Review → Feedback → Follow-ups → Consequences → Done**.",
        ),
        (
            "A good **kickoff** lets another worker identify the exact review surface without reconstructing the initiative.",
            "A good **Brief** lets another worker identify the exact review surface without reconstructing the initiative.",
        ),
        (
            "A good **closeout** states the reviewed result, the target state, and the verification. If integration did not occur, it states the blocker, handoff, and next review point.",
            "A good **Consequences** states the reviewed result, the target state, and the verification. If integration did not occur, it states the blocker, handoff, and next review point.",
        ),
    ),
    "panels-worker-new-worker": (
        (
            "The hard part isn't writing files; it's the **thinking** — what the worker is for, what shape its lifecycle takes, what \"good\" means at each stage, and who does the work. Understanding is the collaborative beat after universal Kickoff: use the same Ticket Chat conversation to learn enough with the human before you design the lifecycle. Each later stage is a thinking beat the human reviews before you move on.\\",
            "The hard part isn't writing files; it's the **thinking** — what the worker is for, what shape its lifecycle takes, what \"good\" means at each stage, and who does the work. Purpose and Boundaries is the collaborative beat after the universal Brief: use the same Ticket Chat conversation to learn enough with the human before you design the lifecycle. Each later stage is a thinking beat the human reviews before you move on.\\",
        ),
        (
            "**Understanding → Stages → Thinking → Runtime Defaults → Drafting → Closeout → Done**",
            "**Purpose and Boundaries → Stages → What Good Looks Like at Each Stage → Model and Effort → Drafting → Consequences → Done**",
        ),
        (
            "Propose a concise durable Understanding result. It should carry forward only the facts the later Stages need: the worker's purpose/outcome, the hard judgment points, the constraints/examples/boundaries, and any open risk that should shape the lifecycle.",
            "Propose a concise durable result for Purpose and Boundaries. It should carry forward only the facts the later Stages need: the worker's purpose/outcome, the hard judgment points, the constraints/examples/boundaries, and any open risk that should shape the lifecycle.",
        ),
        (
            "Decide the new worker's stages before touching any files. The work in understanding should make this simple. A good **stages** proposal gives:",
            "Decide the new worker's stages before touching any files. The work in Purpose and Boundaries should make this simple. A good **stages** proposal gives:",
        ),
        (
            "From the approved thinking, write the two files as artifacts:",
            "From the approved What Good Looks Like at Each Stage, write the two files as artifacts:",
        ),
        (
            "Copy the approved Runtime Defaults values into the record's `profile`.",
            "Copy the approved Model and Effort values into the record's `profile`.",
        ),
        (
            "The `profile` carries the backend, model and reasoning effort approved in Runtime Defaults. There is no second copy of them to keep in step.",
            "The `profile` carries the backend, model and reasoning effort approved in Model and Effort. There is no second copy of them to keep in step.",
        ),
        (
            "A good **closeout** is a short, verified report: what was saved, what `panels worker-type show` reports, and how you confirmed the worker is live.",
            "A good **Consequences** is a short, verified report: what was saved, what `panels worker-type show` reports, and how you confirmed the worker is live.",
        ),
    ),
    "panels-worker-personal-task": (
        (
            "## Kickoff",
            "## Brief",
        ),
        (
            "Let the user provide only the context they need. Do not turn kickoff into planning or infer work that the user has not stated.",
            "Let the user provide only the context they need. Do not turn the Brief into planning or infer work that the user has not stated.",
        ),
        (
            "## Closeout",
            "## Consequences",
        ),
        (
            "Reach or work this stage only after the user explicitly engages the agent. Follow the user's instructions, record what actually resulted from the outcome, and keep the closeout concise. Do not treat Ticket creation, opening, advancement, or silence as permission to start.",
            "Reach or work this stage only after the user explicitly engages the agent. Follow the user's instructions, record what actually resulted from the outcome, and keep the Consequences concise. Do not treat Ticket creation, opening, advancement, or silence as permission to start.",
        ),
    ),
    "panels-worker-planning-day": (
        (
            "description: Review the previous day, agree today's direction, show exact Day changes, and commit the approved Day.",
            "description: Review the previous day, agree Today's Direction, show exact Day changes, and commit the approved Day.",
        ),
    ),
    "panels-worker-planning-midday-check": (
        (
            "do not pre-decide the action.",
            "do not pre-decide the Agreed Intervention.",
        ),
        (
            "Done means approved action and the reconciliation match reality. A missed or dropped\ncheck creates no invented record or catch-up ceremony.",
            "Done means the approved Agreed Intervention and the reconciliation match reality. A\nmissed or dropped check creates no invented record or catch-up ceremony.",
        ),
    ),
    "panels-worker-planning-sprint": (
        (
            "for the user's strategic judgment, and writes only the approved result during Closeout.",
            "for the user's strategic judgment, and writes only the approved result during Consequences.",
        ),
        (
            "The sequence is **Kickoff → Review → Next Sprint → Closeout → Done**.",
            "The sequence is **Brief → Review → Next Sprint → Consequences → Done**.",
        ),
        (
            "Closeout is the only canonical-write phase. Refresh affected records and compare them\nwith the approved Review and Next Sprint packages. If material drift makes either unsafe,\nrequest user help rather than improvising.",
            "The Consequences stage is the only canonical-write phase. Refresh affected records and\ncompare them with the approved Review and Next Sprint packages. If material drift makes\neither unsafe, request user help rather than improvising.",
        ),
        (
            "A good **closeout** briefly names what landed and the readback evidence that it matches",
            "A good **Consequences** briefly names what landed and the readback evidence that it matches",
        ),
    ),
    "panels-worker-product-design": (
        (
            "Keep this outcome-level; do not hide an assumed solution inside the kickoff.",
            "Keep this outcome-level; do not hide an assumed solution inside the Brief.",
        ),
        (
            "the kickoff. Note the top-level issues, the intended flow, and consequential assumptions.",
            "the Brief. Note the top-level issues, the intended flow, and consequential assumptions.",
        ),
        (
            "Work with the user to make the approved direction tangible at low fidelity. Use a\nticket-owned interactive HTML wireframe where behavior matters. Validate structure,\nhierarchy, interactions, and important states before polish. Alternatives should differ\nmeaningfully, not cosmetically.",
            "Work with the user to make the approved Best Guess and Open Options tangible at low\nfidelity. Use a ticket-owned interactive HTML wireframe where behavior matters. Validate\nstructure, hierarchy, interactions, and important states before polish. Alternatives\nshould differ meaningfully, not cosmetically.",
        ),
    ),
    "panels-worker-research": (
        (
            "The sequence is **Kickoff → Research Plan → Research → Closeout → Done**.",
            "The sequence is **Brief → Research Plan → Findings → Consequences → Done**.",
        ),
        (
            "Write the substance as a ticket artifact and link it. Keep the **closeout** field to a short report: what was found, what stayed open, and where the artifact is.",
            "Write the substance as a ticket artifact and link it. Keep the **Consequences** field to a short report: what was found, what stayed open, and where the artifact is.",
        ),
    ),
}


def _rewrite_labels(definition: dict[str, Any], forward: bool) -> int:
    """Swap one label per rename on the field and on the Stage the field gates."""
    worker_type = str(definition.get("worker_type", ""))
    changed = 0
    for field_id, shipped, settled, types in RENAMES:
        if types is not None and worker_type not in types:
            continue
        expected, wanted = (shipped, settled) if forward else (settled, shipped)
        for field in definition.get("fields", []):
            if field.get("id") == field_id and field.get("label") == expected:
                field["label"] = wanted
                changed += 1
        for stage in definition.get("stages", []):
            if stage.get("gating_field") == field_id and stage.get("label") == expected:
                stage["label"] = wanted
                changed += 1
    return changed


def _apply(forward: bool) -> None:
    conn = op.get_bind()

    for worker_type, definition_json in conn.exec_driver_sql(
        "SELECT worker_type, definition_json FROM worker_types"
    ).fetchall():
        decoded: Any = json.loads(definition_json)
        if not isinstance(decoded, dict):
            continue
        if _rewrite_labels(decoded, forward) == 0:
            continue
        conn.exec_driver_sql(
            "UPDATE worker_types SET definition_json = ? WHERE worker_type = ?",
            (json.dumps(decoded, ensure_ascii=False), worker_type),
        )

    for skill_name, replacements in SKILL_REPLACEMENTS.items():
        row = conn.exec_driver_sql(
            "SELECT source_text FROM managed_skills WHERE skill_name = ?", (skill_name,)
        ).fetchone()
        if row is None:
            continue
        source_text = str(row[0])
        corrected = source_text
        for shipped, settled in replacements:
            old_text, new_text = (shipped, settled) if forward else (settled, shipped)
            corrected = corrected.replace(old_text, new_text)
        if corrected != source_text:
            conn.exec_driver_sql(
                "UPDATE managed_skills SET source_text = ? WHERE skill_name = ?",
                (corrected, skill_name),
            )


def upgrade() -> None:
    _apply(forward=True)


def downgrade() -> None:
    _apply(forward=False)
