# PROGRESS

## Current work cycle (2026-07-24): supported Sprint Item deletion (`t_2ncx8seu`)

Implementation is isolated on `ticket/t_2ncx8seu-sprint-item-delete` from current
`staging`. Added the canonical transactional `sprints.data.delete_item` writer, the
direct-user `DELETE /api/items/{item_id}` route, and confirmed
`panels sprint item delete <item-id> --yes`. Deletion refuses ordered child Ticket ids
with the accepted validation envelope, prunes item-owned/referring event history,
removes blocking links with `link_removed` events on surviving Tickets, and leaves one
`sprint_item_deleted` audit/invalidation event plus affected-resource metadata. The
frontend event catalogue classifies the deletion as a Sprint Item event.

Focused gates passed: `tests/unit/test_sprint_item_delete.py` plus
`tests/unit/test_authctx_routes.py` (22 passed); `web/tests/resource-catalogue.test.mjs`
(all assertions passed); `tests/unit/test_frontend_event_mapping.py` plus
`tests/e2e/test_sprint_item_delete_e2e.py` (2 passed); targeted Ruff and mypy checks
passed. Independent diff review found no unresolved violations and additionally ran the
full web npm test green. The Implementation package is ready for proposal. Canonical
`./verify`, current-`staging` integration, push, and cleanup remain reserved for
Closeout. Blockers: none.

## Current work cycle (2026-07-24): Workspace left panel regroup (`t_ava8za6k`)

Implementation is isolated on `ticket/t_ava8za6k-status-buckets` from current `staging`
(`2fbf12bb`). The user-approved design regroups the Workspace left panel by ticket
attention-state into eleven status buckets (Errored, Needs you, Kickoff, Stopped, Taken over,
Paired, Agent working, Needs approval, Closing out, Blocked, Done), replaces the five-state
workspace dot with two row signals (agent working spinner; reply dot accent-unseen/grey-seen),
and widens the board to all non-dropped tickets.

Landed so far: DB v37 adds `has_completed_response` to `ticket_conversation_projections`
(backfilled); the projection remembers a completed reply across acknowledgement;
`workspace_dot.py` replaced by `workspace_signals.py` with `WorkspaceSignals` contracts; board
cards expose `agent_working` + `agent_reply_state` and drop `workspace_dot_state`; `/api/board`
drops day scoping; `BoardRoute.svelte` rebuilt around collapsible boxed status buckets (empty
hidden, Blocked/Done closed, bare title+mark rows, recency sort, kickoff-supersedes-approval);
CSS bucket idiom + `stage-mark--reply-seen` variant. Frontend builds green. Dogfooded live on a
migrated copy of the production DB (170 cards): buckets render in canonical order, empty buckets
absent, kickoff claims kickoff-stage approvals, and the unseen->open->seen dot flip works
end-to-end through acknowledge -> event -> board refetch.

Test suite fully ported to the bucket/signals contract (delegated agent; 1435 unit tests, all
touched e2e files green). The port surfaced a real migration bug — the v29 projection-table
validator rejected the 6-column v37 shape, breaking every fresh DB — fixed by name-keyed shape
validation permitting exactly the one later column. Independent diff review: no blocking
findings; its two should-fixes applied (v37 migration wrapped atomic; dead now/cfg parameters
removed from board_view and /api/board). test_flows_a e22 ported: board is WS-live on create,
day membership no longer moves cards. Canonical full ./verify on the settled tree is the final
gate; staging advanced during review, so Closeout must merge current staging and re-verify.
Blockers: none.
