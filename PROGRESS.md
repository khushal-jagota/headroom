# PROGRESS

## Current work cycle (2026-07-24): Bare Panels CLI for agent shells (`t_80u04jna`)

Implementation is isolated on `ticket/t_80u04jna-bare-panels-cli` from current `staging`
(`ce32d11`). The approved plan provisions a host-level `/usr/local/bin/panels` wrapper
that enters the canonical `/opt/panels/current/bin/panels-launcher`, while deployment
and rollback remain responsible only for switching the `current` pointer.

The wrapper asset, Linux setup install contract, static and hermetic deployment-asset
tests, and environment documentation are implemented. The hermetic test substitutes the
one canonical target in a copied wrapper, exercises a scrubbed agent-like PATH outside
the checkout, and proves exact arguments and a simulated `current` switch across two
releases. The focused deployment-asset module passes (13 tests), and the already-installed
live wrapper passed `--help` plus a read-only Ticket query from `/tmp` with a scrubbed
agent-like environment. Current hypothesis: this is the complete approved implementation
surface. Next step: commit for independent implementation review. Blockers: none.

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
