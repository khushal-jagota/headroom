# Single current Ticket proposal implementation

Implemented in `codex/panels-single-proposal` against root contract `5c9a1ea7` and reviewed plan 09 / review 11. This worktree is isolated from the Outcome implementation. Independent implementation review and the combined final `./verify` remain root-owned; neither is claimed complete here.

## Result

A Ticket stores sparse saved strings, one optional current proposal, and a read-only Historical record. The complete six-member Decision controls those three members and Stage/scope. Resolution persists the whole prospective state. Empty seeded Kickoff strings remain valid stored content; worker submission and text-edit validation retain their existing nonempty checks.

Removed nested field slots, arbitrary-field proposals, nongating acceptance, the arbitrary Stage route/writer, proposal-presence scanners, and discarded event construction. External-work reconciliation produces one complete Decision, preserving its exact-prefix rules and atomic recap write. Proposal parking follows the resulting pending proposal directly.

Workers submit current work through `file_current_proposal_with_recap`; the gate is inferred during its transaction. Approval and pending edits require the supplied stale-request field to match the current proposal and current gate. Saved-value edits have a separate route and retain direct-actor and passed-field checks. The UI shows one approval, with separate saved-value and pending-proposal editors. CLI records list declared saved fields and separate `proposal` / `archive` parts; worker guidance names those parts explicitly.

Drop appends its draft as **Unapproved proposal** before clearing it. Archive, Stage, status and blocker release share the transaction. Historical record is collapsed, readable, and cannot be edited or approved. Search and copy include its text. Ordinary draft replacement does not invent an event journal.

## Migration and handoff boundary

`single_ticket_proposal` follows frozen parent `ticket_guidance`. It freezes all 13 source Worker definitions, reads original raw field JSON, and validates all Tickets before DDL or row writes. Current saved value and current proposal survive together. Off-stage/terminal proposals remain explicitly unapproved history; unknown field values remain previously stored text. Unknown field/proposal metadata remains recursively equal JSON in safely fenced metadata sections. Multiline prose, whitespace, backticks and Unicode are preserved as readable text. Empty structural slots create no history.

Unknown Worker/stage, corrupt field/proposal scalar shapes, or awaiting-approval without a current proposal stop migration before writes. No status repair or compatibility decoder is introduced. The synthetic proof verifies late-write rollback of rows, schema and Alembic revision, repeated-startup no-op, foreign keys, empty Kickoff and current-value/proposal coexistence. Historical migration tests that assert the old slot representation end at `ticket_guidance`; the new migration owns the subsequent representation proof.

Revision delivery still validates and snapshots the complete pending proposal before sending, acknowledges only after successful delivery, and compares that snapshot before clearing. The strengthened existing supervisor rejection test covers refusal, delivery, and a draft replaced during actual delivery. It also verifies exact-child admission and that revision guidance plus pending worker context reaches the backend prompt. A refused send preserves context and proposal; a changed proposal remains pending after the successful send.

No contract files were modified by this implementation. Protected conversation, conversation-start, message-delivery, worker-context and agent-backend source paths remain byte-identical to `5c9a1ea7`, as do frontend conversation modules and Employee configuration. Only Ticket-shaped test fixtures were adapted around those boundaries. The separate claim-release revision repair is not included.

## Focused evidence

Passed:

- All Python source/tests: strict mypy, 356 modules; Ruff lint. Changed-module mypy also passed after the final proof additions.
- New raw migration proof: 5 cases, including all-row validation and late-write rollback.
- Existing pending/saved edit and generic Worker storage gates: 19 cases.
- Proposal/Drop engine, bounded search/copy, migration, readiness loop, Ticket conversation routes, supervisor authority, type-driven ingress and chief external-work CLI: 79 cases passed together. The final revision-delivery expansion then passed its three focused cases.
- DB bootstrap and adoption, scheduled creation, historical guidance/review migrations, Worker persistence/stage contracts and parameterized Worker execution passed their affected focused runs.
- Fifteen additional retained historical migration cases passed, including conversation table adoption and Sprint placement. No protected migration implementation changed.
- Existing Ticket/Review pending-edit E2E passed against a fresh production build. This reuses the existing one-case boundary proof; no E2E was added.
- Frontend Svelte checking, Vitest, Ticket guidance/history browser proof, voice browser proof and production build passed. Vitest's combined typecheck count is not a runtime inventory; root owns the comparable final collection.

The first pending-edit E2E used stale generated assets and failed to find the new approval editor; rebuilding resolved it. Historical bootstrap fixtures were corrected to use valid source stages and a real pending Kickoff for awaiting approval. The new search fixture was excluded from its unrelated title-filter assertion. There are no outstanding failures in the named focused gates.

A whole-root collection-only command encounters the repository's existing non-top-level integration `pytest_plugins` restriction; the canonical separate test groups remain the inventory mechanism. No harness restructuring was made for that command. Final case inventory, all remaining browser cases, independent combined review, live-data rehearsal and canonical `./verify` are unrun here and remain with root. Generated `web/dist` changes were excluded and restored after the build proof; root owns final combined assets.

## Integration

Frontend implementation is `6759e425`; its bounded contract-test adaptation is `fc2de211`. Backend, migration, fixture adaptations and this report are the subsequent handoff commit. Root should integrate this branch serially, then apply its claim-revision repair and Outcome work, run the independent combined review and final canonical gate, and update this evidence with any findings. The migration revision and parent above are fixed so the Outcome migration can follow it deliberately.
