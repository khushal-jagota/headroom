# Follow-up implementation review

Codex `gpt-5.5`, read-only sandbox, high reasoning.

## Findings and disposition

1. Built bundle looked untracked while `web/dist/index.html` referenced it — confirmed as the newly generated checked-in build artifact; final status and verifier cover the complete worktree.
2. Legacy `ProposalCard` still showed a permanent raw textarea — fixed by reusing rendered-at-rest/raw-source `InlineEdit`; a browser assertion failed before the fix and passed after it.
3. Normal field notes and result proposals were not covered — added to the normal-state Playwright fixture.
4. Two editor assertions searched inside a textarea — corrected to assert zero preview nodes across the full editable container.
5. Approval Escape reset was unproved — added discard/reopen/final-approval coverage.
6. HTML sandbox attributes were inferred only — now asserted directly for embedded and full routes, with parent-isolation coverage retained.
7. Self-link behavior lacked browser proof — now asserts the self-link becomes exactly one compact card with no recursive heading.
8. Source persistence used `fill()` — changed to a real keyboard append against literal Markdown source.
9. The edit action could overlap content — rendered bodies now reserve its width.

## Final follow-up

Codex re-reviewed those findings and returned `NO VIOLATIONS`.
