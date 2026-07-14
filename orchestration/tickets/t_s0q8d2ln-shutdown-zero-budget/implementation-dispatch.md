# Implementation dispatch — t_s0q8d2ln

Implement the reviewed `implementation-plan.md` against `contract.md` and
`plan-review-disposition.md`. Read both prior Codex review artifacts and the final `NO VIOLATIONS`
review before editing.

## Required sequence

1. Add the real-process regression first and run its exact command to meaningful RED.
2. Complete each lower seam as one RED → GREEN vertical slice in plan order: Employee runner and
   concrete deadline-aware interrupt, routed gateways, then session manager.
3. Re-run the real-process regression to GREEN.
4. Update only the two allowed live system docs.
5. Run the focused completion commands from the plan and `git diff --check`.
6. Write `implementation-report.md` in this ticket directory with the exact RED/GREEN commands,
   concise full outputs, design choices, and final file list.

## Boundaries

- Modify only files listed under the ticket contract's File ownership section.
- Do not modify public contract/type files, server/loop composition, config, migrations, frontend,
  `PROGRESS.md`, or `decisions.md`.
- Do not add sleeps, a grace floor, a new config value, duplicate session state, or a new lifecycle
  owner.
- Do not treat Panels Chat as worker context. Its running worker-turn session binding is used only to
  identify a currently owned session for shutdown control.
- Do not run `./verify`, commit, stage, merge, or clean the worktree.

Stop and report if a reviewed plan step cannot be implemented inside these boundaries.
