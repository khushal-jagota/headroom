# Ticket-owned workflows

The owner approved Ticket-owned stages, structured preset guidance, and a creation editor. Preset Tickets read the usual specialist skill. Custom Tickets receive their complete guide at kickoff. Per-stage guidance delivery and active workflow replacement are outside this change.

The initial local staging base was edad376fdbacb2bf9fcacefcb4c1fe6e7c68c4ca. Before implementation, the branch advanced to fetched origin/staging 64c9264f53ae0c404aaa3d8f5b2e22e149d268d1. All implementation uses the latter base.

## Work boundaries

- Contracts/storage: shared workflow behavior and validation, Ticket snapshot, guidance renderer, migration, preset saves without live Ticket migrations.
- Backend/runtime: canonical creation API and CLI, saved workflow readers, kickoff/recovery guidance, skill audit and generated-skill mutation authority.
- Frontend: reusable structured editor, reachable creation surface, preset configuration, Ticket-owned lifecycle display and review controls.
- Coordination: documentation, independent review, verification, browser evidence, proposed shared skill edits, and the review package.

The exact interfaces are in contracts.md. Each implementer owns separate files. Contract changes require explicit coordination.

## Plan review findings and decisions

1. Custom workflows cannot require a registered specialist. Extract shared workflow behavior and validate preset profiles separately.
2. Preserve and classify existing specialist prose. Provide a conversion report for owner review. Do not silently discard unmatched text.
3. Custom steps bind orientation and shared skills only. No fabricated specialist binding.
4. The canonical conversation role carries the guide on the first admitted prompt, including manual kickoff. A conversation row does not prove delivery. Rejected sends retain the role for retry.
5. Generated specialist skills have one structured authority. Cover generic PATCH, batch skill apply, and bundled preset writes.
6. Nullable worker_type is preset provenance. The saved workflow controls Ticket behavior. Custom Tickets never inherit coding/general semantics implicitly.
7. Preserve field_approvals and historical terminal ceilings along with all other migration state.

## Verification admission

- Storage/unit proof: two Ticket snapshots remain unchanged after preset edits. Existing global registry tests do not establish snapshot independence.
- Migration proof: existing raw values, approval records, pending proposal identity, retired types, ceilings and claims survive. Ordinary fresh-database tests do not prove preservation.
- Backend integration proof: custom unfamiliar stages pass creation, proposals and approval with their own fields. Existing probe tests require a registered type.
- Prompt boundary proof: actual admitted conversation content contains the full custom guide on kickoff and recovery, without a specialist skill. Existing prompts only identify the current field.
- Generated skill proof: structured saves update rendered skills atomically and independent skill mutation routes cannot diverge.
- Frontend proof: editor order/result/instruction behavior and saved Ticket lifecycle controls. Use existing frontend harnesses.
- One browser/server proof: create a custom Ticket through the real form and inspect the resulting Ticket. This crosses form serialization, server validation, persistence and detail rendering. Reuse its host for screenshots. Backend/frontend unit tests do not prove the complete assembled path.

No live skill edits, deployment, or integration into staging occur during Implementation. Consequences uses the current repository pull-request route into staging.

## Independent implementation review

The review found three concrete gaps. The Mac executor still resolved the current preset, manual first chat omitted the custom guide, and workspace hover labels used current presets. Repairs switch executor skill requirements and workspace descriptions to snapshots, and place custom kickoff guidance in canonical conversation role materials. Focused checks and a recheck of those repairs form the final review evidence.

The navigation protocol adds saved stage and workflow labels. A schema/semantics bump rebuilds persisted snapshots and resets the epoch for the new required fields. Compact Ticket controls omit instruction bodies to retain the existing response size budget.

## Compatibility follow-up

The current schema requires a saved workflow. Existing direct-SQL fixtures now supply one explicitly. Historical migration fixtures retain their original schemas. Existing navigation ownership tests now distinguish a Ticket snapshot update from a preset edit.

An offline diagnostic maintenance case exposed runtime-only SQLite callbacks in Ticket triggers during backfill. The migration now captures and drops those triggers before backfill, then restores their exact SQL after the table rebuild. The existing migration test includes a callback absent from the migration connection. Independent review confirmed that the surrounding BEGIN IMMEDIATE transaction also rolls back trigger removal on failure.

The implementation review reports no unresolved findings. Its three original findings and the later trigger repair received focused rechecks.
