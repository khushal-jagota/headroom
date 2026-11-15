# Ticket workflow contract

Approved design uses nullable preset provenance. There is no registered custom Worker type, fake specialist, or implicit coding/general behavior.

## Python

`StageDefinition` gains `instructions: str = ""` (keyword-only).
`WorkflowDefinition` in worker_types/contracts.py owns `stages`, `fields`, keyword-only `label: str = "Custom workflow"`, `guidance: str = ""`, and all current workflow behavior methods extracted from WorkerTypeDefinition without duplication.
`WorkerTypeDefinition(WorkflowDefinition)` adds the existing required `worker_type: str` and `worker_profile: WorkerProfile`.
`TicketWorkflowDefinition(WorkflowDefinition)` adds keyword-only `specialist_skill: str | None = None`.
`Ticket.worker_type: str | None` is source preset identity only. `Ticket.workflow: TicketWorkflowDefinition` is the authority. A preset copy has its specialist skill string. A custom workflow has null specialist_skill, including an adapted preset.

Helpers in worker_types/store.py:
- `workflow_from_json(raw: str) -> TicketWorkflowDefinition`: strict saved snapshot decoder.
- `workflow_to_json(definition: TicketWorkflowDefinition) -> str`
- `workflow_from_preset(definition: WorkerTypeDefinition) -> TicketWorkflowDefinition`
- `custom_workflow_from_dict(raw: object) -> TicketWorkflowDefinition`: validates input (no specialist_skill input).

`validate_workflow(definition: WorkflowDefinition)` validates behavior only. `validate_definition` adds preset profile/toolset/known-skill checks. Pure custom creation has no specialist skill validation.
`workflow_manifest(definition: WorkflowDefinition, *, worker_type: str | None = None, specialist_skill: str | None = None)` supplies the existing lifecycle shape.
Renderer in worker_types/guidance.py: `render_workflow_guide(definition: WorkflowDefinition) -> str`, plus preset skill frontmatter assembly. Render the same guide structure for preset and custom workflows.

## Workflow JSON

Input custom workflow:
```
{"label":"Investigation","guidance":"Rules across this workflow.","stages":[{"id":"needs_brief","label":"Brief","gating_field":"brief","is_terminal":false,"ownership_mode":"worker","instructions":"Define the job."},{"id":"needs_consequences","label":"Consequences","gating_field":"consequences","is_terminal":false,"ownership_mode":"worker","instructions":"Record the outcome."},{"id":"done","label":"Done","gating_field":null,"is_terminal":true,"ownership_mode":null,"instructions":""}],"fields":[{"id":"brief","label":"Brief"},{"id":"consequences","label":"Consequences"}]}
```
Stage/field rules stay current: optional Brief paired first, exactly one terminal done, every nonterminal stage gates one declared field, every field gated exactly once, consequences required.
Saved Ticket workflow adds `"specialist_skill": "panels-worker-coding"` for an unedited preset or null for custom. No backend/model is stored in workflow; Ticket launch configuration already owns them.

## Creation POST /api/tickets

Existing preset payload works unchanged: `{"worker_type":"coding", ...}`.
Custom payload: `{"worker_type":null,"workflow":<custom workflow>,"employee_backend":"codex","employee_launch_model":"...","employee_launch_reasoning_effort":"medium",...}`.
Adapted preset: `{"worker_type":"coding","workflow":<custom workflow>,...}`. It inherits that selected preset's launch choices unless explicitly overridden. Sending workflow always selects custom guidance delivery.
Pure custom must state employee_backend and employee_launch_model; reasoning effort follows existing optional/null rules. No backend/model default is invented. With no preset, macOS requirement is not inherited; existing locale/backend validation applies.
Omitting both worker_type and workflow fails. Null or omitted worker_type with custom workflow both mean no selected preset.
No workflow replacement appears in PATCH Ticket.

## Ticket detail and workspace wire

Add `workflow` to Ticket detail and workspace Ticket detail. This is the workflow manifest, not the raw storage JSON:
```
{"worker_type":null,"label":"Investigation","stages":[...stage objects including instructions...],"fields":[...],"guidance":"...","advance":{"needs_brief":"needs_consequences","needs_consequences":"done"},"ceiling_range":["needs_brief","needs_consequences","done"],"default_ceiling":"needs_brief","worker_profile_id":null}
```
Preset `worker_type` is provenance string and `worker_profile_id` is saved specialist reference. Do not depend on current registry manifest for a Ticket. Additional launch-default keys remain only on preset manifests.
Compact workspace responses omit guide bodies and stage instruction bodies from their workflow controls. Full Ticket detail and the `workflow` readable part retain the complete guide. Expose Ticket custom guidance through the `workflow` readable part. Custom guide must be recoverable after context loss.

## Preset read/save

GET /api/worker-types/{id} keeps its current raw record fields `worker_type,label,stages,fields,profile` and adds top-level `guidance`; each stage gains `instructions`.
POST /api/worker-types keeps current bundled record shape and authority. Its structured record is the editable source; the specialist skill is rendered from guidance and stage instructions in that same save. No independent specialist Markdown body is a second editable source.
Preset manifests include guidance and stage instructions for creation editor. Skill metadata description remains preservable separately in managed skill row/frontmatter.

## Persistence/migration

Add tickets.workflow_json and make tickets.worker_type nullable. Backfill snapshots from existing types before enforcing snapshot availability. Preserve all existing Ticket columns exactly, including raw field values, field approvals, pending proposal identity, claims, position, and historical terminal ceilings. No runtime registry fallback for absent/malformed saved workflow after migration.
Preset saves no longer migrate existing Tickets. They return an empty migration receipt for API compatibility.
Existing specialist skill conversion must preserve all prose and provide owner review evidence. No live skill write is part of implementation work.

## Delivery

Snapshot specialist_skill nonnull: normal preset skill path, even if source preset later changes.
Snapshot specialist_skill null: the full custom guide enters the canonical conversation role materials. The conversation core delivers that role on its first admitted prompt, including a manual kickoff. A failed first send retains that initial role for retry. Explicit context recovery restores the custom guide. Subsequent ordinary stage messages carry only normal step instructions.
Custom skills audit binds orientation/shared roles only; never create a fake third specialist binding.
