# t_bg70adpd — Add Chief external-work intake without worker state bypass

## Success

Done when the Chief has supported tooling to take a user’s report of work completed outside Panels, reuse an aligned ticket or create the right one, record what was done and why, and carry the ticket through the appropriate reconciliation so Panels reflects reality. This remains a separate Chief/planner capability and does not give ticket workers general state-jump or gate-bypass powers. Special handling for unclear claims is outside this first version.

## Accepted approach

`panels ticket` remains the actor-neutral surface for ordinary ticket operations; the backend must stop treating those requests as proof that a human acted. The Chief continues using that group normally. Add a `chief` group to the same CLI with:

- `panels chief reconcile-ticket-from-external-work <ticket-id>` — bring an existing ticket’s fields, notes, and state in line with externally completed work.
- `panels chief create-ticket-from-external-work` — create an already-populated ticket for externally completed work.

These are explicitly Chief operations because of their meaning. `panels worker` remains attributed and constrained. Use the existing ticket model and normal events. Do not add a new reconciliation object or an ambiguity/proposal system.

## Accepted plan and owner corrections

1. Correct the actor boundary test-first: ordinary ticket operations are unattributed, worker activity remains identified/constrained, and Chief activity is explicit. Remove misleading human naming without changing ordinary behavior.
2. Add tested domain/API operations for existing-ticket reconciliation and already-populated creation. Accept settled fields, existing notes/recap, and target state; validate coherent output and active-work safety; use existing events.
3. Add both commands to the existing Click CLI under `panels chief`, with file options for long Markdown and normal terse/JSON output.
4. Update `skills/panels-chief-of-staff/SKILL.md` with the complete external-work workflow: trigger, inspect existing tickets, existing-vs-new choice, preserve report/reason in notes, invoke command, read back, report id/state, and narrow boundaries.
5. In `skills/panels-worker/SKILL.md`, add only: **Never invoke `panels chief`.** Do not duplicate explanation.
6. Update `skills/panels/SKILL.md` with the three command meanings. Edit repo source skills only; `src/planner/minds/config.py::provision_planner_home_skills` symlinks them into the runtime home.
7. Add domain/API/real-server CLI coverage, live disposable-ticket smoke, independent review, and one final `./verify`.

## Standing constraints

- Ordinary `panels ticket` commands are not “human commands”; caller identity is unknown/irrelevant for those ordinary operations.
- Chief commands are separate because importing work already completed elsewhere is semantically different from ordinary ticket operations.
- Preserve existing worker proposal behavior and state gates.
- Preserve the user report and reconciliation reasoning in existing notes only; no special evidence/audit model.
- No special unclear-claim workflow in v1.
- Follow strict TDD: capture focused RED before production edits, then GREEN.
